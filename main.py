import logging
import time
import requests

from playwright.sync_api import sync_playwright

from config import Config
from core.cache import Cache
from core.base import BaseService
from core.browser import BrowserService

from services.clan_bank import ClanBankService
from services.player import PlayerService
from services.global_buffs import GlobalBuffService
from services.skill_event import SkillEventService
from services.clan_boss import ClanBossService


class Watcher(BaseService):

    def __init__(self, config):
        cache = Cache(config.cache_file)

        super().__init__(config, cache)

        self.browser_service = BrowserService(config, cache)

        self.clan_bank = ClanBankService(config, cache)
        self.player = PlayerService(config, cache)
        self.global_buffs = GlobalBuffService(config, cache)
        self.skill_event = SkillEventService(config, cache)
        self.clan_boss = ClanBossService(config, cache)

        self.browser = None
        self.page = None

    def connect_browser(self, playwright):
        if self.browser is None or self.page is None or self.page.is_closed():
            self.browser_service.ensure_running()

            self.browser, self.page = self.browser_service.connect(playwright)

            self.logger.info("Connected to authenticated Koruxa tab.")

    def poll(self, playwright):
        self.connect_browser(playwright)

        self.clan_bank.check()

        self.player.check(
            self.page,
            self.browser_service,
            self.global_buffs,
            self.skill_event,
        )

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
    watcher.run()


if __name__ == "__main__":
    main()
