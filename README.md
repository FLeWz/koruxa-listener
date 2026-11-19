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

- A Chrome with remote debugging that you run and log in to Koruxa yourself. The
  worker only attaches to it (`CHROME_CDP_URL`); it never starts a browser.
- Docker with Compose (easiest), or Python 3, Go 1.24+ and `make` to run it by hand.

## Configuration

Copy `.env.example` to `.env` and fill in the values. All variables listed
below are required and must not be empty, except `ROLE_ID_*`.

- `POLL_INTERVAL`: seconds between polls
- `CACHE_FILE`: path of the JSON cache
- `PUB_PLAYER_API_URL`, `PUB_PLAYER_API_TOKEN`
- `PUB_CLAN_XP_API_URL`, `PUB_CLAN_BANK_API_URL`, `PUB_CLAN_API_TOKEN`
- `CHROME_CDP_URL`: DevTools endpoint of your Chrome, as an IP address
- `PRIV_PLAYER_API_URL`, `PRIV_CLAN_BOSS_API_URL`
- `CLAN_BANK_WEBHOOK`, `NODE_EVENTS_WEBHOOK`, `CLAN_BOSS_EVENT_WEBHOOK`,
  `GLOBAL_BUFFS_WEBHOOK`: Discord webhook URLs

Optional: `ROLE_ID_<NAME>` holds the Discord role ID to ping for a skill or
`CLANBOSS` (for example `ROLE_ID_MINING`). If unset, the message uses plain
`@Name` text, which does not ping. See `ROLES` in `config.py` for the names.

Never commit `.env`. It is gitignored.

## Run with Docker

    docker compose up -d --build

Two containers share one SQLite database in the `data` volume (`/data`):

- `worker`: the Python watcher. Polls Koruxa, posts to Discord, runs market refreshes.
- `web`: the Go web server on `http://127.0.0.1:8091/` (`MARKET_PORT`, `WEB_BIND`).

The worker reaches your browser at `CHROME_CDP_URL`. Inside a container `127.0.0.1` is the
container itself, so set it to the IP of the machine running Chrome, for example
`http://192.168.1.10:9222`. Chrome listens on `127.0.0.1` only by default, so the
browser must be started so that it accepts connections on that address (for example
`--remote-debugging-address=0.0.0.0`, or a port forward). Anything that can reach that
port controls the browser and its Koruxa login: keep it on a trusted network.

## Run without Docker

    make run        # the worker (main.py), in the venv
    make run-web    # the web server, reads .env

`make venv` creates the venv from `requirements.txt`. Both processes must use the same
`MARKET_DB`.

## Layout

    main.py       watcher loop, starts the market job loop
    config.py     env config, role list, embed colors
    core/         base service, browser attach, cache, Discord helper, SQLite store
    services/     one module per event source
    web/          Go web server: login, Herblore market, Herblore tracker, users
    Dockerfile, web/Dockerfile, docker-compose.yml

## Web server and the worker

The web server and the worker only talk through the database. A market refresh takes
minutes, so the web server queues a row in `refresh_jobs`, the worker picks it up, runs it
and writes the progress back, and the page polls the status. The worker also updates
`meta.worker_heartbeat`; without a recent beat the web server answers "The worker is not
running" instead of queueing. The web server does not need Chrome, so the pages work
even when the browser or the worker is down. `ENABLE_MARKET_HELPER_WEB=false` stops the
worker from answering refresh requests.

The first account created at `/login` on an empty database is the admin.

## License

MIT. See `LICENSE`.
