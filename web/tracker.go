package main

import (
	"database/sql"
	"fmt"
	"math"
	"regexp"
	"strings"
)

var keyRE = regexp.MustCompile(`^[a-z0-9_]+$`)

const maxName = 64

// userErr is a bad request: the message is shown to the user.
type userError string

func (e userError) Error() string { return string(e) }

func userErr(msg string) error { return userError(msg) }

type notFoundError string

func (e notFoundError) Error() string { return string(e) }

// ---- request value helpers -------------------------------------------

// wholeNumber accepts an integral JSON number of at least min.
func wholeNumber(v any, label string, min int64) (int64, error) {
	f, ok := v.(float64)
	if !ok {
		return 0, userErr(label + " must be a number")
	}

	if math.IsNaN(f) || math.IsInf(f, 0) || f != math.Trunc(f) {
		return 0, userErr(label + " must be a whole number")
	}

	if int64(f) < min {
		return 0, userErr(fmt.Sprintf("%s must be at least %d", label, min))
	}

	return int64(f), nil
}

func itemKey(v any) (string, error) {
	s, ok := v.(string)
	if !ok || !keyRE.MatchString(s) {
		return "", userErr("Invalid item key")
	}

	return s, nil
}

// ---- API view --------------------------------------------------------

type PotionIngredient struct {
	ItemKey  string  `json:"item_key"`
	Name     string  `json:"name"`
	Quantity float64 `json:"quantity"`
}

type Potion struct {
	Key         string             `json:"key"`
	Label       string             `json:"label"`
	Amount      float64            `json:"amount"`       // items one craft gives; the ingredients are per craft
	YieldChance float64            `json:"yield_chance"` // double drop chance in percent
	Ingredients []PotionIngredient `json:"ingredients"`
}

// PoolIngredient is one ingredient of a player's pool, shared by all their requests of a skill.
type PoolIngredient struct {
	ItemKey   string   `json:"item_key"`
	Name      string   `json:"name"`
	Needed    *float64 `json:"needed"` // nil when an unlimited request uses it
	FromSeeds int64    `json:"from_seeds"`
	FromDrops int64    `json:"from_drops"`
	Have      int64    `json:"have"`
}

type Planting struct {
	ID        int64  `json:"id"`
	Planted   int64  `json:"planted"`
	Yield     *int64 `json:"yield"`
	InvBefore *int64 `json:"inv_before"` // own herbs in the inventory before the harvest
	InvAfter  *int64 `json:"inv_after"`  // ... and after it; with both set the yield is the difference
}

type Seed struct {
	ID            int64      `json:"id"`
	ItemKey       string     `json:"item_key"`
	Name          string     `json:"name"`
	TotalReceived int64      `json:"total_received"`
	Planted       int64      `json:"planted"`
	Left          int64      `json:"left"`
	Yield         int64      `json:"yield"`
	Pending       int64      `json:"pending"`
	Plantings     []Planting `json:"plantings"`
}

type Drop struct {
	ID       int64    `json:"id"`
	ItemKey  string   `json:"item_key"`
	Name     string   `json:"name"`
	Quantity int64    `json:"quantity"`
	Source   string   `json:"source"`
	Price    *float64 `json:"price"`
}

type Give struct {
	ID       int64  `json:"id"`
	Quantity int64  `json:"quantity"`
	Date     string `json:"date"`
}

type Order struct {
	ID        int64  `json:"id"`
	PotionKey string `json:"potion_key"`
	Potion    string `json:"potion"`
	Quantity  int64  `json:"quantity"`
	Given     int64  `json:"given"`
	Gives     []Give `json:"gives"`
	Possible  *int64 `json:"possible"`
	// Possible with the double drop chance counted.
	PossibleDouble *int64 `json:"possible_double"`
	Remaining      *int64 `json:"remaining"`
}

// Ingredients, seeds and drops belong to the player, not to a request: requests that share an
// ingredient draw on the same pool.
type PlayerOut struct {
	ID          int64            `json:"id"`
	Name        string           `json:"name"`
	Orders      []Order          `json:"orders"`
	Ingredients []PoolIngredient `json:"ingredients"`
	Seeds       []Seed           `json:"seeds"`
	Drops       []Drop           `json:"drops"`
}

type TrackerView struct {
	Skill   string      `json:"skill"`
	Potions []Potion    `json:"potions"`
	Players []PlayerOut `json:"players"`
}

