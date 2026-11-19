from core.base import BaseService


class BrowserService(BaseService):
    """Attaches to a Chrome that is already running with remote debugging.

    The browser is not started here. CHROME_CDP_URL must point at its DevTools
    endpoint, as an IP address (Chrome refuses other host names on that endpoint).
    """

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
