from config import GREEN
from core.base import BaseService


class ClanBossService(BaseService):

    def format_duration(self, seconds):
        seconds = max(0, int(seconds or 0))

        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours:
            return f"{hours}h {minutes}m"

        if minutes:
            return f"{minutes}m {seconds}s"

        return f"{seconds}s"

    def process(self, data):
        boss = data.get("boss") or {}
        phase = data.get("phase") or {}

        if not isinstance(boss, dict):
            return

        if not isinstance(phase, dict):
            return

        state = phase.get("state")

        previous = self.cache.get(
            "clan_boss_phase",
            "__missing__",
        )

        if state != "active":
            self.cache.set("clan_boss_phase", None)
            return

        signature = {
            "state": state,
            "started": phase.get("started"),
            "window": phase.get("window"),
            "tier": boss.get("tier"),
            "name": boss.get("name", "Clan Boss"),
        }

        if previous == "__missing__":
            self.cache.set("clan_boss_phase", signature)
            return

        if previous == signature:
            return

        current_hp = boss.get("current_hp")
        max_hp = boss.get("max_hp")

        lines = [
            f"⚔️ **{signature['name']} — " f"Tier {signature['tier']}**",
            "",
        ]

        if current_hp is not None and max_hp is not None:
            lines.append(f"**HP:** {current_hp:,} / {max_hp:,}")

        parts = data.get("parts") or {}
        part_order = [
            ("head", "🎯"),
            ("arms", "💪"),
            ("body", "💀"),
            ("legs", "🦵"),
        ]

        for part_key, emoji in part_order:
            part = parts.get(part_key)

            if not isinstance(part, dict):
                continue

            label = part.get("label", part_key.title())

            hp = part.get("hp", 0)
            max_part_hp = part.get("max_hp", 0)

            shield_hp = part.get("shield_hp", 0)
            shield_max = part.get("shield_max", 0)

            hp_pct = round(hp / max_part_hp * 100) if max_part_hp else 0
            shield_pct = round(shield_hp / shield_max * 100) if shield_max else 0

            def make_bar(current, maximum, filled, empty):
                if not maximum or maximum <= 0:
                    filled_count = 0
                else:
                    filled_count = max(
                        0,
                        min(10, round(current / maximum * 10)),
                    )

                return filled * filled_count + empty * (10 - filled_count)

            shield_bar = make_bar(shield_hp, shield_max, "🔷", "◆")
            hp_bar = make_bar(hp, max_part_hp, "🟥", "⬛")

            lines.extend(
                [
                    "",
                    f"**{emoji} {label}**",
                    f"Shield: {shield_hp:,} / {shield_max:,}",
                    f"HP: {hp:,} / {max_part_hp:,}",
                    f"{shield_pct:3d}% {shield_bar}",
                    f"{hp_pct:3d}% {hp_bar}",
                ]
            )

        reset_in = data.get("reset_in")

        if reset_in is not None:
            lines.append(f"**Window resets in:** " f"{self.format_duration(reset_in)}")

        self.notify(
            "\n".join(lines),
            GREEN,
            webhook=self.config.clan_boss_event_webhook,
        )

        self.cache.set("clan_boss_phase", signature)
