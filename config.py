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

    @classmethod
    def from_env(cls):
        def required(name):
            value = os.getenv(name)
            if not value:
                raise ValueError(f"Missing environment variable: {name}")
            return value

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
            clan_bank_webhook=required("CLAN_BANK_WEBHOOK"),
            node_events_webhook=required("NODE_EVENTS_WEBHOOK"),
            clan_boss_event_webhook=required("CLAN_BOSS_EVENT_WEBHOOK"),
            global_buffs_webhook=required("GLOBAL_BUFFS_WEBHOOK"),
            role_ids={r.lower(): os.getenv(f"ROLE_ID_{r.upper()}", "") for r in ROLES},
        )


# Discord embed colors
GREEN = 0x2ECC71
RED = 0xE74C3C
YELLOW = 0xF1C40F
