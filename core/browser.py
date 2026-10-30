import os
import time
import urllib.request
import urllib.error
import subprocess

from core.base import BaseService


class BrowserService(BaseService):
    DEBUG_URL = "http://127.0.0.1:9222/json/version"

    def ensure_running(self):
        try:
            with urllib.request.urlopen(
                self.DEBUG_URL,
                timeout=2,
            ) as response:
                if response.status == 200:
                    self.logger.info("Chrome debugging endpoint is available.")
                    return

        except (urllib.error.URLError, TimeoutError, OSError):
            pass

        self.logger.info("Launching Koruxa browser...")

        profile_dir = os.path.expanduser("~/.koruxa-bot-chrome")

        subprocess.Popen(
            [
                "google-chrome-stable",
                "--remote-debugging-port=9222",
                f"--user-data-dir={profile_dir}",
                "https://koruxa.com/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        for _ in range(30):
            try:
                with urllib.request.urlopen(
                    self.DEBUG_URL,
                    timeout=2,
                ) as response:
                    if response.status == 200:
                        self.logger.info("Chrome started.")
                        return

            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
            ):
                time.sleep(1)

        raise RuntimeError("Chrome failed to start within 30 seconds.")

    def connect(self, playwright):
        browser = playwright.chromium.connect_over_cdp(self.config.chrome_cdp_url)

        for context in browser.contexts:
            for page in context.pages:
                if page.url.startswith("https://koruxa.com/") and not page.is_closed():
                    return browser, page

        raise RuntimeError("No open Koruxa tab found in connected Chrome.")

    def fetch_authenticated(self, page, url, name):
        result = page.evaluate(
            """
            async (url) => {
                const response = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers: {
                        "Accept": "application/json"
                    }
                });

                if (!response.ok) {
                    throw new Error(
                        `API returned HTTP ${response.status}`
                    );
                }

                return await response.json();
            }
            """,
            url,
        )

        if not isinstance(result, dict):
            raise RuntimeError(f"Invalid {name} API response")

        return result

    def fetch_player(self, page):
        return self.fetch_authenticated(
            page,
            self.config.priv_player_api_url,
            "Player",
        )

    def fetch_clan_boss(self, page):
        return self.fetch_authenticated(
            page,
            self.config.priv_clan_boss_api_url,
            "Clan boss",
        )
