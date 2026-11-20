package main

import (
	"database/sql"
	"embed"
	"encoding/json"
	"errors"
	"html"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

//go:embed pages/*.html
var pagesFS embed.FS

const (
	cookieName   = "session"
	defaultSkill = "herblore"
	maxBody      = 4096
)

var skillRE = regexp.MustCompile(`^[a-z_]+$`)

var securityHeaders = map[string]string{
	"X-Content-Type-Options":  "nosniff",
	"X-Frame-Options":         "DENY",
	"Referrer-Policy":         "same-origin",
	"Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'",
}

const navStyle = `<style>
  html { scrollbar-gutter: stable; }
  nav { display: flex; gap: 1.5rem; align-items: center; padding: .7rem 1.5rem; background: var(--surface); border-bottom: 1px solid var(--border); font: 14px system-ui, sans-serif; }
  nav a { color: var(--muted); text-decoration: none; padding: .2rem 0; border-bottom: 2px solid transparent; }
  nav a:hover { color: var(--text); }
  nav a.active { color: var(--text); border-bottom-color: var(--accent); }
  nav .right { margin-left: auto; display: flex; gap: .8rem; align-items: center; color: var(--muted); }
  nav button.logout { background: none; border: 1px solid var(--border-strong); color: var(--muted); border-radius: 4px; padding: .15rem .6rem; cursor: pointer; }
  nav button.logout:hover { color: var(--text); }
</style>`

// The Arcana market is the same page as the Herblore market, for another skill.
const arcanaMarketPath = "/?skill=arcana"

const refreshButton = `<button id="refresh">Refresh market data</button>`

type App struct {
	db     *sql.DB
	auth   *Auth
	worker *Worker
}

// Request state for one call.
type req struct {
	app   *App
	w     http.ResponseWriter
	r     *http.Request
	user  *User
	token string
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func (a *App) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	sw := &statusWriter{ResponseWriter: w, status: 200}

	for name, value := range securityHeaders {
		sw.Header().Set(name, value)
	}

	sw.Header().Set("Cache-Control", "no-store")

	q := &req{w: sw, r: r}

	if c, err := r.Cookie(cookieName); err == nil {
		q.token = c.Value
		q.user = a.auth.userForToken(c.Value)
	}

	a.route(q)

	logf("INFO", "%s %s %s %d %s", clientIP(r), r.Method, r.URL.Path, sw.status, time.Since(start).Round(time.Millisecond))
}

func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}

	return host
}

// ---- responses -------------------------------------------------------

func (q *req) cookie(token string, maxAge int) string {
	c := &http.Cookie{Name: cookieName, Value: token, Path: "/", MaxAge: maxAge, HttpOnly: true, SameSite: http.SameSiteLaxMode}

	// Behind an HTTPS reverse proxy the cookie must not travel over plain HTTP.
	c.Secure = q.r.Header.Get("X-Forwarded-Proto") == "https"

	return c.String()
}

func (q *req) json(status int, payload any) {
	// A valid session is renewed on every visit.
	if q.user != nil && q.token != "" {
		q.w.Header().Add("Set-Cookie", q.cookie(q.token, sessionSeconds))
	}

	q.w.Header().Set("Content-Type", "application/json")
	q.w.WriteHeader(status)
	json.NewEncoder(q.w).Encode(payload)
}

func (q *req) fail(status int, msg string) { q.json(status, map[string]string{"error": msg}) }

func (q *req) redirect(location string) {
	q.w.Header().Set("Location", location)
	q.w.WriteHeader(http.StatusFound)
}

func (q *req) page(name, active string, refresh bool) {
	raw, err := pagesFS.ReadFile("pages/" + name + ".html")
	if err != nil {
		q.fail(500, "Server error")
		return
	}

	page := string(raw)
	page = strings.Replace(page, "<!--THEME-->", themeHead, 1)
	page = strings.Replace(page, "<!--THEMEBUTTON-->", themeButton, 1)
	page = strings.Replace(page, "<!--NAV-->", navHTML(active, q.user), 1)

	if refresh {
		page = strings.Replace(page, "<!--REFRESH-->", refreshButton, 1)
	} else {
		page = strings.Replace(page, "<!--REFRESH-->", "", 1)
	}

	if q.user != nil && q.token != "" {
		q.w.Header().Add("Set-Cookie", q.cookie(q.token, sessionSeconds))
	}

	q.w.Header().Set("Content-Type", "text/html; charset=utf-8")
	q.w.Write([]byte(page))
}