// potions lists the recipes of the last refresh of a skill (potions, or the alchemy recipes).
// An order names its recipe by key: reward keys are not unique in alchemy. The fixed-price
// vial is left out of the ingredients.
func (a *App) potions(skill string) ([]Potion, error) {
	view, err := a.marketView(skill)
	if err != nil {
		return nil, err
	}

	potions := []Potion{}

	for _, r := range view.Recipes {
		p := Potion{Key: r.Key, Label: r.Label, Amount: r.Amount, YieldChance: r.YieldChance, Ingredients: []PotionIngredient{}}

		for _, i := range r.Ingredients {
			if _, fixed := fixedPrices[i.ItemKey]; !fixed {
				p.Ingredients = append(p.Ingredients, PotionIngredient{ItemKey: i.ItemKey, Name: i.Name, Quantity: i.Quantity})
			}
		}

		potions = append(potions, p)
	}

	return potions, nil
}

func nullInt(v sql.NullInt64) *int64 {
	if !v.Valid {
		return nil
	}

	return &v.Int64
}

func (a *App) trackerView(uid int64, skill string) (*TrackerView, error) {
	potions, err := a.potions(skill)
	if err != nil {
		return nil, err
	}

	byKey := map[string]Potion{}
	for _, p := range potions {
		byKey[p.Key] = p
	}

	view := &TrackerView{Skill: skill, Potions: potions, Players: []PlayerOut{}}

	// Gather everything first, then assemble per player and order.
	type orderRow struct {
		id, playerID, quantity int64
		potionKey              string
	}

	var players []PlayerOut
	var orders []orderRow

	rows, err := a.db.Query("SELECT id, name FROM players WHERE user_id = ? ORDER BY name", uid)
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		p := PlayerOut{Orders: []Order{}, Ingredients: []PoolIngredient{}, Seeds: []Seed{}, Drops: []Drop{}}
		if err := rows.Scan(&p.ID, &p.Name); err != nil {
			rows.Close()
			return nil, err
		}

		players = append(players, p)
	}

	rows.Close()

	rows, err = a.db.Query("SELECT id, player_id, potion_key, quantity FROM orders WHERE skill = ? AND player_id IN (SELECT id FROM players WHERE user_id = ?) ORDER BY id", skill, uid)
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var o orderRow
		if err := rows.Scan(&o.id, &o.playerID, &o.potionKey, &o.quantity); err != nil {
			rows.Close()
			return nil, err
		}

		orders = append(orders, o)
	}

	rows.Close()

	plantings := map[int64][]Planting{}

	rows, err = a.db.Query("SELECT id, seed_id, planted, harvested, inv_before, inv_after FROM plantings ORDER BY id")
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var seedID int64
		var p Planting
		var harvested, before, after sql.NullInt64

		if err := rows.Scan(&p.ID, &seedID, &p.Planted, &harvested, &before, &after); err != nil {
			rows.Close()
			return nil, err
		}

		p.Yield = nullInt(harvested)
		p.InvBefore = nullInt(before)
		p.InvAfter = nullInt(after)
		plantings[seedID] = append(plantings[seedID], p)
	}

	rows.Close()

	seeds := map[int64][]Seed{}

	rows, err = a.db.Query("SELECT id, player_id, item_key, total_received FROM seeds WHERE skill = ? ORDER BY id", skill)
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var playerID int64
		var s Seed

		if err := rows.Scan(&s.ID, &playerID, &s.ItemKey, &s.TotalReceived); err != nil {
			rows.Close()
			return nil, err
		}

		s.Name = itemName(s.ItemKey)
		s.Plantings = plantings[s.ID]

		if s.Plantings == nil {
			s.Plantings = []Planting{}
		}

		for _, p := range s.Plantings {
			s.Planted += p.Planted

			if p.Yield == nil {
				s.Pending++
			} else {
				s.Yield += *p.Yield
			}
		}

		// Harvests can give more seeds, so more can be planted than were received.
		s.Left = max(s.TotalReceived-s.Planted, 0)
		seeds[playerID] = append(seeds[playerID], s)
	}

	rows.Close()

	drops := map[int64][]Drop{}

	rows, err = a.db.Query("SELECT id, player_id, item_key, quantity, source, price FROM drops WHERE skill = ? ORDER BY id", skill)
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var playerID int64
		var d Drop
		var price sql.NullFloat64

		if err := rows.Scan(&d.ID, &playerID, &d.ItemKey, &d.Quantity, &d.Source, &price); err != nil {
			rows.Close()
			return nil, err
		}

		d.Name = itemName(d.ItemKey)

		if price.Valid {
			d.Price = ptr(price.Float64)
		}

		drops[playerID] = append(drops[playerID], d)
	}

	rows.Close()

	gives := map[int64][]Give{}

	rows, err = a.db.Query("SELECT id, order_id, quantity, created_at FROM potions_given ORDER BY id")
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var orderID int64
		var g Give

		if err := rows.Scan(&g.ID, &orderID, &g.Quantity, &g.Date); err != nil {
			rows.Close()
			return nil, err
		}

		gives[orderID] = append(gives[orderID], g)
	}

	rows.Close()

	ordersByPlayer := map[int64][]Order{}
	for _, p := range players {
		if list := seeds[p.ID]; list != nil {
			p.Seeds = list
		}

		if list := drops[p.ID]; list != nil {
			p.Drops = list
		}

		// Ingredient pool: every ingredient of the player's requests once, in first-seen order.
		pool := map[string]*PoolIngredient{}
		unlimited := map[string]bool{}

		for _, o := range orders {
			potion, known := byKey[o.potionKey]
			if o.playerID != p.ID || !known {
				continue
			}

			amount := math.Max(potion.Amount, 1)

			for _, ing := range potion.Ingredients {
				pi := pool[ing.ItemKey]

				if pi == nil {
					pi = &PoolIngredient{ItemKey: ing.ItemKey, Name: ing.Name, Needed: ptr(0.0)}
					pool[ing.ItemKey] = pi
					p.Ingredients = append(p.Ingredients, PoolIngredient{ItemKey: ing.ItemKey})
				}

				// Unlimited requests (quantity 0) have no fixed need.
				if o.quantity == 0 {
					unlimited[ing.ItemKey] = true
				} else {
					*pi.Needed += ing.Quantity * math.Ceil(float64(o.quantity)/amount)
				}
			}
		}

		for i := range p.Ingredients {
			pi := pool[p.Ingredients[i].ItemKey]

			for _, s := range p.Seeds {
				if s.ItemKey == pi.ItemKey {
					pi.FromSeeds += s.Yield
				}
			}

			for _, d := range p.Drops {
				if d.ItemKey == pi.ItemKey {
					pi.FromDrops += d.Quantity
				}
			}

			pi.Have = pi.FromSeeds + pi.FromDrops

			if unlimited[pi.ItemKey] {
				pi.Needed = nil
			}

			p.Ingredients[i] = *pi
		}

		for _, o := range orders {
			if o.playerID != p.ID {
				continue
			}

			potion, known := byKey[o.potionKey]

			out := Order{
				ID:        o.id,
				PotionKey: o.potionKey,
				Potion:    itemName(o.potionKey),
				Quantity:  o.quantity,
				Gives:     gives[o.id],
			}

			if known {
				out.Potion = potion.Label
			}

			if out.Gives == nil {
				out.Gives = []Give{}
			}

			for _, g := range out.Gives {
				out.Given += g.Quantity
			}

			// An order counts items, the ingredients are per craft, and a craft can give several
			// items (a crystal dust recipe gives 2 or 4). Output follows from the scarcest
			// ingredient of the pool; requests that share an ingredient each see all of it.
			amount := math.Max(potion.Amount, 1)

			for _, ing := range potion.Ingredients {
				if ing.Quantity <= 0 {
					continue
				}

				n := int64(math.Floor(float64(pool[ing.ItemKey].Have)/ing.Quantity) * amount)

				if out.Possible == nil || n < *out.Possible {
					d := int64(math.Floor(float64(n) * (1 + potion.YieldChance/100)))
					out.Possible, out.PossibleDouble = &n, &d
				}
			}

			if o.quantity > 0 {
				r := o.quantity - out.Given
				out.Remaining = &r
			} else if out.Possible != nil {
				r := *out.Possible - out.Given
				out.Remaining = &r
			}

			ordersByPlayer[p.ID] = append(ordersByPlayer[p.ID], out)
		}

		if list := ordersByPlayer[p.ID]; list != nil {
			p.Orders = list
		}

		view.Players = append(view.Players, p)
	}

	return view, nil
}

