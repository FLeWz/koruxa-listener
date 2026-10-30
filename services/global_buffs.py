import json
from datetime import datetime, timezone

from config import GREEN, YELLOW
from core.base import BaseService


class GlobalBuffService(BaseService):

    def buff_key(self, buff):
        return (
            str(buff.get("buff_type", "unknown")),
            buff.get("buff_value"),
        )

    def buff_label(self, buff):
        labels = {
            "xp_boost": "XP Boost",
            "speed_boost": "Speed Boost",
        }

        kind = buff.get("buff_type", "unknown")
        label = labels.get(
            kind,
            kind.replace("_", " ").title(),
        )

        value = buff.get("buff_value")

        if value is not None:
            label += f" +{value}%"

        return label

    def utc_time(self, value):
        if not value:
            return "unknown"

        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        except (ValueError, AttributeError):
            return str(value)

    def process(self, data):
        buffs = data.get("global_buffs") or []

        if not isinstance(buffs, list):
            return

        current = {
            json.dumps(self.buff_key(buff), sort_keys=True): buff
            for buff in buffs
            if isinstance(buff, dict)
        }

        previous = self.cache.get("global_buffs")

        if previous is None:
            self.cache.set("global_buffs", current)
            return

        for key, buff in current.items():
            if key in previous:
                continue

            message = (
                f"🌍 **Global buff active: "
                f"{self.buff_label(buff)}**\n"
                f"Expires: {self.utc_time(buff.get('expires_at'))}\n"
                f"Activated by: "
                f"{buff.get('activated_by_name') or 'Unknown'}"
            )

            self.notify(
                message,
                GREEN,
                webhook=self.config.events_webhook,
            )

        for key, buff in previous.items():
            if key in current:
                continue

            self.notify(
                f"🌍 **Global buff ended:** " f"{self.buff_label(buff)}",
                YELLOW,
                webhook=self.config.events_webhook,
            )

        self.cache.set("global_buffs", current)