// navHTML is the top navigation bar. The Users link is shown to the admin only.
func navHTML(active string, user *User) string {
	links := [][2]string{{"/", "Herblore market"}, {arcanaMarketPath, "Arcana market"}, {"/tracker", "Herblore tracker"}, {"/arcana", "Arcana sell tracker"}}

	if user != nil && user.IsAdmin {
		links = append(links, [2]string{"/admin", "Users"})
	}

	var b strings.Builder

	b.WriteString(navStyle + "<nav>")

	for _, l := range links {
		class := ""
		if l[0] == active {
			class = ` class="active"`
		}

		b.WriteString(`<a href="` + l[0] + `"` + class + `>` + l[1] + `</a>`)
	}

	name := ""
	if user != nil {
		name = user.Username
	}

	b.WriteString(`<span class="right">` + themeButton + `<span>` + html.EscapeString(name) + `</span>`)
	b.WriteString(`<button class="logout" onclick="fetch('/api/logout',{method:'POST'}).then(()=>location.href='/login')">Log out</button></span></nav>`)

	return b.String()
}

// ---- request helpers -------------------------------------------------

func (q *req) body() (map[string]any, error) {
	q.r.Body = http.MaxBytesReader(q.w, q.r.Body, maxBody)

	var body map[string]any

	if err := json.NewDecoder(q.r.Body).Decode(&body); err != nil || body == nil {
		return nil, userErr("Invalid request body")
	}

	return body, nil
}

func (q *req) skill() (string, error) {
	skill := q.r.URL.Query().Get("skill")

	if skill == "" {
		return defaultSkill, nil
	}

	if !skillRE.MatchString(skill) {
		return "", userErr("Invalid skill")
	}

	return skill, nil
}

func (q *req) sameOrigin() bool {
	origin := q.r.Header.Get("Origin")
	if origin == "" {
		return true
	}

	u, err := url.Parse(origin)

	return err == nil && u.Host == q.r.Host
}

// requireUser is true when logged in. Otherwise it answers: redirect for pages, 401 for the API.
func (q *req) requireUser(page bool) bool {
	if q.user != nil {
		return true
	}

	if page {
		q.redirect("/login")
	} else {
		q.fail(401, "Login required")
	}

	return false
}

func (q *req) requireAdmin(page bool) bool {
	if !q.requireUser(page) {
		return false
	}

	if q.user.IsAdmin {
		return true
	}

	if page {
		q.redirect("/")
	} else {
		q.fail(403, "Admin only")
	}

	return false
}

// handleErr maps an error to a response. Details stay in the log for unexpected errors.
func (q *req) handleErr(err error) {
	var ae *AuthError

	switch {
	case errors.As(err, &ae):
		q.fail(ae.Status, ae.Msg)
	case errors.Is(err, errWorkerDown):
		q.fail(503, err.Error())
	case isType[userError](err):
		q.fail(400, err.Error())
	case isType[notFoundError](err):
		q.fail(404, err.Error())
	default:
		logf("ERROR", "%s %s: %v", q.r.Method, q.r.URL.Path, err)
		q.fail(500, "Server error")
	}
}

func isType[T error](err error) bool {
	var target T

	return errors.As(err, &target)
}

func (q *req) startSession(userID int64) {
	token, err := q.app.auth.createSession(userID)
	if err != nil {
		q.handleErr(err)
		return
	}

	q.w.Header().Add("Set-Cookie", q.cookie(token, sessionSeconds))
	q.json(200, map[string]string{"status": "ok"})
}

// ---- routes ----------------------------------------------------------

func (a *App) route(q *req) {
	q.app = a

	switch q.r.Method {
	case http.MethodGet, http.MethodHead:
		a.get(q)
	case http.MethodPost:
		a.post(q)
	default:
		q.fail(405, "Method not allowed")
	}
}

