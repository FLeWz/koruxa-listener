package main

// Theme: dark (default), light, or system. The choice is kept in the browser
// (localStorage "theme") and set on <html data-theme> before the page paints,
// so there is no flash. Pages use the CSS variables below. The button in the
// navigation bar cycles dark, light, system and shows an icon for the current one.

// The light palette is soft on purpose: grey-tinted surfaces instead of pure white.
const lightVars = `color-scheme: light;
    --bg: #e6e8ed; --text: #20242d; --surface: #eff1f5; --input-bg: #f6f7f9;
    --border: #cbd0da; --border-strong: #aeb5c2; --muted: #596173; --hover: #dde2ec;
    --link: #2a5fa0; --accent: #2d6cdf; --danger: #b53a2e; --ok: #217a47; --warn: #8a6600;`

const themeHead = `<script>(function () {
  var order = ["dark", "light", "system"];
  var names = {dark: "Dark", light: "Light", system: "System"};
  var svg = function (body) {
    return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
  };
  var icons = {
    dark: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
    light: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    system: svg('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>')
  };

  var theme = localStorage.getItem("theme");
  if (order.indexOf(theme) < 0) theme = "dark";
  document.documentElement.dataset.theme = theme;

  window.renderThemeButton = function () {
    var button = document.getElementById("theme");
    if (!button) return;
    var current = document.documentElement.dataset.theme;
    var next = order[(order.indexOf(current) + 1) % order.length];
    button.innerHTML = icons[current];
    button.title = "Theme: " + names[current] + " (click for " + names[next] + ")";
    button.setAttribute("aria-label", button.title);
  };

  window.cycleTheme = function () {
    var current = document.documentElement.dataset.theme;
    var next = order[(order.indexOf(current) + 1) % order.length];
    localStorage.setItem("theme", next);
    document.documentElement.dataset.theme = next;
    window.renderThemeButton();
  };
})();</script>
<style>
  :root, :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #15171c; --text: #dde; --surface: #1a1d24; --input-bg: #0e1014;
    --border: #2a2e38; --border-strong: #3a3f4b; --muted: #8a91a0; --hover: #222733;
    --link: #8ab; --accent: #2d6cdf; --danger: #e74c3c; --ok: #2ecc71; --warn: #f1c40f;
  }
  :root[data-theme="light"] {
    ` + lightVars + `
  }
  @media (prefers-color-scheme: light) {
    :root[data-theme="system"] {
      ` + lightVars + `
    }
  }
  button.theme-btn { display: inline-flex; align-items: center; justify-content: center; width: 2rem; height: 2rem; padding: 0; background: none; color: var(--muted); border: 1px solid var(--border-strong); border-radius: 6px; cursor: pointer; }
  button.theme-btn:hover { color: var(--text); border-color: var(--muted); }
</style>`

const themeButton = `<button type="button" id="theme" class="theme-btn" onclick="cycleTheme()"></button>` +
	`<script>renderThemeButton();</script>`
