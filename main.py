import json
import time
import requests
from pathlib import Path
import os
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL"))
CACHE_FILE = Path(os.getenv("CACHE_FILE"))

PUB_PLAYER_API_URL = os.getenv("PUB_PLAYER_API_URL")
PUB_PLAYER_API_TOKEN = os.getenv("PUB_PLAYER_API_TOKEN")

PUB_CLAN_XP_API_URL = os.getenv("PUB_CLAN_XP_API_URL")
PUB_CLAN_BANK_API_URL = os.getenv("PUB_CLAN_BANK_API_URL")
PUB_CLAN_API_TOKEN = os.getenv("PUB_CLAN_API_TOKEN")

CHROME_CDP_URL = os.getenv("CHROME_CDP_URL")
PRIV_PLAYER_API_URL = os.getenv("PRIV_PLAYER_API_URL")
PRIV_CLAN_BOSS_API_URL = os.getenv("PRIV_CLAN_BOSS_API_URL")

CLAN_BANK_WEBHOOK = os.getenv("CLAN_BANK_WEBHOOK")
EVENTS_WEBHOOK = os.getenv("EVENTS_WEBHOOK")


HEADERS = {
    "Authorization": f"Bearer {PUB_CLAN_API_TOKEN}",
}

# Discord embed colors
GREEN = 0x2ECC71
RED = 0xE74C3C
YELLOW = 0xF1C40F


def load_cache():
    if not CACHE_FILE.exists():
        return {
            "generated_at": None,
            "coins": None,
            "last_item_at": None,
        }

    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            cache = json.load(f)

        cache.setdefault("generated_at", None)
        cache.setdefault("coins", None)
        cache.setdefault("last_item_at", None)

        return cache

    except (OSError, json.JSONDecodeError):
        return {
            "generated_at": None,
            "coins": None,
            "last_item_at": None,
        }


def save_cache(cache):
    temp_file = CACHE_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    temp_file.replace(CACHE_FILE)


def fetch_clan_bank():
    response = requests.get(PUB_CLAN_BANK_API_URL, headers=HEADERS, timeout=15)
    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError("API returned ok=false")

    return data


def send_discord(message, color, webhook=None):
    payload = {
        "embeds": [
            {
                "description": message,
                "color": color,
            }
        ]
    }

    response = requests.post(
        webhook or CLAN_BANK_WEBHOOK,
        json=payload,
        timeout=15,
    )

    response.raise_for_status()


def format_item_table(entries, title):
    grouped = defaultdict(int)

    for entry in entries:
        character = entry.get("character", "Unknown")
        name = entry.get(
            "name",
            entry.get("item_key", "Unknown item"),
        )
        quantity = entry.get("quantity", 0)

        grouped[(character, name)] += quantity

    rows = [
        (character, f"{quantity:,}x", name)
        for (character, name), quantity in grouped.items()
    ]

    if not rows:
        return None

    rows.sort(key=lambda row: (row[0].lower(), row[2].lower()))

    player_width = max(len(row[0]) for row in rows)

    quantity_width = max(len(row[1]) for row in rows)

    lines = [
        f"[ITEM] {title}",
        "",
        "```",
    ]

    for player, quantity, item in rows:
        lines.append(
            f"{player:<{player_width}}  " f"{quantity:>{quantity_width}}  " f"{item}"
        )

    lines.append("```")

    return "\n".join(lines)


def send_item_summary(deposits, withdrawals):
    if deposits:
        message = format_item_table(
            deposits,
            "Deposits",
        )

        if message:
            send_discord(message, GREEN)

    if withdrawals:
        message = format_item_table(
            withdrawals,
            "Withdrawals",
        )

        if message:
            send_discord(message, RED)


def process_item_logs(data, cache):
    logs = data.get("log", [])
    last_item_at = cache.get("last_item_at")

    # First run: establish the newest existing event
    # without announcing existing history.
    if last_item_at is None:
        timestamps = [
            entry.get("at")
            for entry in logs
            if entry.get("action") in ("deposit", "withdraw") and entry.get("at")
        ]

        if timestamps:
            cache["last_item_at"] = max(timestamps)

        return

    deposits = []
    withdrawals = []

    for entry in logs:
        action = entry.get("action")
        timestamp = entry.get("at")

        if action not in ("deposit", "withdraw"):
            continue

        if not timestamp:
            continue

        if timestamp <= last_item_at:
            continue

        if action == "deposit":
            deposits.append(entry)
        else:
            withdrawals.append(entry)

    deposits.sort(key=lambda entry: entry["at"])
    withdrawals.sort(key=lambda entry: entry["at"])

    send_item_summary(deposits, withdrawals)

    new_entries = deposits + withdrawals

    if new_entries:
        cache["last_item_at"] = max(entry["at"] for entry in new_entries)


def process_gold(data, cache):
    new_coins = data.get("coins")
    old_coins = cache.get("coins")

    if new_coins is None:
        return

    # First run: establish balance without announcing.
    if old_coins is None:
        cache["coins"] = new_coins
        return

    change = new_coins - old_coins

    if change == 0:
        return

    if change > 0:
        message = f"[GOLD] +{change:,} Coins " f"(total: {new_coins:,})"
    else:
        message = f"[GOLD] {change:,} Coins " f"(total: {new_coins:,})"

    send_discord(message, YELLOW)

    cache["coins"] = new_coins


def check_clan_bank(cache):
    data = fetch_clan_bank()

    process_item_logs(data, cache)
    process_gold(data, cache)

    cache["generated_at"] = data.get("generated_at")

    save_cache(cache)


def main():
    print("Koruxa watcher started.")
    print(f"Clan bank API: {PUB_CLAN_BANK_API_URL}")
    print(f"Player API: {PRIV_PLAYER_API_URL}")
    print(f"Polling every {POLL_INTERVAL} seconds.")

    cache = load_cache()

    while True:
        try:
            check_clan_bank(cache)

        except requests.RequestException as e:
            print(f"[ERROR] Request failed: {e}")

        except (ValueError, RuntimeError) as e:
            print(f"[ERROR] API error: {e}")

        except Exception as e:
            print(f"[ERROR] Unexpected error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
