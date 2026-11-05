# koruxa-listener

Python poller that watches Koruxa (https://koruxa.com/) and posts events to
Discord webhooks.

## What it does

Every `POLL_INTERVAL` seconds the watcher checks:

- Clan bank: new items and gold changes (`services/clan_bank.py`)
- Player data: feeds the skill event and global buff checks (`services/player.py`)
- Global buffs: XP / speed boosts (`services/global_buffs.py`)
- Skill events: node events, with optional role mentions (`services/skill_event.py`)
- Clan boss: HP and shield status (`services/clan_boss.py`)

Each service posts a Discord embed through `BaseService.notify()`.
State is kept in a JSON cache file so events are not reported twice.

## Requirements

- Python 3
- Google Chrome (`google-chrome-stable` on PATH)
- Python packages: `requests`, `python-dotenv`, `playwright`

There is no `requirements.txt` yet. Install manually:

    python -m venv venv
    source venv/bin/activate
    pip install requests python-dotenv playwright

## Configuration

Copy `.env.example` to `.env` and fill in the values. All variables listed
below are required and must not be empty, except `ROLE_ID_*`.

- `POLL_INTERVAL`: seconds between polls
- `CACHE_FILE`: path of the JSON cache
- `PUB_PLAYER_API_URL`, `PUB_PLAYER_API_TOKEN`
- `PUB_CLAN_XP_API_URL`, `PUB_CLAN_BANK_API_URL`, `PUB_CLAN_API_TOKEN`
- `CHROME_CDP_URL`: Chrome DevTools endpoint
- `PRIV_PLAYER_API_URL`, `PRIV_CLAN_BOSS_API_URL`
- `CLAN_BANK_WEBHOOK`, `NODE_EVENTS_WEBHOOK`, `CLAN_BOSS_EVENT_WEBHOOK`,
  `GLOBAL_BUFFS_WEBHOOK`: Discord webhook URLs

Optional: `ROLE_ID_<NAME>` holds the Discord role ID to ping for a skill or
`CLANBOSS` (for example `ROLE_ID_MINING`). If unset, the message uses plain
`@Name` text, which does not ping. See `ROLES` in `config.py` for the names.

Never commit `.env`. It is gitignored.

## Run

    python main.py

On start the watcher launches Chrome with remote debugging on port 9222 and
profile `~/.koruxa-bot-chrome`, opens Koruxa, and attaches via Playwright.
Log in to Koruxa in that browser once. The private API calls reuse that
authenticated session.

## Layout

    main.py       watcher loop
    config.py     env config, role list, embed colors
    core/         base service, browser, cache, Discord helper
    services/     one module per event source

## License

MIT. See `LICENSE`.
