package main

import "math"

// Arcana sell tracker: a list of sale prices, their sum and 20% of the sum.
//
// Prices are typed in millions (500.6 is 500.6M) and stored as whole gold, so the
// sum is exact. Every user has their own list, with no limit on its size.

const (
	goldPerMillion   = 1_000_000
	maxPriceMillions = 1_000_000 // a typo guard, not a limit on how many prices there are
	shareDivisor     = 5         // 20%
)

func (a *App) arcanaView(uid int64) (any, error) {
	rows, err := a.db.Query("SELECT id, price FROM arcana_prices WHERE user_id = ? ORDER BY id DESC", uid)
	if err != nil {
		return nil, err
	}

	defer rows.Close()

	prices := []map[string]int64{}

	var sum int64

	for rows.Next() {
		var id, gold int64

		if err := rows.Scan(&id, &gold); err != nil {
			return nil, err
		}

		sum += gold
		prices = append(prices, map[string]int64{"id": id, "gold": gold})
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	// Gold amounts; the page shows them in millions.
	return map[string]any{
		"prices": prices,
		"sum":    sum,
		"share":  (sum + shareDivisor/2) / shareDivisor,
	}, nil
}

// goldFromMillions turns a typed price into whole gold.
func goldFromMillions(v any) (int64, error) {
	f, ok := v.(float64)
	if !ok || math.IsNaN(f) || math.IsInf(f, 0) {
		return 0, userErr("Price must be a number")
	}

	if f <= 0 {
		return 0, userErr("Price must be more than 0")
	}

	if f > maxPriceMillions {
		return 0, userErr("Price is too large")
	}

	return int64(math.Round(f * goldPerMillion)), nil
}

func (a *App) arcanaAction(uid int64, action string, body map[string]any) (any, error) {
	tx, err := a.db.Begin()
	if err != nil {
		return nil, err
	}

	defer tx.Rollback()

	switch action {
	case "add":
		list, ok := body["prices"].([]any)
		if !ok || len(list) == 0 {
			return nil, userErr("Enter at least one price")
		}

		// All or nothing: one bad price rejects the whole batch.
		golds := make([]int64, 0, len(list))

		for _, v := range list {
			gold, err := goldFromMillions(v)
			if err != nil {
				return nil, err
			}

			golds = append(golds, gold)
		}

		for _, gold := range golds {
			if _, err := tx.Exec("INSERT INTO arcana_prices (user_id, price, created_at) VALUES (?, ?, ?)", uid, gold, now()); err != nil {
				return nil, err
			}
		}

	case "delete":
		if _, err := deleter("arcana_prices", "Price")(tx, uid, body, a, ""); err != nil {
			return nil, err
		}

	case "clear":
		if _, err := tx.Exec("DELETE FROM arcana_prices WHERE user_id = ?", uid); err != nil {
			return nil, err
		}

	default:
		return nil, notFoundError("Unknown action")
	}

	if err := tx.Commit(); err != nil {
		return nil, err
	}

	return a.arcanaView(uid)
}
