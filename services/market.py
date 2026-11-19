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

# Seconds between checks of the job queue (also the worker heartbeat interval).
JOB_POLL = 2


class MarketService(BaseService):
    """Fetches recipes from the skill API and prices from the marketplace into SQLite.

    This is the part that needs Chrome. The Go web server (web/) reads the
    results from the database and queues refresh requests there for this service.
    """

    def __init__(self, config, cache):
        super().__init__(config, cache)

        # An old JSON store at config.market_file is imported on first start.
        self.store = Store(config.market_db, config.market_file)
        self.browser_service = BrowserService(config, cache)

        self.refresh_lock = threading.Lock()

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
                    self.store.set_job_progress(skill, f"{step}/{len(lookups)}")
                    self._pause(step, len(lookups), key)
                    market[key] = self._lookup_price(page, key)

            self.store.save_skill(skill, recipes, market)

    # ---- refresh jobs ------------------------------------------------
    # A refresh takes minutes. The web server queues a row in refresh_jobs (see
    # core/store.py); serve_jobs() picks it up, runs it and reports progress there.

    def _run_job(self, skill):
        try:
            self.refresh(skill)
        except Exception as error:
            self.logger.exception("Market refresh failed for %s", skill)
            self.store.finish_job(skill, error=str(error) or type(error).__name__)
        else:
            self.logger.info("Market refresh done for %s", skill)
            self.store.finish_job(skill)

    def serve_jobs(self):
        """Run queued refresh jobs one at a time and keep the worker heartbeat. Never returns."""
        self.store.fail_interrupted_jobs()
        self.logger.info("Waiting for market refresh requests in %s", self.config.market_db)

        job = None

        while True:
            try:
                self.store.heartbeat()

                if job is None or not job.is_alive():
                    skill = self.store.claim_job()

                    if skill:
                        job = threading.Thread(target=self._run_job, args=(skill,), name=f"refresh-{skill}", daemon=True)
                        job.start()

            except Exception:
                self.logger.exception("Job queue error")

            time.sleep(JOB_POLL)
