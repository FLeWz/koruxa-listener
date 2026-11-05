import logging
import re

import requests

from config import Config


class BaseService:
    def __init__(self, config: Config, cache):
        self.config = config
        self.cache = cache

        self.session = requests.Session()

        self.logger = logging.getLogger(self.__class__.__name__)

    def get(self, url, headers=None, timeout=15):
        response = self.session.get(
            url,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def post(self, url, payload, timeout=15):
        response = self.session.post(
            url,
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response

    def mention(self, role):
        key = str(role).replace(" ", "").replace("_", "").lower()
        role_id = self.config.role_ids.get(key)

        if role_id:
            return f"<@&{role_id}>"

        return f"@{role}"

    def notify(self, message, color, webhook=None):
        webhook = webhook or self.config.clan_bank_webhook

        payload = {
            "embeds": [
                {
                    "description": message,
                    "color": color,
                }
            ]
        }

        role_ids = re.findall(r"<@&(\d+)>", message)

        if role_ids:
            payload["allowed_mentions"] = {"roles": list(dict.fromkeys(role_ids))}

        self.post(webhook, payload)

    def save_cache(self):
        self.cache.save()
