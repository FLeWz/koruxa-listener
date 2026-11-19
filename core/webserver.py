import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from core.tracker_page import TRACKER_PAGE

logger = logging.getLogger("WebServer")

SKILL_RE = re.compile(r"^[a-z_]+$")
DEFAULT_SKILL = "herblore"
MAX_BODY = 4096

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Herblore market</title>
<style>
  body { font: 14px system-ui, sans-serif; background: #15171c; color: #dde; margin: 2rem auto; max-width: 1900px; padding: 0 1rem; }
  h1, h2 { font-weight: 600; }
  button { background: #2d6cdf; color: #fff; border: 0; border-radius: 4px; padding: .4rem .9rem; cursor: pointer; }
  button:disabled { opacity: .5; cursor: wait; }
  button.link { background: none; color: #8ab; padding: 0 .4rem; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
  th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #2a2e38; vertical-align: top; }
  .side { display: flex; gap: 3rem; align-items: flex-start; flex-wrap: wrap; }
  table.narrow { width: auto; }
  table.narrow input { width: 7rem; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  input { width: 9rem; background: #0e1014; color: #dde; border: 1px solid #3a3f4b; border-radius: 4px; padding: .25rem .4rem; text-align: right; }
  input.manual { border-color: #f1c40f; color: #f1c40f; }
  input.missing { border-color: #e74c3c; }
  tbody tr:hover { background: #222733; }
  th.center { text-align: center; }
  .ingr { max-width: 400px; }
  th.grp, td.grp { border-left: 1px solid #3a3f4b; }
  .muted { color: #8a91a0; }
  .manual { color: #f1c40f; }
  .none { color: #e74c3c; }
  #status { margin-left: 1rem; }
  .err { color: #e74c3c; }
  nav { display: flex; gap: 1.5rem; padding-bottom: .6rem; border-bottom: 1px solid #2a2e38; }
  nav a { color: #8a91a0; text-decoration: none; padding: .2rem 0; border-bottom: 2px solid transparent; }
  nav a:hover { color: #dde; }
  nav a.active { color: #dde; border-bottom-color: #2d6cdf; }
</style>
</head>
<body>
<nav>
  <a href="/" class="active">Herblore market</a>
  <a href="/tracker">Herblore tracker</a>
</nav>
<h1 id="title">Herblore market</h1>
<p>
  <button id="refresh">Refresh market data</button>
  <span id="status" class="muted"></span>
</p>

<h2>Potions</h2>
<table>
  <thead>
    <tr>
      <th colspan="3"></th>
      <th class="grp center" colspan="3">Regular</th>
      <th class="grp center" colspan="3">Double Drop</th>
      <th class="grp"></th>
      <th class="grp"></th>
    </tr>
    <tr>
      <th class="num">Lvl</th><th>Potion</th><th>Ingredients</th>
      <th class="num grp">Craft price</th><th class="num">Qty/h</th><th class="num">Gold/h</th>
      <th class="num grp">Craft price</th><th class="num">Qty/h</th><th class="num">Gold/h</th>
      <th class="num grp" title="Market price. Potions made from other potions also show their craft price in brackets.">Market (craft)</th>
      <th class="num grp" title="Best Gold/h (regular or double drop) x 24">Gold/day</th>
    </tr>
  </thead>
  <tbody id="recipes"></tbody>
</table>

<div class="side">
  <div>
    <h2>Ingredients</h2>
    <table class="narrow">
      <thead><tr><th>Item</th><th class="num">Market</th><th>Your price</th></tr></thead>
      <tbody id="items"></tbody>
    </table>
  </div>
  <div>
    <h2>Potion prices</h2>
    <table class="narrow">
      <thead><tr><th>Potion</th><th class="num">Market</th><th>Your price</th></tr></thead>
      <tbody id="potions"></tbody>
    </table>
  </div>
</div>
<p class="muted">Type a price to override it. Empty fields use the market minimum (vial of water is fixed at 50).</p>

<script>
const skill = new URLSearchParams(location.search).get("skill") || "herblore";
const $ = (id) => document.getElementById(id);
const fmt = (n) => (n === null || n === undefined) ? "-" : Number(n).toLocaleString();

const fmt1 = (n) => (n === null || n === undefined) ? "-" : Number(n).toLocaleString(undefined, {maximumFractionDigits: 1});

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function status(text, err) {
  const s = $("status");
  s.textContent = text;
  s.className = err ? "err" : "muted";
}

async function api(path, options) {
  const r = await fetch(path, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
  return body;
}

async function savePrice(key, raw) {
  const text = raw.trim();
  const price = text === "" ? null : Number(text);
  if (price !== null && (!Number.isFinite(price) || price < 0)) {
    status("Invalid price", true);
    return;
  }
  try {
    await api("/api/price", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({item_key: key, price: price}),
    });
    render(await api("/api/data?skill=" + encodeURIComponent(skill)));
    status("Saved");
  } catch (e) {
    status(e.message, true);
  }
}

function priceInput(key, info) {
  const input = document.createElement("input");
  input.type = "text";
  input.inputMode = "decimal";
  input.value = info.manual_price === null ? "" : info.manual_price;
  input.placeholder = info.auto_price === null ? "enter price" : "";
  if (info.manual_price !== null) input.className = "manual";
  else if (info.auto_price === null) input.className = "missing";
  input.addEventListener("change", () => savePrice(key, input.value));
  return input;
}

function render(data) {
  $("title").textContent = data.skill.charAt(0).toUpperCase() + data.skill.slice(1) + " market";
  if (data.updated_at) status("Market data from " + new Date(data.updated_at).toLocaleString());
  else status("No data yet. Press refresh.");

  const recipes = $("recipes");
  recipes.replaceChildren();
  for (const r of data.recipes) {
    const tr = el("tr");
    tr.append(el("td", r.level === null ? "-" : r.level, "num"));
    tr.append(el("td", r.label));
    const cell = el("td");
    const ingredients = el("div", undefined, "ingr");
    cell.append(ingredients);
    r.ingredients.forEach((i, n) => {
      if (n > 0) ingredients.append(", ");
      ingredients.append(el("span", i.quantity + " x " + i.name, i.price === null ? "none" : undefined));
    });
    tr.append(cell);
    const money = (n, cls) => el("td", n === null ? "-" : Math.round(n).toLocaleString(), "num" + (cls || "") + (n === null ? " muted" : (n < 0 ? " none" : "")));

    tr.append(r.cost === null ? el("td", "missing " + r.missing + " price(s)", "num grp none") : money(r.cost, " grp"));
    tr.append(el("td", fmt1(r.qty_per_hour), "num"));
    tr.append(money(r.gold_per_hour));
    tr.append(money(r.cost_double, " grp"));
    tr.append(el("td", fmt1(r.qty_per_hour_double), "num"));
    tr.append(money(r.gold_per_hour_double));

    // Your price wins over the market price
    const price = r.market ? r.market.price : null;
    // No price: fall back to craft price, marked with "*"
    const fallback = price === null && r.cost !== null;
    let market = fallback ? "*" + fmt(r.cost) : fmt(price);
    if (r.uses_potions) market = fmt(price) + " (" + fmt(r.cost) + ")";
    const mine = r.market && r.market.manual_price !== null;
    tr.append(el("td", market, "num" + (mine ? " manual" : (price === null ? " muted" : ""))));
    const rates = [r.gold_per_hour, r.gold_per_hour_double].filter((g) => g !== null && g !== undefined);
    const perDay = rates.length ? Math.max(...rates) * 24 : null;
    tr.append(money(perDay, " grp"));
    recipes.append(tr);
  }

  fillPriceTable("items", data.items);
  fillPriceTable("potions", data.potions);
}

function fillPriceTable(id, list) {
  const body = $(id);
  body.replaceChildren();
  for (const it of list) {
    const tr = el("tr");
    tr.append(el("td", it.name));
    tr.append(el("td", fmt(it.auto_price), "num" + (it.auto_price === null ? " muted" : "")));
    const mine = el("td");
    mine.append(priceInput(it.item_key, it));
    tr.append(mine);
    body.append(tr);
  }
}

$("refresh").addEventListener("click", async () => {
  const button = $("refresh");
  button.disabled = true;
  status("Refreshing...");
  try {
    render(await api("/api/refresh?skill=" + encodeURIComponent(skill), {method: "POST"}));
  } catch (e) {
    status("Refresh failed: " + e.message, true);
  } finally {
    button.disabled = false;
  }
});

api("/api/data?skill=" + encodeURIComponent(skill)).then(render).catch((e) => status(e.message, true));
</script>
</body>
</html>
"""


def make_handler(market, tracker):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logger.info("%s - %s", self.address_string(), format % args)

        def send_body(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, payload):
            self.send_body(status, json.dumps(payload).encode(), "application/json")

        def read_body(self):
            length = int(self.headers.get("Content-Length") or 0)

            if length <= 0 or length > MAX_BODY:
                raise ValueError("Invalid request body")

            body = json.loads(self.rfile.read(length))

            if not isinstance(body, dict):
                raise ValueError("Invalid request body")

            return body

        def skill(self, query):
            skill = (query.get("skill") or [DEFAULT_SKILL])[0]

            if not SKILL_RE.match(skill):
                raise ValueError("Invalid skill")

            return skill

        def do_GET(self):
            url = urlparse(self.path)

            if url.path == "/":
                self.send_body(200, PAGE.encode(), "text/html; charset=utf-8")
            elif url.path == "/tracker":
                self.send_body(200, TRACKER_PAGE.encode(), "text/html; charset=utf-8")
            elif url.path in ("/api/data", "/api/tracker"):
                try:
                    skill = self.skill(parse_qs(url.query))
                    view = market.view if url.path == "/api/data" else tracker.view
                    self.send_json(200, view(skill))
                except ValueError as error:
                    self.send_json(400, {"error": str(error)})
            else:
                self.send_json(404, {"error": "Not found"})

        def do_POST(self):
            url = urlparse(self.path)

            try:
                if url.path == "/api/refresh":
                    self.send_json(200, market.refresh(self.skill(parse_qs(url.query))))

                elif url.path == "/api/price":
                    body = self.read_body()
                    market.set_price(body.get("item_key"), body.get("price"))
                    self.send_json(200, {"ok": True})

                elif url.path.startswith("/api/tracker/"):
                    action = url.path[len("/api/tracker/") :]
                    skill = self.skill(parse_qs(url.query))
                    self.send_json(200, tracker.handle(action, self.read_body(), skill))

                else:
                    self.send_json(404, {"error": "Not found"})

            except LookupError as error:
                self.send_json(404, {"error": str(error)})
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except Exception as error:
                logger.exception("Request failed: %s", url.path)
                self.send_json(502, {"error": str(error)})

    return Handler


def start_webserver(config, market, tracker):
    try:
        server = ThreadingHTTPServer(
            (config.market_host, config.market_port),
            make_handler(market, tracker),
        )
    except OSError as error:
        logger.error("Web server failed to start: %s", error)
        return None

    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Market helper at http://%s:%s/", config.market_host, config.market_port)

    return server
