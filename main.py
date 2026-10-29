import json
import time
import requests
from pathlib import Path
import os
import subprocess
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
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


def ensure_chrome_running():
    debug_url = "http://127.0.0.1:9222/json/version"

    # Reuse Chrome if its debugging endpoint is already available.
    try:
        with urllib.request.urlopen(debug_url, timeout=2) as response:
            if response.status == 200:
                print("Chrome debugging endpoint is available.")
                return
    except (urllib.error.URLError, TimeoutError, OSError):
        pass

    print("Chrome is not available. Launching Koruxa browser...")

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

    # Wait for Chrome's debugging endpoint to become available.
    for _ in range(30):
        try:
            with urllib.request.urlopen(debug_url, timeout=2) as response:
                if response.status == 200:
                    print("Chrome started successfully.")
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1)

    raise RuntimeError(
        "Chrome failed to start or debugging port 9222 "
        "did not become available within 30 seconds."
    )


def connect_koruxa_browser(playwright):
    browser = playwright.chromium.connect_over_cdp(CHROME_CDP_URL)

    for context in browser.contexts:
        for page in context.pages:
            if page.url.startswith("https://koruxa.com/") and not page.is_closed():
                return browser, page

    raise RuntimeError("No open Koruxa tab found in the connected Chrome.")


