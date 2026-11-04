from config import GREEN, YELLOW
from core.base import BaseService


class SkillEventService(BaseService):

    def event_key(self, event):
        if not isinstance(event, dict) or not event:
            return None

        return {
            "id": event.get("id"),
            "flavor": event.get("flavor"),
            "node_label": event.get("node_label"),
            "skill": event.get("skill"),
            "state": event.get("state"),
            "shield_max": event.get("shield_max"),
        }

    def format_event(self, event):
        skill = (event.get("skill") or "unknown").replace("_", " ").title()

        name = event.get("flavor") or "Skill Event"
        target = event.get("node_label") or "Unknown target"

        seconds = event.get("seconds_left")
        time_left = ""

        if isinstance(seconds, (int, float)):
            minutes = max(0, int(seconds)) // 60
            time_left = f"\nTime remaining: {minutes}m"

        return (
            f"⚔️ **{name}**\n"
            f"{self.mention(skill)} · {target}"
            f"{time_left}"
        )

    def process(self, data):
        event = data.get("skill_event") or {}

        if not isinstance(event, dict):
            event = {}

        current = self.event_key(event)

        if "skill_event" not in self.cache:
            self.cache.set("skill_event", current)
            return

        previous = self.cache.get("skill_event")

        if current == previous:
            return

        webhook = self.config.node_events_webhook

        if current is None:
            if previous is not None:
                self.notify(
                    f"**{previous["skill"]} event ended.**",
                    YELLOW,
                    webhook=webhook,
                )

        elif previous is None:
            self.notify(
                "**Skill event detected!**\n" + self.format_event(event) + "\nBreak the shield!",
                GREEN,
                webhook=webhook,
            )

        else:
            self.notify(
                "**Skill event active!**\n" + self.format_event(event),
                GREEN,
                webhook=webhook,
            )

        self.cache.set("skill_event", current)
