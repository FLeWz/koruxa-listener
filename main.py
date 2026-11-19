import logging
import threading
import time
import requests

from playwright.sync_api import sync_playwright

from config import Config
from core.cache import Cache
from core.base import BaseService
from core.browser import BrowserService
from core.auth import Auth
from core.tracker import Tracker
from core.webserver import start_webserver

from services.clan_bank import ClanBankService
from services.player import PlayerService
from services.global_buffs import GlobalBuffService
from services.skill_event import SkillEventService
from services.clan_boss import ClanBossService
from services.market import MarketService


class Watcher(BaseService):

    def __init__(self, config):
        cache = Cache(config.cache_file)

        super().__init__(config, cache)

        self.browser_service = BrowserService(config, cache)

        # Only enabled services are created; the rest stay None.
        self.clan_bank = ClanBankService(config, cache) if config.enable_clan_bank else None
        self.player = PlayerService(config, cache)
        self.global_buffs = GlobalBuffService(config, cache) if config.enable_global_buffs else None
        self.skill_event = SkillEventService(config, cache) if config.enable_skill_event else None
        self.clan_boss = ClanBossService(config, cache) if config.enable_clan_boss else None

        self.browser = None
        self.page = None

    def connect_browser(self, playwright):
        if self.browser is None or self.page is None or self.page.is_closed():
            self.browser_service.ensure_running()

            self.browser, self.page = self.browser_service.connect(playwright)

            self.logger.info("Connected to authenticated Koruxa tab.")

    def has_work(self):
        return any((self.clan_bank, self.global_buffs, self.skill_event, self.clan_boss))

    def poll(self, playwright):
        needs_player = self.global_buffs or self.skill_event

        if needs_player or self.clan_boss:
            self.connect_browser(playwright)

        if self.clan_bank:
            self.clan_bank.check()

        if needs_player:
            self.player.check(
                self.page,
                self.browser_service,
                self.global_buffs,
                self.skill_event,
            )

        if self.clan_boss:
            boss_data = self.browser_service.fetch_clan_boss(self.page)

            self.clan_boss.process(boss_data)

        self.cache.save()

    def run(self):
        self.logger.info("Koruxa watcher started.")
        self.logger.info(
            "Clan bank API: %s",
            self.config.pub_clan_bank_api_url,
        )
        self.logger.info(
            "Player API: %s",
            self.config.priv_player_api_url,
        )
        self.logger.info(
            "Polling every %s seconds.",
            self.config.poll_interval,
        )

        with sync_playwright() as playwright:
            while True:
                try:
                    self.poll(playwright)

                except requests.RequestException as error:
                    self.logger.error(
                        "HTTP request failed: %s",
                        error,
                    )

                except Exception:
                    self.logger.exception("Watcher error")

                    self.browser = None
                    self.page = None

                time.sleep(self.config.poll_interval)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s [%(levelname)s] " "%(name)s: %(message)s"),
    )

    config = Config.from_env()

    watcher = Watcher(config)

    if config.enable_market:
        market = MarketService(config, watcher.cache)

        start_webserver(config, market, Tracker(market.store, market), Auth(market.store))

    if watcher.has_work():
        watcher.run()
    elif config.enable_market:
        logging.getLogger("main").info("No watcher services enabled. Only the market web server is running.")
        threading.Event().wait()
    else:
        logging.getLogger("main").error("All services are disabled. Nothing to run.")


if __name__ == "__main__":
    main()