def fetch_player_data(page):
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
                    `Player API returned HTTP ${response.status}`
                );
            }

            return await response.json();
        }
    """,
        PRIV_PLAYER_API_URL,
    )

    if not isinstance(result, dict):
        raise RuntimeError("Invalid player API response")

    return result


def utc_time(value):
    if not value:
        return "unknown"

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, AttributeError):
        return str(value)


def buff_key(buff):
    # Exclude seconds_left: it changes on every poll.
    return (
        str(buff.get("buff_type", "unknown")),
        buff.get("buff_value"),
        buff.get("expires_at"),
        buff.get("activated_by_name"),
    )


def buff_label(buff):
    labels = {
        "xp_boost": "XP Boost",
        "speed_boost": "Speed Boost",
    }

    kind = buff.get("buff_type", "unknown")
    label = labels.get(kind, kind.replace("_", " ").title())
    value = buff.get("buff_value")

    if value is not None:
        label += f" +{value}%"

    return label


def process_global_buffs(data, cache):
    buffs = data.get("global_buffs") or []

    if not isinstance(buffs, list):
        return

    current = {
        json.dumps(buff_key(buff), sort_keys=True): buff
        for buff in buffs
        if isinstance(buff, dict)
    }

    previous = cache.get("global_buffs")

    # First run: save the current state without announcements.
    if previous is None:
        cache["global_buffs"] = current
        return

    # Announce new or changed buffs.
    for key, buff in current.items():
        old = previous.get(key)

        if old is not None:
            continue

        message = (
            f"🌍 **Global buff active: {buff_label(buff)}**\n"
            f"Expires: {utc_time(buff.get('expires_at'))}\n"
            f"Activated by: "
            f"{buff.get('activated_by_name') or 'Unknown'}"
        )
        send_discord(message, GREEN, webhook=EVENTS_WEBHOOK)

    # Announce buffs that disappeared.
    for key, buff in previous.items():
        if key in current:
            continue

        send_discord(
            f"🌍 **Global buff ended or removed:** " f"{buff_label(buff)}",
            YELLOW,
            webhook=EVENTS_WEBHOOK,
        )

    cache["global_buffs"] = current


def skill_event_key(event):
    if not isinstance(event, dict) or not event:
        return None

    # Ignore countdown fields, which naturally change each poll.
    return {
        "id": event.get("id"),
        "flavor": event.get("flavor"),
        "node_label": event.get("node_label"),
        "skill": event.get("skill"),
        "state": event.get("state"),
        "shield_max": event.get("shield_max"),
    }


def format_skill_event(event):
    skill = (event.get("skill") or "unknown").replace("_", " ").title()
    name = event.get("flavor") or "Skill Event"
    target = event.get("node_label") or "Unknown target"

    seconds = event.get("seconds_left")
    time_left = ""

    if isinstance(seconds, (int, float)):
        minutes = max(0, int(seconds)) // 60
        time_left = f"\nTime remaining: {minutes}m"

    shield_hp = event.get("shield_hp")
    shield_max = event.get("shield_max")

    shield = ""
    if shield_hp is not None and shield_max is not None:
        shield = f"\nShield: {shield_hp:,} / {shield_max:,} HP"

    return (
        f"⚔️ **{name}**\n" f"Skill: {skill} · Target: {target}" f"{time_left}{shield}"
    )


def process_skill_event(data, cache):
    event = data.get("skill_event") or {}

    if not isinstance(event, dict):
        event = {}

    current = skill_event_key(event)
    previous = cache.get("skill_event")

    # First run: establish baseline.
    if "skill_event" not in cache:
        cache["skill_event"] = current
        return

    if current == previous:
        return

    if current is None:
        if previous is not None:
            send_discord(
                "⚔️ **Skill event ended or is no longer active.**",
                YELLOW,
                webhook=EVENTS_WEBHOOK,
            )

    elif previous is None:
        send_discord(
            "⚔️ **Skill event detected!**\n" + format_skill_event(event),
            GREEN,
            webhook=EVENTS_WEBHOOK,
        )

    else:
        send_discord(
            "⚔️ **Skill event changed!**\n" + format_skill_event(event),
            GREEN,
            webhook=EVENTS_WEBHOOK,
        )

    cache["skill_event"] = current


def fetch_clan_boss(page):
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
                    `Clan boss API returned HTTP ${response.status}`
                );
            }

            return await response.json();
        }
    """,
        PRIV_CLAN_BOSS_API_URL,
    )

    if not isinstance(result, dict):
        raise RuntimeError("Invalid clan boss API response")

    return result


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def process_clan_boss(data, cache):
    boss = data.get("boss") or {}
    phase = data.get("phase") or {}

    if not isinstance(boss, dict) or not isinstance(phase, dict):
        return

    state = phase.get("state")
    previous = cache.get("clan_boss_phase", "__missing__")

    if state != "active":
        # Remember that the active phase has ended. This allows
        # the next active phase to trigger a notification.
        cache["clan_boss_phase"] = None
        return

    signature = {
        "state": phase.get("state"),
        "started": phase.get("started"),
        "window": phase.get("window"),
        "tier": boss.get("tier"),
        "name": boss.get("name", "Clan Boss"),
    }

    # First run: establish a baseline without announcing an
    # already-running boss.
    if previous == "__missing__":
        cache["clan_boss_phase"] = signature
        return

    if previous == signature:
        return

    current_hp = boss.get("current_hp")
    max_hp = boss.get("max_hp")
    tier = signature["tier"]
    name = signature["name"]
    reset_in = data.get("reset_in")

    lines = [
        f"⚔️ **{name} — Tier {tier}**",
        "",
    ]

    if current_hp is not None and max_hp is not None:
        lines.append(f"**HP:** {current_hp:,} / {max_hp:,}")

    if reset_in is not None:
        lines.append(f"**Window resets in:** {format_duration(reset_in)}")

    send_discord(
        "\n".join(lines),
        GREEN,
        webhook=EVENTS_WEBHOOK,
    )

    cache["clan_boss_phase"] = signature


def main():
    print("Koruxa watcher started.")
    print(f"Clan bank API: {PUB_CLAN_BANK_API_URL}")
    print(f"Player API: {PRIV_PLAYER_API_URL}")
    print(f"Polling every {POLL_INTERVAL} seconds.")

    cache = load_cache()
    browser = None
    page = None

    with sync_playwright() as playwright:
        while True:
            try:
                if browser is None or page is None or page.is_closed():
                    ensure_chrome_running()
                    browser, page = connect_koruxa_browser(playwright)
                    print("Connected to authenticated Koruxa tab.")

                check_clan_bank(cache)

                data = fetch_player_data(page)
                process_global_buffs(data, cache)
                process_skill_event(data, cache)

                boss_data = fetch_clan_boss(page)
                process_clan_boss(boss_data, cache)

                save_cache(cache)

            except requests.RequestException as e:
                print(f"[ERROR] Clan bank request failed: {e}")

            except Exception as e:
                print(f"[ERROR] Watcher error: {e}")
                browser = None
                page = None

            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
