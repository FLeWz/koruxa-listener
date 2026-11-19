import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("Store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Market helper
CREATE TABLE IF NOT EXISTS skills (
    skill      TEXT PRIMARY KEY,
    updated_at TEXT,
    recipes    TEXT NOT NULL            -- JSON list of recipes
);

CREATE TABLE IF NOT EXISTS market_prices (
    skill    TEXT NOT NULL,
    item_key TEXT NOT NULL,
    price    REAL,                      -- NULL: not on the market
    PRIMARY KEY (skill, item_key)
);

CREATE TABLE IF NOT EXISTS manual_prices (
    item_key TEXT PRIMARY KEY,
    price    REAL NOT NULL
);

-- Potion crafting tracker
CREATE TABLE IF NOT EXISTS players (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    skill      TEXT NOT NULL,
    potion_key TEXT NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 1,   -- 0: unlimited, output follows from the ingredients
    created_at TEXT NOT NULL
);

-- Potions handed to the player, one row per hand-over. They sum up per order.
CREATE TABLE IF NOT EXISTS potions_given (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    quantity   INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- Seeds received for an order. Planted in segments, see plantings.
CREATE TABLE IF NOT EXISTS seeds (
    id             INTEGER PRIMARY KEY,
    order_id       INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_key       TEXT NOT NULL,       -- ingredient (herb) the seeds grow
    total_received INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plantings (
    id         INTEGER PRIMARY KEY,
    seed_id    INTEGER NOT NULL REFERENCES seeds(id) ON DELETE CASCADE,
    planted    INTEGER NOT NULL,
    harvested  INTEGER,                 -- NULL until the yield is known
    created_at TEXT NOT NULL
);

-- Monster drop ingredients given by the player or bought by us.
CREATE TABLE IF NOT EXISTS drops (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_key   TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    source     TEXT NOT NULL CHECK (source IN ('given', 'bought')),
    price      REAL,                    -- unit price paid, for bought drops
    created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """SQLite store. One short-lived connection per operation, safe across threads."""

    def __init__(self, db_path, legacy_json=None):
        self.path = db_path

        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self.connect() as db:
            db.executescript(SCHEMA)

            # An earlier version kept one running total in orders.given. Keep it as the first entry.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}

            if "given" in columns:
                db.execute(
                    "INSERT INTO potions_given (order_id, quantity, created_at) "
                    "SELECT id, given, created_at FROM orders WHERE given > 0"
                )
                db.execute("ALTER TABLE orders DROP COLUMN given")

        if legacy_json is not None:
            self._migrate_json(legacy_json)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")

        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    # ---- JSON migration ----------------------------------------------

    def _migrate_json(self, path):
        """Copy the old JSON store into SQLite once. The JSON file is left untouched."""
        with self.connect() as db:
            done = db.execute("SELECT 1 FROM meta WHERE key = 'json_migrated'").fetchone()

            if done or not path.exists():
                return

            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as error:
                logger.error("Cannot migrate %s: %s", path, error)
                return

            if not isinstance(data, dict):
                logger.error("Cannot migrate %s: unexpected layout", path)
                return

            skills = data.get("skills")
            manual = data.get("manual")

            skills = skills if isinstance(skills, dict) else {}
            manual = manual if isinstance(manual, dict) else {}

            for skill, entry in skills.items():
                if not isinstance(entry, dict):
                    continue

                market = entry.get("market")
                market = market if isinstance(market, dict) else {}

                db.execute(
                    "INSERT OR REPLACE INTO skills (skill, updated_at, recipes) VALUES (?, ?, ?)",
                    (skill, entry.get("updated_at"), json.dumps(entry.get("recipes") or [])),
                )

                for key, price in market.items():
                    db.execute(
                        "INSERT OR REPLACE INTO market_prices (skill, item_key, price) VALUES (?, ?, ?)",
                        (skill, key, price),
                    )

            for key, price in manual.items():
                if isinstance(price, (int, float)) and not isinstance(price, bool):
                    db.execute(
                        "INSERT OR REPLACE INTO manual_prices (item_key, price) VALUES (?, ?)",
                        (key, price),
                    )

            db.execute(
                "INSERT INTO meta (key, value) VALUES ('json_migrated', ?)",
                (now(),),
            )

            logger.info("Migrated %s into %s (%d skill(s), %d manual price(s))", path, self.path, len(skills), len(manual))

    # ---- market helper -----------------------------------------------

    def load_skill(self, skill):
        """Return {updated_at, recipes, market} or None when the skill was never refreshed."""
        with self.connect() as db:
            row = db.execute("SELECT updated_at, recipes FROM skills WHERE skill = ?", (skill,)).fetchone()

            if row is None:
                return None

            prices = db.execute("SELECT item_key, price FROM market_prices WHERE skill = ?", (skill,)).fetchall()

        return {
            "updated_at": row["updated_at"],
            "recipes": json.loads(row["recipes"]),
            "market": {p["item_key"]: p["price"] for p in prices},
        }

    def save_skill(self, skill, recipes, market):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO skills (skill, updated_at, recipes) VALUES (?, ?, ?)",
                (skill, now(), json.dumps(recipes)),
            )
            db.execute("DELETE FROM market_prices WHERE skill = ?", (skill,))
            db.executemany(
                "INSERT INTO market_prices (skill, item_key, price) VALUES (?, ?, ?)",
                [(skill, key, price) for key, price in market.items()],
            )

    def manual_prices(self):
        with self.connect() as db:
            return {r["item_key"]: r["price"] for r in db.execute("SELECT item_key, price FROM manual_prices")}

    def set_manual_price(self, item_key, price):
        with self.connect() as db:
            if price is None:
                db.execute("DELETE FROM manual_prices WHERE item_key = ?", (item_key,))
            else:
                db.execute(
                    "INSERT OR REPLACE INTO manual_prices (item_key, price) VALUES (?, ?)",
                    (item_key, price),
                )
