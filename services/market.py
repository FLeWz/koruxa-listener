import json
import math
import random
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from core.base import BaseService
from core.browser import BrowserService

# Items that are not sold on the market.
FIXED_PRICES = {"vial_of_water": 50}

# Random pause (seconds) between API requests, to stay polite to the server.
REQUEST_DELAY = (10, 20)

KEY_RE = re.compile(r"^[a-z0-9_]+$")


def item_name(key):
    return key.replace("_", " ").title()


class MarketService(BaseService):
    """Crafting cost helper: recipes from the skill API, prices from the marketplace.

    Store layout (JSON):
        skills: {skill: {updated_at, recipes: [...], market: {item_key: price|null}}}
        manual: {item_key: price}   user-entered prices, override everything
    """

    def __init__(self, config, cache):
        super().__init__(config, cache)

        self.path = config.market_file
        self.browser_service = BrowserService(config, cache)

        self.store_lock = threading.Lock()
        self.refresh_lock = threading.Lock()

    # ---- store -------------------------------------------------------

    def _load(self):
        data = {}

        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

        if not isinstance(data, dict):
            data = {}

        if not isinstance(data.get("skills"), dict):
            data["skills"] = {}

        if not isinstance(data.get("manual"), dict):
            data["manual"] = {}

        return data

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        temp_file = self.path.with_suffix(self.path.suffix + ".tmp")

        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        temp_file.replace(self.path)

    # ---- fetching ----------------------------------------------------

    def _pause(self, step, total, item_key):
        delay = random.uniform(*REQUEST_DELAY)
        self.logger.info("[%d/%d] Waiting %.1fs before looking up %s", step, total, delay, item_key)
        time.sleep(delay)

    def _lookup_price(self, page, item_key):
        search = quote(item_key.replace("_", " "))
        url = f"/api/marketplace/grouped?sort=price_asc&page=1&per_page=12&search={search}"

        data = self.browser_service.fetch_authenticated(page, url, "Marketplace")

        for group in data.get("groups") or []:
            if group.get("base_key") == item_key:
                return group.get("min_price")

        return None

    @staticmethod
    def _parse_recipes(data):
        recipes = []

        for action in data.get("actions") or []:
            ingredients = []

            for ingredient in action.get("ingredients") or []:
                key = ingredient.get("item_key")
                quantity = ingredient.get("quantity")

                if key and isinstance(quantity, (int, float)):
                    ingredients.append({"item_key": key, "quantity": quantity})

            calc = action.get("calc") or {}

            if ingredients:
                recipes.append(
                    {
                        "key": action.get("key"),
                        "label": action.get("label") or action.get("key"),
                        "level": action.get("min_level"),
                        "cycle_ms": calc.get("duration_ms") or action.get("duration_ms"),
                        "yield_chance": calc.get("yield_chance") or 0,
                        "amount": calc.get("base_amount") or action.get("amount") or 1,
                        "reward_key": action.get("reward_item_key") or action.get("key"),
                        "ingredients": ingredients,
                    }
                )

        return recipes

    def refresh(self, skill):
        with self.refresh_lock:
            self.browser_service.ensure_running()

            with sync_playwright() as playwright:
                _, page = self.browser_service.connect(playwright)

                data = self.browser_service.fetch_authenticated(
                    page,
                    f"/api/skill?skill={quote(skill)}",
                    "Skill",
                )

                recipes = self._parse_recipes(data)
                keys = {i["item_key"] for r in recipes for i in r["ingredients"]}
                keys |= {r["reward_key"] for r in recipes if r["reward_key"]}
                keys = sorted(keys)

                market = {}
                lookups = [key for key in keys if key not in FIXED_PRICES]

                for step, key in enumerate(lookups, start=1):
                    self._pause(step, len(lookups), key)
                    market[key] = self._lookup_price(page, key)

            with self.store_lock:
                store = self._load()
                store["skills"][skill] = {
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "recipes": recipes,
                    "market": market,
                }
                self._save(store)

        return self.view(skill)

    # ---- manual prices -----------------------------------------------

    def set_price(self, item_key, price):
        if not isinstance(item_key, str) or not KEY_RE.match(item_key):
            raise ValueError("Invalid item key")

        if price is not None:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise ValueError("Price must be a number")

            if not math.isfinite(price) or price < 0:
                raise ValueError("Price must be a non-negative number")

        with self.store_lock:
            store = self._load()

            if price is None:
                store["manual"].pop(item_key, None)
            else:
                store["manual"][item_key] = price

            self._save(store)

    # ---- view --------------------------------------------------------

    def view(self, skill):
        with self.store_lock:
            store = self._load()

        entry = store["skills"].get(skill)
        manual = store["manual"]

        if not isinstance(entry, dict):
            return {"skill": skill, "updated_at": None, "recipes": [], "items": [], "potions": []}

        market = entry.get("market") or {}

        def auto_price(key):
            if key in FIXED_PRICES:
                return FIXED_PRICES[key]

            return market.get(key)

        def price_info(key):
            """Used price (your price wins), your price, and the automatic price."""
            auto = auto_price(key)
            mine = manual.get(key)

            return {
                "price": mine if mine is not None else auto,
                "manual_price": mine,
                "auto_price": auto,
            }

        by_reward = {(r.get("reward_key") or r["key"]): r for r in entry.get("recipes") or []}

        def unit_price(key, stack=()):
            """Your price, else market/fixed, else craft price when the item is a potion."""
            price = price_info(key)["price"]

            if price is not None:
                return price

            recipe = by_reward.get(key)

            if recipe is None or key in stack:
                return None

            total = 0

            for ingredient in recipe["ingredients"]:
                part = unit_price(ingredient["item_key"], stack + (key,))

                if part is None:
                    return None

                total += part * ingredient["quantity"]

            return total

        recipes = []
        item_keys = set()

        for recipe in entry.get("recipes") or []:
            ingredients = []
            cost = 0
            missing = 0

            for ingredient in recipe["ingredients"]:
                key = ingredient["item_key"]
                quantity = ingredient["quantity"]
                info = price_info(key)
                price = unit_price(key)
                item_keys.add(key)

                total = price * quantity if price is not None else None

                if total is None:
                    missing += 1
                else:
                    cost += total

                ingredients.append(
                    {
                        "item_key": key,
                        "name": item_name(key),
                        "quantity": quantity,
                        "total": total,
                        **info,
                        "price": price,
                    }
                )

            reward_key = recipe.get("reward_key") or recipe["key"]
            cost = cost if not missing else None

            # Per-hour numbers use your current bonuses as reported by the skill API.
            cycle_ms = recipe.get("cycle_ms")
            sell = price_info(reward_key)["price"]
            crafts = qty = qty_double = gold = gold_double = None

            if cycle_ms:
                crafts = 3_600_000 / cycle_ms
                qty = crafts * (recipe.get("amount") or 1)
                qty_double = qty * (1 + (recipe.get("yield_chance") or 0) / 100)

                if sell is not None and cost is not None:
                    gold = qty * sell - crafts * cost
                    gold_double = qty_double * sell - crafts * cost

            recipes.append(
                {
                    "key": recipe["key"],
                    "label": recipe["label"],
                    "level": recipe.get("level"),
                    "reward_key": reward_key,
                    "market": price_info(reward_key) if reward_key else None,
                    "uses_potions": any(i["item_key"] in by_reward for i in ingredients),
                    "ingredients": ingredients,
                    "cost": cost,
                    "cost_double": cost / (1 + (recipe.get("yield_chance") or 0) / 100) if cost is not None and cycle_ms else None,
                    "missing": missing,
                    "crafts_per_hour": crafts,
                    "qty_per_hour": qty,
                    "qty_per_hour_double": qty_double,
                    "gold_per_hour": gold,
                    "gold_per_hour_double": gold_double,
                }
            )

        recipes.sort(key=lambda r: (r["level"] is None, r["level"] or 0, r["label"] or ""))

        # Potions are listed in their own table, not as ingredients.
        potion_keys = {r["reward_key"] for r in recipes}
        items = [{"item_key": key, "name": item_name(key), **price_info(key)} for key in sorted(item_keys - potion_keys)]

        potions = [{"item_key": r["reward_key"], "name": r["label"], **price_info(r["reward_key"])} for r in recipes]

        return {
            "skill": skill,
            "updated_at": entry.get("updated_at"),
            "recipes": recipes,
            "items": items,
            "potions": potions,
        }
