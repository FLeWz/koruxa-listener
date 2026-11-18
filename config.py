import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


ROLES = [
    "Woodcutting",
    "Mining",
    "Fishing",
    "Cooking",
    "Fletching",
    "Crafting",
    "Jewellery",
    "Herblore",
    "Smithing",
    "Firemaking",
    "Alchemy",
    "Arcana",
    "Construction",
    "Tinkering",
    "ClanBoss",
    "Combat",
]


@dataclass(frozen=True)
class Config:
    poll_interval: int
    cache_file: Path

    pub_player_api_url: str
    pub_player_api_token: str

    pub_clan_xp_api_url: str
    pub_clan_bank_api_url: str
    pub_clan_api_token: str

    chrome_cdp_url: str
    priv_player_api_url: str
    priv_clan_boss_api_url: str

    clan_bank_webhook: str
    node_events_webhook: str
    clan_boss_event_webhook: str
    global_buffs_webhook: str

    # role name (lowercase) -> Discord role ID; empty when env var unset
    role_ids: dict

    # which services to run (all on by default)
    enable_clan_bank: bool
    enable_global_buffs: bool
    enable_skill_event: bool
    enable_clan_boss: bool
    enable_market: bool

    # market helper web server (optional env, defaults used when unset/empty)
    market_host: str
    market_port: int
    market_file: Path

    @classmethod
    def from_env(cls):
        def required(name):
            value = os.getenv(name)
            if not value:
                raise ValueError(f"Missing environment variable: {name}")
            return value

        def flag(name):
            value = (os.getenv(name) or "true").strip().lower()
            return value not in ("0", "false", "no", "off")

        enable_clan_bank = flag("ENABLE_CLAN_BANK")
        enable_global_buffs = flag("ENABLE_GLOBAL_BUFFS")
        enable_skill_event = flag("ENABLE_SKILL_EVENT")
        enable_clan_boss = flag("ENABLE_CLAN_BOSS")

        def required_if(enabled, name):
            # A disabled service does not need its webhook configured.
            return required(name) if enabled else os.getenv(name, "")

        return cls(
            poll_interval=int(required("POLL_INTERVAL")),
            cache_file=Path(required("CACHE_FILE")),
            pub_player_api_url=required("PUB_PLAYER_API_URL"),
            pub_player_api_token=required("PUB_PLAYER_API_TOKEN"),
            pub_clan_xp_api_url=required("PUB_CLAN_XP_API_URL"),
            pub_clan_bank_api_url=required("PUB_CLAN_BANK_API_URL"),
            pub_clan_api_token=required("PUB_CLAN_API_TOKEN"),
            chrome_cdp_url=required("CHROME_CDP_URL"),
            priv_player_api_url=required("PRIV_PLAYER_API_URL"),
            priv_clan_boss_api_url=required("PRIV_CLAN_BOSS_API_URL"),
            clan_bank_webhook=required_if(enable_clan_bank, "CLAN_BANK_WEBHOOK"),
            node_events_webhook=required_if(enable_skill_event, "NODE_EVENTS_WEBHOOK"),
            clan_boss_event_webhook=required_if(enable_clan_boss, "CLAN_BOSS_EVENT_WEBHOOK"),
            global_buffs_webhook=required_if(enable_global_buffs, "GLOBAL_BUFFS_WEBHOOK"),
            role_ids={r.lower(): os.getenv(f"ROLE_ID_{r.upper()}", "") for r in ROLES},
            enable_clan_bank=enable_clan_bank,
            enable_global_buffs=enable_global_buffs,
            enable_skill_event=enable_skill_event,
            enable_clan_boss=enable_clan_boss,
            enable_market=flag("ENABLE_MARKET_HELPER_WEB"),
            market_host=os.getenv("MARKET_HOST") or "127.0.0.1",
            market_port=int(os.getenv("MARKET_PORT") or 8091),
            market_file=Path(os.getenv("MARKET_FILE") or "market.json"),
        )


# Discord embed colors
GREEN = 0x2ECC71
RED = 0xE74C3C
YELLOW = 0xF1C40F
