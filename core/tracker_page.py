TRACKER_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Herblore tracker</title>
<style>
  body { font: 14px system-ui, sans-serif; background: #15171c; color: #dde; margin: 2rem auto; max-width: 1100px; padding: 0 1rem; }
  h1, h2, h3 { font-weight: 600; }
  h3 { margin: 0 0 .5rem; }
  a { color: #8ab; }
  button { background: #2d6cdf; color: #fff; border: 0; border-radius: 4px; padding: .4rem .9rem; cursor: pointer; }
  button.link { background: none; color: #e74c3c; padding: 0 .4rem; }
  input, select { background: #0e1014; color: #dde; border: 1px solid #3a3f4b; border-radius: 4px; padding: .3rem .5rem; }
  input.n { width: 6rem; text-align: right; }
  input.name { width: 14rem; }
  table { border-collapse: collapse; margin: .5rem 0 1rem; }
  th, td { text-align: left; padding: .3rem .8rem .3rem 0; border-bottom: 1px solid #2a2e38; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .row { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; margin: .6rem 0; }
  .card { border: 1px solid #2a2e38; border-radius: 6px; padding: 1rem; margin-bottom: 1.2rem; background: #1a1d24; }
  .seed { border-left: 3px solid #3a3f4b; padding-left: .8rem; margin: .8rem 0; }
  .muted { color: #8a91a0; }
  .ok { color: #2ecc71; }
  .none { color: #e74c3c; }
  .err { color: #e74c3c; }
  nav { display: flex; gap: 1.5rem; padding-bottom: .6rem; border-bottom: 1px solid #2a2e38; }
  nav a { color: #8a91a0; text-decoration: none; padding: .2rem 0; border-bottom: 2px solid transparent; }
  nav a:hover { color: #dde; }
  nav a.active { color: #dde; border-bottom-color: #2d6cdf; }
</style>
</head>
<body>
<!--NAV-->
<h1>Herblore tracker</h1>
<p><span id="status" class="muted"></span></p>

<div class="row">
  <input id="newPlayer" class="name" type="text" maxlength="64" placeholder="Player name">
  <button id="addPlayer">Save player</button>
</div>

<div class="row">
  <label for="player">Player</label>
  <select id="player"></select>
  <button id="deletePlayer" class="link">Delete player</button>
</div>

<div id="request" class="row"></div>
<div id="orders"></div>

<script>
const skill = new URLSearchParams(location.search).get("skill") || "herblore";
const $ = (id) => document.getElementById(id);
const fmt = (n) => (n === null || n === undefined) ? "-" : Number(n).toLocaleString();

let data = null;
let playerId = Number(localStorage.getItem("tracker_player")) || null;

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function status(text, err) {
  const s = $("status");
  s.textContent = text ? " " + text : "";
  s.className = err ? "err" : "muted";
}

async function api(path, options) {
  const r = await fetch(path, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
  return body;
}

async function load() {
  data = await api("/api/tracker?skill=" + encodeURIComponent(skill));
  render();
}

// Send an action, reload on success. Returns the response or null.
async function act(action, body) {
  try {
    const result = await api("/api/tracker/" + action + "?skill=" + encodeURIComponent(skill), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    await load();
    status("Saved");
    return result;
  } catch (e) {
    status(e.message, true);
    return null;
  }
}

function button(text, onClick, cls) {
  const b = el("button", text, cls);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function numInput(placeholder, value) {
  const i = el("input", undefined, "n");
  i.type = "text";
  i.inputMode = "numeric";
  i.placeholder = placeholder;
  if (value !== undefined && value !== null) i.value = value;
  return i;
}

// Empty text is null, anything else is sent as a number and validated by the server.
const num = (input) => input.value.trim() === "" ? null : Number(input.value);

function select(options, selected) {
  const s = document.createElement("select");
  for (const [value, label] of options) {
    const o = el("option", label);
    o.value = value;
    if (value === selected) o.selected = true;
    s.append(o);
  }
  return s;
}

function confirmDelete(what, action, id) {
  return button("delete", () => { if (confirm("Delete " + what + "?")) act(action, {id}); }, "link");
}

function table(headers) {
  const t = el("table");
  const head = el("thead");
  const tr = el("tr");
  headers.forEach(([text, num]) => tr.append(el("th", text, num ? "num" : undefined)));
  head.append(tr);
  t.append(head, el("tbody"));
  return t;
}

function renderPlanting(p) {
  const tr = el("tr");
  tr.append(el("td", fmt(p.planted), "num"));

  const yieldInput = numInput("pending", p.yield);
  yieldInput.addEventListener("change", () => act("set_planting", {id: p.id, yield: num(yieldInput)}));
  const cell = el("td");
  cell.append(yieldInput);
  tr.append(cell);

  const del = el("td");
  del.append(confirmDelete("this planting", "delete_planting", p.id));
  tr.append(del);
  return tr;
}

function renderSeed(order, s) {
  const box = el("div", undefined, "seed");
  const head = el("div", undefined, "row");
  head.append(el("strong", s.name + " seeds"));
  head.append(el("span", "received " + fmt(s.total_received) + ", planted " + fmt(s.planted) +
    ", left " + fmt(s.left) + ", yield " + fmt(s.yield) + (s.pending ? " (" + s.pending + " pending)" : ""), "muted"));

  const total = numInput("total", s.total_received);
  total.addEventListener("change", () => act("set_seed_total", {id: s.id, total_received: num(total)}));
  head.append(total);
  head.append(confirmDelete("these seeds", "delete_seed", s.id));
  box.append(head);

  if (s.plantings.length) {
    const t = table([["Planted", true], ["Yield"], [""]]);
    s.plantings.forEach((p) => t.tBodies[0].append(renderPlanting(p)));
    box.append(t);
  }

  const form = el("div", undefined, "row");
  const planted = numInput("planted");
  const harvested = numInput("yield (optional)");
  form.append(planted, harvested, button("Add planting", () => act("add_planting", {
    seed_id: s.id, planted: num(planted), yield: num(harvested),
  })));
  box.append(form);
  return box;
}

function renderDrop(d) {
  const tr = el("tr");
  tr.append(el("td", d.name));
  tr.append(el("td", fmt(d.quantity), "num"));
  tr.append(el("td", d.source));
  tr.append(el("td", d.price === null ? "-" : fmt(d.price), "num"));
  const del = el("td");
  del.append(confirmDelete("this drop", "delete_drop", d.id));
  tr.append(del);
  return tr;
}

function renderOrder(o) {
  const card = el("div", undefined, "card");
  const title = el("div", undefined, "row");
  title.append(el("h3", o.potion + " x " + (o.quantity === 0 ? "unlimited" : fmt(o.quantity))));
  title.append(confirmDelete("this request", "delete_order", o.id));
  card.append(title);

  // Potions handed to the player. Unlimited requests: output follows from the ingredients.
  // Each entry is a newly handed-over batch; the total is their sum.
  const given = el("div", undefined, "row");
  given.append(el("strong", "Potions given: " + fmt(o.given)));
  if (o.quantity === 0) given.append(el("span", "can make " + fmt(o.possible), "muted"));
  given.append(el("span", "left to give " + fmt(o.remaining), (o.remaining !== null && o.remaining < 0 ? "none" : "muted")));
  card.append(given);

  const giveForm = el("div", undefined, "row");
  const giveQty = numInput("newly crafted");
  giveQty.style.width = "9rem";
  giveForm.append(giveQty, button("Add given", () => act("add_given", {order_id: o.id, quantity: num(giveQty)})));
  card.append(giveForm);

  const ingredientOptions = o.ingredients.map((i) => [i.item_key, i.name]);

  // Needed vs received per ingredient
  if (o.ingredients.length) {
    const t = table([["Ingredient"], ["Needed", true], ["From seeds", true], ["Drops", true], ["Have", true], ["Potions", true]]);
    for (const i of o.ingredients) {
      const tr = el("tr");
      tr.append(el("td", i.name));
      tr.append(el("td", fmt(i.needed), "num"));
      tr.append(el("td", fmt(i.from_seeds), "num"));
      tr.append(el("td", fmt(i.from_drops), "num"));
      const short = i.needed !== null && i.have < i.needed;
      tr.append(el("td", fmt(i.have), "num " + (i.needed === null ? "" : (short ? "none" : "ok"))));
      tr.append(el("td", fmt(i.potions), "num"));
      t.tBodies[0].append(tr);
    }
    card.append(t);
  }

  card.append(el("strong", "Seeds"));
  o.seeds.forEach((s) => card.append(renderSeed(o, s)));
  const seedForm = el("div", undefined, "row");
  const seedItem = select(ingredientOptions);
  const seedTotal = numInput("total received");
  seedForm.append(seedItem, seedTotal, button("Add seeds", () => act("add_seed", {
    order_id: o.id, item_key: seedItem.value, total_received: num(seedTotal),
  })));
  card.append(seedForm);

  card.append(el("strong", "Monster drops"));
  if (o.drops.length) {
    const t = table([["Item"], ["Qty", true], ["Source"], ["Unit price", true], [""]]);
    o.drops.forEach((d) => t.tBodies[0].append(renderDrop(d)));
    card.append(t);
  }
  const dropForm = el("div", undefined, "row");
  const dropItem = select(ingredientOptions);
  const dropQty = numInput("quantity");
  const dropSource = select([["given", "given by player"], ["bought", "bought"]]);
  const dropPrice = numInput("unit price (bought)");
  dropForm.append(dropItem, dropQty, dropSource, dropPrice, button("Add drops", () => act("add_drop", {
    order_id: o.id, item_key: dropItem.value, quantity: num(dropQty), source: dropSource.value, price: num(dropPrice),
  })));
  card.append(dropForm);
  return card;
}

function render() {
  const players = $("player");
  players.replaceChildren();
  for (const p of data.players) {
    const o = el("option", p.name);
    o.value = p.id;
    players.append(o);
  }

  if (!data.players.some((p) => p.id === playerId)) playerId = data.players.length ? data.players[0].id : null;
  if (playerId !== null) players.value = playerId;
  localStorage.setItem("tracker_player", playerId === null ? "" : playerId);

  const request = $("request");
  const orders = $("orders");
  request.replaceChildren();
  orders.replaceChildren();

  const player = data.players.find((p) => p.id === playerId);
  if (!player) {
    orders.append(el("p", "Add a player to start.", "muted"));
    return;
  }

  if (!data.potions.length) {
    orders.append(el("p", "No potions known. Press refresh on the Herblore market page first.", "none"));
  } else {
    const potion = select(data.potions.map((p) => [p.key, p.label]));
    const qty = numInput("0 = unlimited", 1);
    qty.style.width = "9rem";
    request.append(el("label", "Requested potion"), potion, qty,
      button("Add request", () => act("add_order", {player_id: player.id, potion_key: potion.value, quantity: num(qty)})));
  }

  if (!player.orders.length) orders.append(el("p", "No requests yet.", "muted"));
  player.orders.forEach((o) => orders.append(renderOrder(o)));
}

$("player").addEventListener("change", () => { playerId = Number($("player").value); render(); });

$("addPlayer").addEventListener("click", async () => {
  const input = $("newPlayer");
  const result = await act("add_player", {name: input.value});
  if (result) {
    input.value = "";
    playerId = result.id;
    render();
  }
});

$("deletePlayer").addEventListener("click", () => {
  const player = data && data.players.find((p) => p.id === playerId);
  if (player && confirm("Delete " + player.name + " and all their requests?")) act("delete_player", {id: player.id});
});

load().catch((e) => status(e.message, true));
</script>
</body>
</html>
"""