func (a *App) get(q *req) {
	path := q.r.URL.Path

	switch path {
	case "/login":
		if q.user != nil {
			q.redirect("/")
		} else {
			q.page("login", "", false)
		}

	case "/api/session":
		var user any

		if q.user != nil {
			user = map[string]any{"username": q.user.Username, "is_admin": q.user.IsAdmin}
		}

		q.json(200, map[string]any{"setup": a.auth.needsSetup(), "user": user})

	case "/":
		if q.requireUser(true) {
			active := "/"
			if q.r.URL.Query().Get("skill") == "arcana" {
				active = arcanaMarketPath
			}

			q.page("market", active, q.user.IsAdmin)
		}

	case "/tracker":
		if q.requireUser(true) {
			q.page("tracker", "/tracker", false)
		}

	case "/arcana":
		if q.requireUser(true) {
			q.page("arcana", "/arcana", false)
		}

	case "/api/arcana":
		if q.requireUser(false) {
			view, err := a.arcanaView(q.user.ID)
			if err != nil {
				q.handleErr(err)
				return
			}

			q.json(200, view)
		}

	case "/admin":
		if q.requireAdmin(true) {
			q.page("users", "/admin", false)
		}

	case "/api/users":
		if q.requireAdmin(false) {
			users, err := a.auth.listUsers()
			if err != nil {
				q.handleErr(err)
				return
			}

			q.json(200, map[string]any{"users": users})
		}

	case "/api/data", "/api/tracker":
		if !q.requireUser(false) {
			return
		}

		skill, err := q.skill()
		if err != nil {
			q.handleErr(err)
			return
		}

		if path == "/api/data" {
			view, err := a.marketView(skill)
			if err != nil {
				q.handleErr(err)
				return
			}

			q.json(200, view)
		} else {
			view, err := a.trackerView(q.user.ID, skill)
			if err != nil {
				q.handleErr(err)
				return
			}

			q.json(200, view)
		}

	case "/api/refresh/status":
		if !q.requireAdmin(false) {
			return
		}

		skill, err := q.skill()
		if err != nil {
			q.handleErr(err)
			return
		}

		status, err := a.worker.refreshStatus(skill)
		if err != nil {
			q.handleErr(err)
			return
		}

		q.json(200, status)

	default:
		q.fail(404, "Not found")
	}
}

func (a *App) post(q *req) {
	path := q.r.URL.Path

	if !q.sameOrigin() {
		q.fail(403, "Cross-origin request refused")
		return
	}

	client := clientIP(q.r)

	switch {
	case path == "/api/login":
		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		state, id, err := a.auth.login(str(body["username"]), str(body["password"]), client)
		if err != nil {
			q.handleErr(err)
		} else if state == "set_password" {
			q.json(200, map[string]string{"status": "set_password"})
		} else {
			q.startSession(id)
		}

	case path == "/api/register":
		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		id, err := a.auth.register(str(body["username"]), str(body["password"]), client)
		if err != nil {
			q.handleErr(err)
		} else {
			q.startSession(id)
		}

	case path == "/api/setup":
		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		id, err := a.auth.setup(str(body["username"]), str(body["password"]))
		if err != nil {
			q.handleErr(err)
		} else {
			q.startSession(id)
		}

	case path == "/api/logout":
		a.auth.deleteSession(q.token)
		q.user = nil
		q.w.Header().Add("Set-Cookie", q.cookie("", -1))
		q.json(200, map[string]bool{"ok": true})

	case path == "/api/refresh":
		if !q.requireAdmin(false) {
			return
		}

		skill, err := q.skill()
		if err != nil {
			q.handleErr(err)
			return
		}

		status, err := a.worker.startRefresh(skill)
		if err != nil {
			q.handleErr(err)
			return
		}

		q.json(202, status)

	case path == "/api/users/add":
		if !q.requireAdmin(false) {
			return
		}

		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		id, err := a.auth.addUser(str(body["username"]))
		if err != nil {
			q.handleErr(err)
		} else {
			q.json(200, map[string]int64{"id": id})
		}

	case path == "/api/users/reset":
		if !q.requireAdmin(false) {
			return
		}

		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		id, err := wholeNumber(body["id"], "Id", 1)
		if err == nil {
			err = a.auth.resetPassword(id)
		}

		if err != nil {
			q.handleErr(err)
		} else {
			q.json(200, map[string]bool{"ok": true})
		}

	case path == "/api/price":
		if !q.requireUser(false) {
			return
		}

		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		var price *float64

		if v := body["price"]; v != nil {
			f, ok := v.(float64)
			if !ok {
				q.handleErr(userErr("Price must be a number"))
				return
			}

			price = &f
		}

		key, _ := body["item_key"].(string)

		if err := a.setManualPrice(key, price); err != nil {
			q.handleErr(err)
		} else {
			q.json(200, map[string]bool{"ok": true})
		}

	case strings.HasPrefix(path, "/api/arcana/"):
		if !q.requireUser(false) {
			return
		}

		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		result, err := a.arcanaAction(q.user.ID, strings.TrimPrefix(path, "/api/arcana/"), body)
		if err != nil {
			q.handleErr(err)
		} else {
			q.json(200, result)
		}

	case strings.HasPrefix(path, "/api/tracker/"):
		if !q.requireUser(false) {
			return
		}

		skill, err := q.skill()
		if err != nil {
			q.handleErr(err)
			return
		}

		body, err := q.body()
		if err != nil {
			q.handleErr(err)
			return
		}

		result, err := a.trackerAction(q.user.ID, strings.TrimPrefix(path, "/api/tracker/"), body, skill)
		if err != nil {
			q.handleErr(err)
		} else {
			q.json(200, result)
		}

	default:
		q.fail(404, "Not found")
	}
}

func str(v any) string {
	s, _ := v.(string)
	return s
}
