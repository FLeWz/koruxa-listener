USERS_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Users</title>
<style>
  body { font: 14px system-ui, sans-serif; background: #15171c; color: #dde; margin: 2rem auto; max-width: 900px; padding: 0 1rem; }
  h1 { font-weight: 600; }
  button { background: #2d6cdf; color: #fff; border: 0; border-radius: 4px; padding: .4rem .9rem; cursor: pointer; }
  button.danger { background: none; color: #e74c3c; padding: 0 .4rem; }
  input { background: #0e1014; color: #dde; border: 1px solid #3a3f4b; border-radius: 4px; padding: .3rem .5rem; width: 14rem; }
  table { border-collapse: collapse; margin: 1rem 0 2rem; width: 100%; }
  th, td { text-align: left; padding: .4rem .8rem .4rem 0; border-bottom: 1px solid #2a2e38; }
  .row { display: flex; gap: .6rem; align-items: center; margin: .6rem 0; }
  .muted { color: #8a91a0; }
  .ok { color: #2ecc71; }
  .pending { color: #f1c40f; }
  .err { color: #e74c3c; }
  nav { display: flex; gap: 1.5rem; padding-bottom: .6rem; border-bottom: 1px solid #2a2e38; }
  nav a { color: #8a91a0; text-decoration: none; padding: .2rem 0; border-bottom: 2px solid transparent; }
  nav a:hover { color: #dde; }
  nav a.active { color: #dde; border-bottom-color: #2d6cdf; }
</style>
</head>
<body>
<!--NAV-->
<h1>Users</h1>
<p class="muted">Add a username, then tell that person to open the login page and enter it. With no password set, they are asked to choose one. Resetting a password empties it so they register again.</p>

<div class="row">
  <input id="newUser" maxlength="32" placeholder="Username" autocomplete="off">
  <button id="add">Add user</button>
  <span id="status" class="muted"></span>
</div>

<table>
  <thead><tr><th>Username</th><th>Role</th><th>Password</th><th></th></tr></thead>
  <tbody id="users"></tbody>
</table>

<script>
const $ = (id) => document.getElementById(id);

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function status(text, err) {
  $("status").textContent = text;
  $("status").className = err ? "err" : "muted";
}

async function api(path, options) {
  const r = await fetch(path, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
  return body;
}

async function post(path, body) {
  return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
}

async function load() {
  const data = await api("/api/users");
  const body = $("users");
  body.replaceChildren();

  for (const u of data.users) {
    const tr = el("tr");
    tr.append(el("td", u.username));
    tr.append(el("td", u.is_admin ? "admin" : "user", "muted"));
    tr.append(el("td", u.has_password ? "set" : "not set (registration pending)", u.has_password ? "ok" : "pending"));

    const cell = el("td");
    if (!u.is_admin && u.has_password) {
      const b = el("button", "Reset password", "danger");
      b.addEventListener("click", async () => {
        if (!confirm("Reset the password of " + u.username + "? They must register a new one.")) return;
        try {
          await post("/api/users/reset", {id: u.id});
          status("Password reset");
          await load();
        } catch (e) {
          status(e.message, true);
        }
      });
      cell.append(b);
    }
    tr.append(cell);
    body.append(tr);
  }
}

$("add").addEventListener("click", async () => {
  try {
    await post("/api/users/add", {username: $("newUser").value});
    $("newUser").value = "";
    status("User added");
    await load();
  } catch (e) {
    status(e.message, true);
  }
});

load().catch((e) => status(e.message, true));
</script>
</body>
</html>
"""