// ---- actions ---------------------------------------------------------

type actionFn func(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error)

var trackerActions = map[string]actionFn{
	"add_player":      actAddPlayer,
	"delete_player":   deleter("players", "Player"),
	"add_order":       actAddOrder,
	"add_given":       actAddGiven,
	"delete_given":    deleter("potions_given", "Entry"),
	"delete_order":    deleter("orders", "Request"),
	"add_seed":        actAddSeed,
	"set_seed_total":  actSetSeedTotal,
	"delete_seed":     deleter("seeds", "Seeds"),
	"add_planting":    actAddPlanting,
	"set_planting":    actSetPlanting,
	"delete_planting": deleter("plantings", "Planting"),
	"add_drop":        actAddDrop,
	"delete_drop":     deleter("drops", "Drop"),
}

func (a *App) trackerAction(uid int64, action string, body map[string]any, skill string) (any, error) {
	fn, ok := trackerActions[action]
	if !ok {
		return nil, notFoundError("Unknown action")
	}

	tx, err := a.db.Begin()
	if err != nil {
		return nil, err
	}

	defer tx.Rollback()

	result, err := fn(tx, uid, body, a, skill)
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, err
	}

	if result == nil {
		result = map[string]any{}
	}

	return result, nil
}

// Trackers are per user. ownedBy gives, for each tracker table, the condition (an id and a
// user id placeholder) that matches a row only when it belongs to that user: directly, or
// through its player and order. Every id that comes from the page goes through it, because
// ids are easy to guess. A row of someone else looks the same as a missing one.
const userOrders = "SELECT o.id FROM orders o JOIN players p ON p.id = o.player_id WHERE p.user_id = ?"

