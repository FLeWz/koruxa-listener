import math
import re
import sqlite3

from core.store import now
from services.market import FIXED_PRICES, item_name

KEY_RE = re.compile(r"^[a-z0-9_]+$")
MAX_NAME = 64


def whole_number(value, label, minimum=0):
    """Accept an int (or an integral float) of at least `minimum`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")

    if not math.isfinite(value) or value != int(value):
        raise ValueError(f"{label} must be a whole number")

    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")

    return int(value)


def item_key(value):
    if not isinstance(value, str) or not KEY_RE.match(value):
        raise ValueError("Invalid item key")

    return value


class Tracker:
    """Potion crafting requests: per player, per potion, with seeds and monster drops.

    Seeds are received in one total and planted in segments. The yield of each
    segment is random and is filled in once it is known.
    """

    def __init__(self, store, market):
        self.store = store
        self.market = market

    # ---- potions -----------------------------------------------------

    def potions(self, skill):
        """Potions from the last market refresh, ingredients without the fixed-price vial."""
        potions = []

        for recipe in self.market.view(skill)["recipes"]:
            ingredients = [
                {"item_key": i["item_key"], "name": i["name"], "quantity": i["quantity"]}
                for i in recipe["ingredients"]
                if i["item_key"] not in FIXED_PRICES
            ]

            potions.append({"key": recipe["reward_key"], "label": recipe["label"], "ingredients": ingredients})

        return potions

    # ---- view --------------------------------------------------------

    def view(self, skill):
        potions = self.potions(skill)
        by_key = {p["key"]: p for p in potions}

        with self.store.connect() as db:
            players = db.execute("SELECT id, name FROM players ORDER BY name").fetchall()
            orders = db.execute("SELECT * FROM orders ORDER BY id").fetchall()
            seeds = db.execute("SELECT * FROM seeds ORDER BY id").fetchall()
            plantings = db.execute("SELECT * FROM plantings ORDER BY id").fetchall()
            drops = db.execute("SELECT * FROM drops ORDER BY id").fetchall()
            gives = db.execute("SELECT * FROM potions_given ORDER BY id").fetchall()

        gives_by_order = {}

        for g in gives:
            gives_by_order.setdefault(g["order_id"], []).append({"id": g["id"], "quantity": g["quantity"]})

        plantings_by_seed = {}

        for p in plantings:
            plantings_by_seed.setdefault(p["seed_id"], []).append(
                {"id": p["id"], "planted": p["planted"], "yield": p["harvested"]}
            )

        seeds_by_order = {}

        for s in seeds:
            rows = plantings_by_seed.get(s["id"], [])
            planted = sum(r["planted"] for r in rows)

            seeds_by_order.setdefault(s["order_id"], []).append(
                {
                    "id": s["id"],
                    "item_key": s["item_key"],
                    "name": item_name(s["item_key"]),
                    "total_received": s["total_received"],
                    "planted": planted,
                    "left": s["total_received"] - planted,
                    "yield": sum(r["yield"] or 0 for r in rows),
                    "pending": sum(1 for r in rows if r["yield"] is None),
                    "plantings": rows,
                }
            )

        drops_by_order = {}

        for d in drops:
            drops_by_order.setdefault(d["order_id"], []).append(
                {
                    "id": d["id"],
                    "item_key": d["item_key"],
                    "name": item_name(d["item_key"]),
                    "quantity": d["quantity"],
                    "source": d["source"],
                    "price": d["price"],
                }
            )

        orders_by_player = {}

        for o in orders:
            potion = by_key.get(o["potion_key"])
            order_seeds = seeds_by_order.get(o["id"], [])
            order_drops = drops_by_order.get(o["id"], [])

            # Per ingredient: how many are needed and how many we have (herb yield + drops).
            ingredients = []

            for ingredient in potion["ingredients"] if potion else []:
                key = ingredient["item_key"]

                from_seeds = sum(s["yield"] for s in order_seeds if s["item_key"] == key)
                from_drops = sum(d["quantity"] for d in order_drops if d["item_key"] == key)

                have = from_seeds + from_drops

                ingredients.append(
                    {
                        **ingredient,
                        # Unlimited requests (quantity 0) have no fixed need.
                        "needed": ingredient["quantity"] * o["quantity"] if o["quantity"] else None,
                        "from_seeds": from_seeds,
                        "from_drops": from_drops,
                        "have": have,
                        "potions": have // ingredient["quantity"] if ingredient["quantity"] else None,
                    }
                )

            # Output follows from the scarcest ingredient.
            counts = [i["potions"] for i in ingredients if i["potions"] is not None]
            possible = min(counts) if counts else None

            order_gives = gives_by_order.get(o["id"], [])
            given = sum(g["quantity"] for g in order_gives)

            if o["quantity"]:
                remaining = o["quantity"] - given
            else:
                remaining = possible - given if possible is not None else None

            orders_by_player.setdefault(o["player_id"], []).append(
                {
                    "id": o["id"],
                    "potion_key": o["potion_key"],
                    "potion": potion["label"] if potion else item_name(o["potion_key"]),
                    "quantity": o["quantity"],
                    "given": given,
                    "gives": order_gives,
                    "possible": possible,
                    "remaining": remaining,
                    "ingredients": ingredients,
                    "seeds": order_seeds,
                    "drops": order_drops,
                }
            )

        return {
            "skill": skill,
            "potions": potions,
            "players": [{"id": p["id"], "name": p["name"], "orders": orders_by_player.get(p["id"], [])} for p in players],
        }

    # ---- actions -----------------------------------------------------

    def handle(self, action, body, skill):
        handler = getattr(self, "do_" + action, None) if re.match(r"^[a-z_]+$", action) else None

        if handler is None:
            raise LookupError("Unknown action")

        with self.store.connect() as db:
            return handler(db, body, skill) or {}

    @staticmethod
    def _require(db, table, row_id, label):
        row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()

        if row is None:
            raise ValueError(f"{label} not found")

        return row

    @staticmethod
    def _delete(db, table, body, label):
        row_id = whole_number(body.get("id"), "Id", 1)

        if db.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,)).rowcount == 0:
            raise ValueError(f"{label} not found")

    def do_add_player(self, db, body, skill):
        name = body.get("name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Player name is required")

        name = " ".join(name.split())

        if len(name) > MAX_NAME:
            raise ValueError("Player name is too long")

        try:
            cursor = db.execute("INSERT INTO players (name, created_at) VALUES (?, ?)", (name, now()))
        except sqlite3.IntegrityError:
            raise ValueError("Player already exists")

        return {"id": cursor.lastrowid}

    def do_delete_player(self, db, body, skill):
        self._delete(db, "players", body, "Player")

    def do_add_order(self, db, body, skill):
        player_id = whole_number(body.get("player_id"), "Player", 1)
        quantity = whole_number(body.get("quantity", 1), "Quantity", 0)  # 0 = unlimited
        potion_key = item_key(body.get("potion_key"))

        self._require(db, "players", player_id, "Player")

        if potion_key not in {p["key"] for p in self.potions(skill)}:
            raise ValueError("Unknown potion. Refresh market data first.")

        cursor = db.execute(
            "INSERT INTO orders (player_id, skill, potion_key, quantity, created_at) VALUES (?, ?, ?, ?, ?)",
            (player_id, skill, potion_key, quantity, now()),
        )

        return {"id": cursor.lastrowid}

    def do_add_given(self, db, body, skill):
        order = self._require(db, "orders", whole_number(body.get("order_id"), "Request", 1), "Request")
        quantity = whole_number(body.get("quantity"), "Potions given", 1)

        total = db.execute("SELECT COALESCE(SUM(quantity), 0) FROM potions_given WHERE order_id = ?", (order["id"],)).fetchone()[0]

        if order["quantity"] and total + quantity > order["quantity"]:
            raise ValueError("Cannot give more potions than requested")

        cursor = db.execute(
            "INSERT INTO potions_given (order_id, quantity, created_at) VALUES (?, ?, ?)",
            (order["id"], quantity, now()),
        )

        return {"id": cursor.lastrowid}

    def do_delete_order(self, db, body, skill):
        self._delete(db, "orders", body, "Request")

    def do_add_seed(self, db, body, skill):
        order_id = whole_number(body.get("order_id"), "Request", 1)
        total = whole_number(body.get("total_received"), "Total received", 1)
        key = item_key(body.get("item_key"))

        self._require(db, "orders", order_id, "Request")

        cursor = db.execute(
            "INSERT INTO seeds (order_id, item_key, total_received, created_at) VALUES (?, ?, ?, ?)",
            (order_id, key, total, now()),
        )

        return {"id": cursor.lastrowid}

    def do_set_seed_total(self, db, body, skill):
        seed = self._require(db, "seeds", whole_number(body.get("id"), "Id", 1), "Seeds")
        total = whole_number(body.get("total_received"), "Total received", 1)

        if total < self._planted(db, seed["id"]):
            raise ValueError("Total received is less than already planted")

        db.execute("UPDATE seeds SET total_received = ? WHERE id = ?", (total, seed["id"]))

    def do_delete_seed(self, db, body, skill):
        self._delete(db, "seeds", body, "Seeds")

    @staticmethod
    def _planted(db, seed_id, exclude=None):
        return db.execute(
            "SELECT COALESCE(SUM(planted), 0) FROM plantings WHERE seed_id = ? AND id IS NOT ?",
            (seed_id, exclude),
        ).fetchone()[0]

    def _check_planted(self, db, seed, planted, exclude=None):
        if self._planted(db, seed["id"], exclude) + planted > seed["total_received"]:
            raise ValueError("Planted more seeds than received")

    def do_add_planting(self, db, body, skill):
        seed = self._require(db, "seeds", whole_number(body.get("seed_id"), "Seeds", 1), "Seeds")
        planted = whole_number(body.get("planted"), "Planted", 1)
        harvested = body.get("yield")

        if harvested is not None:
            harvested = whole_number(harvested, "Yield")

        self._check_planted(db, seed, planted)

        cursor = db.execute(
            "INSERT INTO plantings (seed_id, planted, harvested, created_at) VALUES (?, ?, ?, ?)",
            (seed["id"], planted, harvested, now()),
        )

        return {"id": cursor.lastrowid}

    def do_set_planting(self, db, body, skill):
        planting = self._require(db, "plantings", whole_number(body.get("id"), "Id", 1), "Planting")
        planted = planting["planted"]
        harvested = planting["harvested"]

        if "planted" in body:
            planted = whole_number(body["planted"], "Planted", 1)

            seed = self._require(db, "seeds", planting["seed_id"], "Seeds")
            self._check_planted(db, seed, planted, exclude=planting["id"])

        if "yield" in body:
            harvested = None if body["yield"] is None else whole_number(body["yield"], "Yield")

        db.execute("UPDATE plantings SET planted = ?, harvested = ? WHERE id = ?", (planted, harvested, planting["id"]))

    def do_delete_planting(self, db, body, skill):
        self._delete(db, "plantings", body, "Planting")

    def do_add_drop(self, db, body, skill):
        order_id = whole_number(body.get("order_id"), "Request", 1)
        quantity = whole_number(body.get("quantity"), "Quantity", 1)
        key = item_key(body.get("item_key"))
        source = body.get("source")
        price = body.get("price")

        if source not in ("given", "bought"):
            raise ValueError("Source must be given or bought")

        if price is not None:
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0:
                raise ValueError("Price must be a non-negative number")

        self._require(db, "orders", order_id, "Request")

        cursor = db.execute(
            "INSERT INTO drops (order_id, item_key, quantity, source, price, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, key, quantity, source, price, now()),
        )

        return {"id": cursor.lastrowid}

    def do_delete_drop(self, db, body, skill):
        self._delete(db, "drops", body, "Drop")
