from collections import defaultdict

from config import GREEN, RED, YELLOW
from core.base import BaseService


class ClanBankService(BaseService):

    def fetch(self):
        headers = {"Authorization": (f"Bearer {self.config.pub_clan_api_token}")}

        data = self.get(
            self.config.pub_clan_bank_api_url,
            headers=headers,
        )

        if not data.get("ok"):
            raise RuntimeError("Clan bank API returned ok=false")

        return data

    def format_item_table(self, entries, title):
        grouped = defaultdict(int)

        for entry in entries:
            character = entry.get("character", "Unknown")
            name = entry.get(
                "name",
                entry.get("item_key", "Unknown item"),
            )

            grouped[(character, name)] += entry.get("quantity", 0)

        rows = [
            (character, f"{quantity:,}x", name)
            for (character, name), quantity in grouped.items()
        ]

        if not rows:
            return None

        rows.sort(
            key=lambda row: (
                row[0].lower(),
                row[2].lower(),
            )
        )

        player_width = max(len(row[0]) for row in rows)
        quantity_width = max(len(row[1]) for row in rows)

        lines = [
            f"[ITEM] {title}",
            "",
            "```",
        ]

        for player, quantity, item in rows:
            lines.append(
                f"{player:<{player_width}}  "
                f"{quantity:>{quantity_width}}  "
                f"{item}"
            )

        lines.append("```")

        return "\n".join(lines)

    def process_items(self, data):
        logs = data.get("log", [])
        last_item_at = self.cache.get("last_item_at")

        if last_item_at is None:
            timestamps = [
                entry.get("at")
                for entry in logs
                if entry.get("action") in ("deposit", "withdraw") and entry.get("at")
            ]

            if timestamps:
                self.cache.set(
                    "last_item_at",
                    max(timestamps),
                )

            return

        deposits = []
        withdrawals = []

        for entry in logs:
            action = entry.get("action")
            timestamp = entry.get("at")

            if action not in ("deposit", "withdraw"):
                continue

            if not timestamp or timestamp <= last_item_at:
                continue

            if action == "deposit":
                deposits.append(entry)
            else:
                withdrawals.append(entry)

        deposits.sort(key=lambda entry: entry["at"])
        withdrawals.sort(key=lambda entry: entry["at"])

        for entries, title, color in (
            (deposits, "Deposits", GREEN),
            (withdrawals, "Withdrawals", RED),
        ):
            message = self.format_item_table(entries, title)

            if message:
                self.notify(message, color)

        new_entries = deposits + withdrawals

        if new_entries:
            self.cache.set(
                "last_item_at",
                max(entry["at"] for entry in new_entries),
            )

    def process_gold(self, data):
        new_coins = data.get("coins")
        old_coins = self.cache.get("coins")

        if new_coins is None:
            return

        if old_coins is None:
            self.cache.set("coins", new_coins)
            return

        change = new_coins - old_coins

        if change == 0:
            return

        sign = "+" if change > 0 else ""

        self.notify(
            f"[GOLD] {sign}{change:,} Coins " f"(total: {new_coins:,})",
            YELLOW,
        )

        self.cache.set("coins", new_coins)

    def check(self):
        data = self.fetch()

        self.process_items(data)
        self.process_gold(data)

        self.cache.set(
            "generated_at",
            data.get("generated_at"),
        )