var ownedBy = map[string]string{
	"players":       "id = ? AND user_id = ?",
	"arcana_prices": "id = ? AND user_id = ?",
	"orders":        "id = ? AND player_id IN (SELECT id FROM players WHERE user_id = ?)",
	"seeds":         "id = ? AND player_id IN (SELECT id FROM players WHERE user_id = ?)",
	"plantings":     "id = ? AND seed_id IN (SELECT id FROM seeds WHERE player_id IN (SELECT id FROM players WHERE user_id = ?))",
	"drops":         "id = ? AND player_id IN (SELECT id FROM players WHERE user_id = ?)",
	"potions_given": "id = ? AND order_id IN (" + userOrders + ")",
}

func exists(tx *sql.Tx, uid int64, table string, id int64, label string) error {
	var one int

	err := tx.QueryRow("SELECT 1 FROM "+table+" WHERE "+ownedBy[table], id, uid).Scan(&one)
	if err == sql.ErrNoRows {
		return userErr(label + " not found")
	}

	return err
}

func deleter(table, label string) actionFn {
	return func(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
		id, err := wholeNumber(body["id"], "Id", 1)
		if err != nil {
			return nil, err
		}

		res, err := tx.Exec("DELETE FROM "+table+" WHERE "+ownedBy[table], id, uid)
		if err != nil {
			return nil, err
		}

		if n, _ := res.RowsAffected(); n == 0 {
			return nil, userErr(label + " not found")
		}

		return nil, nil
	}
}

func inserted(res sql.Result, err error) (any, error) {
	if err != nil {
		return nil, err
	}

	id, err := res.LastInsertId()

	return map[string]any{"id": id}, err
}

func actAddPlayer(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	name, ok := body["name"].(string)
	if !ok || strings.TrimSpace(name) == "" {
		return nil, userErr("Player name is required")
	}

	name = strings.Join(strings.Fields(name), " ")

	if len([]rune(name)) > maxName {
		return nil, userErr("Player name is too long")
	}

	res, err := tx.Exec("INSERT INTO players (user_id, name, created_at) VALUES (?, ?, ?)", uid, name, now())
	if err != nil && strings.Contains(err.Error(), "UNIQUE") {
		return nil, userErr("Player already exists")
	}

	return inserted(res, err)
}

