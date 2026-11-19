import random
import threading
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from core.base import BaseService
from core.browser import BrowserService
from core.store import Store

# Items that are not sold on the market (the Go web server knows the same list).
FIXED_PRICES = {"vial_of_water": 50}

# Random pause (seconds) between API requests, to stay polite to the server.
REQUEST_DELAY = (10, 20)


class MarketService(BaseService):
    """Fetches recipes from the skill API and prices from the marketplace into SQLite.

    This is the part that needs Chrome. The Go web server (web/) reads the
    results from the database and asks this service, through core/worker.py,
    to refresh.
    """

    def __init__(self, config, cache):
        super().__init__(config, cache)

        # An old JSON store at config.market_file is imported on first start.
        self.store = Store(config.market_db, config.market_file)
        self.browser_service = BrowserService(config, cache)

        self.refresh_lock = threading.Lock()

        self.jobs = {}  # skill -> {state, progress, error, started_at, finished_at}
        self.jobs_lock = threading.Lock()

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
                    self._set_job(skill, progress=f"{step}/{len(lookups)}")
                    self._pause(step, len(lookups), key)
                    market[key] = self._lookup_price(page, key)

            self.store.save_skill(skill, recipes, market)

    # ---- refresh jobs ------------------------------------------------
    # A refresh takes minutes, so the web server starts one and polls its status.

    def _set_job(self, skill, **fields):
        with self.jobs_lock:
            self.jobs.setdefault(skill, {"state": "idle"}).update(fields)

    def job_status(self, skill):
        with self.jobs_lock:
            return dict(self.jobs.get(skill, {"state": "idle"}))

    def start_refresh(self, skill):
        """Start a refresh in the background. A refresh already running is reported, not doubled."""
        with self.jobs_lock:
            if any(job.get("state") == "running" for job in self.jobs.values()):
                job = self.jobs.get(skill) or {}

                return dict(job) if job.get("state") == "running" else {"state": "running", "progress": "another refresh is running"}

            self.jobs[skill] = {"state": "running", "progress": "starting", "started_at": time.time(), "error": None}

        threading.Thread(target=self._run_refresh, args=(skill,), name=f"refresh-{skill}", daemon=True).start()

        return self.job_status(skill)

    def _run_refresh(self, skill):
        try:
            self.refresh(skill)
        except Exception as error:
            self.logger.exception("Market refresh failed for %s", skill)
            self._set_job(skill, state="error", error=str(error) or type(error).__name__, finished_at=time.time())
        else:
            self.logger.info("Market refresh done for %s", skill)
            self._set_job(skill, state="done", progress="", finished_at=time.time())