func actAddOrder(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	playerID, err := wholeNumber(body["player_id"], "Player", 1)
	if err != nil {
		return nil, err
	}

	var quantity int64 = 1

	if v, has := body["quantity"]; has {
		// 0 = unlimited
		if quantity, err = wholeNumber(v, "Quantity", 0); err != nil {
			return nil, err
		}
	}

	potionKey, err := itemKey(body["potion_key"])
	if err != nil {
		return nil, err
	}

	if err := exists(tx, uid, "players", playerID, "Player"); err != nil {
		return nil, err
	}

	potions, err := a.potions(skill)
	if err != nil {
		return nil, err
	}

	var potion *Potion

	for i := range potions {
		if potions[i].Key == potionKey {
			potion = &potions[i]
		}
	}

	if potion == nil {
		return nil, userErr("Unknown recipe. Refresh the recipes first.")
	}

	// Herblore: every ingredient gets a seeds entry, so a new ingredient shows up in the
	// seeds section too. Entries that exist already are kept; unused ones can be deleted.
	if skill == "herblore" {
		for _, ing := range potion.Ingredients {
			if _, err := tx.Exec(
				"INSERT INTO seeds (player_id, skill, item_key, total_received, created_at) "+
					"SELECT ?, ?, ?, 0, ? WHERE NOT EXISTS (SELECT 1 FROM seeds WHERE player_id = ? AND skill = ? AND item_key = ?)",
				playerID, skill, ing.ItemKey, now(), playerID, skill, ing.ItemKey,
			); err != nil {
				return nil, err
			}
		}
	}

	return inserted(tx.Exec(
		"INSERT INTO orders (player_id, skill, potion_key, quantity, created_at) VALUES (?, ?, ?, ?, ?)",
		playerID, skill, potionKey, quantity, now(),
	))
}

func actAddGiven(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	orderID, err := wholeNumber(body["order_id"], "Request", 1)
	if err != nil {
		return nil, err
	}

	quantity, err := wholeNumber(body["quantity"], "Potions given", 1)
	if err != nil {
		return nil, err
	}

	var limit, total int64

	if err := tx.QueryRow("SELECT quantity FROM orders WHERE "+ownedBy["orders"], orderID, uid).Scan(&limit); err == sql.ErrNoRows {
		return nil, userErr("Request not found")
	} else if err != nil {
		return nil, err
	}

	if err := tx.QueryRow("SELECT COALESCE(SUM(quantity), 0) FROM potions_given WHERE order_id = ?", orderID).Scan(&total); err != nil {
		return nil, err
	}

	if limit > 0 && total+quantity > limit {
		return nil, userErr("Cannot give more than was requested")
	}

	return inserted(tx.Exec("INSERT INTO potions_given (order_id, quantity, created_at) VALUES (?, ?, ?)", orderID, quantity, now()))
}

func actAddSeed(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	playerID, err := wholeNumber(body["player_id"], "Player", 1)
	if err != nil {
		return nil, err
	}

	total, err := wholeNumber(body["total_received"], "Total received", 0)
	if err != nil {
		return nil, err
	}

	key, err := itemKey(body["item_key"])
	if err != nil {
		return nil, err
	}

	if err := exists(tx, uid, "players", playerID, "Player"); err != nil {
		return nil, err
	}

	return inserted(tx.Exec("INSERT INTO seeds (player_id, skill, item_key, total_received, created_at) VALUES (?, ?, ?, ?, ?)", playerID, skill, key, total, now()))
}

func actSetSeedTotal(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	id, err := wholeNumber(body["id"], "Id", 1)
	if err != nil {
		return nil, err
	}

	total, err := wholeNumber(body["total_received"], "Total received", 0)
	if err != nil {
		return nil, err
	}

	if err := exists(tx, uid, "seeds", id, "Seeds"); err != nil {
		return nil, err
	}

	_, err = tx.Exec("UPDATE seeds SET total_received = ? WHERE id = ?", total, id)

	return nil, err
}

// optionalCount reads an optional whole number (0 or more). null or a missing value is "not set".
func optionalCount(v any, label string) (sql.NullInt64, error) {
	if v == nil {
		return sql.NullInt64{}, nil
	}

	n, err := wholeNumber(v, label, 0)
	if err != nil {
		return sql.NullInt64{}, err
	}

	return sql.NullInt64{Int64: n, Valid: true}, nil
}

// inventoryYield is the yield that follows from the own herbs counted in the inventory before
// and after a harvest. ok is false until both counts are set.
func inventoryYield(before, after sql.NullInt64) (yield sql.NullInt64, ok bool, err error) {
	if !before.Valid || !after.Valid {
		return sql.NullInt64{}, false, nil
	}

	if after.Int64 < before.Int64 {
		return sql.NullInt64{}, false, userErr("Quantity after harvest is lower than the quantity before")
	}

	return sql.NullInt64{Int64: after.Int64 - before.Int64, Valid: true}, true, nil
}

func actAddPlanting(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	seedID, err := wholeNumber(body["seed_id"], "Seeds", 1)
	if err != nil {
		return nil, err
	}

	count, err := wholeNumber(body["planted"], "Planted", 1)
	if err != nil {
		return nil, err
	}

	harvested, err := optionalCount(body["yield"], "Yield")
	if err != nil {
		return nil, err
	}

	before, err := optionalCount(body["inv_before"], "Quantity before harvest")
	if err != nil {
		return nil, err
	}

	after, err := optionalCount(body["inv_after"], "Quantity after harvest")
	if err != nil {
		return nil, err
	}

	// With both inventory counts the yield is their difference, so it is not typed as well.
	if diff, ok, err := inventoryYield(before, after); err != nil {
		return nil, err
	} else if ok {
		if harvested.Valid {
			return nil, userErr("The yield is calculated from the quantities before and after the harvest")
		}

		harvested = diff
	}

	// Harvests can give more seeds, so planting is not limited by the seeds received.
	if err := exists(tx, uid, "seeds", seedID, "Seeds"); err != nil {
		return nil, err
	}

	return inserted(tx.Exec(
		"INSERT INTO plantings (seed_id, planted, harvested, inv_before, inv_after, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		seedID, count, harvested, before, after, now(),
	))
}

func actSetPlanting(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	id, err := wholeNumber(body["id"], "Id", 1)
	if err != nil {
		return nil, err
	}

	var count int64
	var harvested, before, after sql.NullInt64

	if err := tx.QueryRow(
		"SELECT planted, harvested, inv_before, inv_after FROM plantings WHERE "+ownedBy["plantings"], id, uid,
	).Scan(&count, &harvested, &before, &after); err == sql.ErrNoRows {
		return nil, userErr("Planting not found")
	} else if err != nil {
		return nil, err
	}

	_, wasDerived, _ := inventoryYield(before, after)

	if v, has := body["planted"]; has {
		if count, err = wholeNumber(v, "Planted", 1); err != nil {
			return nil, err
		}
	}

	touched := false

	if v, has := body["inv_before"]; has {
		touched = true

		if before, err = optionalCount(v, "Quantity before harvest"); err != nil {
			return nil, err
		}
	}

	if v, has := body["inv_after"]; has {
		touched = true

		if after, err = optionalCount(v, "Quantity after harvest"); err != nil {
			return nil, err
		}
	}

	diff, derived, err := inventoryYield(before, after)
	if err != nil {
		return nil, err
	}

	if v, has := body["yield"]; has {
		if derived {
			return nil, userErr("The yield is calculated from the quantities before and after the harvest")
		}

		if harvested, err = optionalCount(v, "Yield"); err != nil {
			return nil, err
		}
	}

	if touched {
		if derived {
			harvested = diff
		} else if wasDerived {
			// A count was cleared: the calculated yield is gone and the harvest is pending again.
			harvested = sql.NullInt64{}
		}
	}

	_, err = tx.Exec(
		"UPDATE plantings SET planted = ?, harvested = ?, inv_before = ?, inv_after = ? WHERE id = ?",
		count, harvested, before, after, id,
	)

	return nil, err
}

func actAddDrop(tx *sql.Tx, uid int64, body map[string]any, a *App, skill string) (any, error) {
	playerID, err := wholeNumber(body["player_id"], "Player", 1)
	if err != nil {
		return nil, err
	}

	quantity, err := wholeNumber(body["quantity"], "Quantity", 1)
	if err != nil {
		return nil, err
	}

	key, err := itemKey(body["item_key"])
	if err != nil {
		return nil, err
	}

	source, _ := body["source"].(string)
	if source != "given" && source != "bought" {
		return nil, userErr("Source must be given or bought")
	}

	var price sql.NullFloat64

	if v := body["price"]; v != nil {
		f, ok := v.(float64)
		if !ok || math.IsNaN(f) || math.IsInf(f, 0) || f < 0 {
			return nil, userErr("Price must be a non-negative number")
		}

		price = sql.NullFloat64{Float64: f, Valid: true}
	}

	if err := exists(tx, uid, "players", playerID, "Player"); err != nil {
		return nil, err
	}

	return inserted(tx.Exec("INSERT INTO drops (player_id, skill, item_key, quantity, source, price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", playerID, skill, key, quantity, source, price, now()))
}
