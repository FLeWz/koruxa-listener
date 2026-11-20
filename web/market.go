package main

import (
	"database/sql"
	"encoding/json"
	"math"
	"sort"
	"strings"
	"unicode"
)

// Items that are not sold on the market.
var fixedPrices = map[string]float64{"vial_of_water": 50}

// itemName turns "vial_of_water" into "Vial Of Water".
func itemName(key string) string {
	var b strings.Builder

	prevLetter := false

	for _, r := range strings.ReplaceAll(key, "_", " ") {
		if unicode.IsLetter(r) {
			if prevLetter {
				b.WriteRune(unicode.ToLower(r))
			} else {
				b.WriteRune(unicode.ToUpper(r))
			}

			prevLetter = true
		} else {
			b.WriteRune(r)
			prevLetter = false
		}
	}

	return b.String()
}

// ---- stored recipes (written by the Python worker) -------------------

type ingredientIn struct {
	ItemKey  string  `json:"item_key"`
	Quantity float64 `json:"quantity"`
}

type recipeIn struct {
	Key         string         `json:"key"`
	Label       string         `json:"label"`
	Level       *float64       `json:"level"`
	CycleMs     *float64       `json:"cycle_ms"`
	YieldChance float64        `json:"yield_chance"`
	Amount      float64        `json:"amount"`
	RewardKey   string         `json:"reward_key"`
	Category    string         `json:"item_category"`
	Ingredients []ingredientIn `json:"ingredients"`
}

// ---- API view --------------------------------------------------------

type PriceInfo struct {
	Price       *float64 `json:"price"`
	ManualPrice *float64 `json:"manual_price"`
	AutoPrice   *float64 `json:"auto_price"`
}

type IngredientOut struct {
	ItemKey     string   `json:"item_key"`
	Name        string   `json:"name"`
	Quantity    float64  `json:"quantity"`
	Total       *float64 `json:"total"`
	ManualPrice *float64 `json:"manual_price"`
	AutoPrice   *float64 `json:"auto_price"`
	Price       *float64 `json:"price"`
}

type RecipeOut struct {
	Key               string          `json:"key"`
	Label             string          `json:"label"`
	Level             *float64        `json:"level"`
	Category          string          `json:"category"`
	Amount            float64         `json:"amount"`       // items one craft gives
	YieldChance       float64         `json:"yield_chance"` // double drop chance in percent
	RewardKey         string          `json:"reward_key"`
	Market            *PriceInfo      `json:"market"`
	UsesPotions       bool            `json:"uses_potions"`
	Ingredients       []IngredientOut `json:"ingredients"`
	Cost              *float64        `json:"cost"`
	CostDouble        *float64        `json:"cost_double"`
	Missing           int             `json:"missing"`
	CraftsPerHour     *float64        `json:"crafts_per_hour"`
	QtyPerHour        *float64        `json:"qty_per_hour"`
	QtyPerHourDouble  *float64        `json:"qty_per_hour_double"`
	GoldPerHour       *float64        `json:"gold_per_hour"`
	GoldPerHourDouble *float64        `json:"gold_per_hour_double"`
}

type ItemOut struct {
	ItemKey string `json:"item_key"`
	Name    string `json:"name"`
	PriceInfo
}

type MarketView struct {
	Skill     string      `json:"skill"`
	UpdatedAt *string     `json:"updated_at"`
	Recipes   []RecipeOut `json:"recipes"`
	Items     []ItemOut   `json:"items"`
	Potions   []ItemOut   `json:"potions"`
}

func ptr(v float64) *float64 { return &v }

// categoryRank orders the items of a skill: runes, then tools, then everything else.
func categoryRank(category string) int {
	switch category {
	case "runes":
		return 0
	case "tools":
		return 1
	}

	return 2
}

func (a *App) marketView(skill string) (*MarketView, error) {
	view := &MarketView{Skill: skill, Recipes: []RecipeOut{}, Items: []ItemOut{}, Potions: []ItemOut{}}

	var updated sql.NullString
	var recipesJSON string

	err := a.db.QueryRow("SELECT updated_at, recipes FROM skills WHERE skill = ?", skill).Scan(&updated, &recipesJSON)
	if err == sql.ErrNoRows {
		return view, nil
	}

	if err != nil {
		return nil, err
	}

	if updated.Valid {
		view.UpdatedAt = &updated.String
	}

	var recipes []recipeIn
	if err := json.Unmarshal([]byte(recipesJSON), &recipes); err != nil {
		return nil, err
	}

	market := map[string]*float64{}
	manual := map[string]float64{}

	rows, err := a.db.Query("SELECT item_key, price FROM market_prices WHERE skill = ?", skill)
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var key string
		var price sql.NullFloat64

		if err := rows.Scan(&key, &price); err != nil {
			rows.Close()
			return nil, err
		}

		if price.Valid {
			market[key] = ptr(price.Float64)
		}
	}

	rows.Close()

	rows, err = a.db.Query("SELECT item_key, price FROM manual_prices")
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var key string
		var price float64

		if err := rows.Scan(&key, &price); err != nil {
			rows.Close()
			return nil, err
		}

		manual[key] = price
	}

	rows.Close()

	autoPrice := func(key string) *float64 {
		if v, ok := fixedPrices[key]; ok {
			return ptr(v)
		}

		return market[key]
	}

	// Your price wins over the market price.
	priceInfo := func(key string) PriceInfo {
		auto := autoPrice(key)
		info := PriceInfo{Price: auto, AutoPrice: auto}

		if v, ok := manual[key]; ok {
			info.ManualPrice = ptr(v)
			info.Price = ptr(v)
		}

		return info
	}

	rewardOf := func(r recipeIn) string {
		if r.RewardKey != "" {
			return r.RewardKey
		}

		return r.Key
	}

	byReward := map[string]recipeIn{}
	for _, r := range recipes {
		byReward[rewardOf(r)] = r
	}

	// One craft can give more than one item (a rune recipe gives 2).
	amountOf := func(r recipeIn) float64 {
		if r.Amount > 0 {
			return r.Amount
		}

		return 1
	}

	// Your price, else market/fixed, else craft price when the item is a potion.
	var unitPrice func(key string, stack []string) *float64

	unitPrice = func(key string, stack []string) *float64 {
		if p := priceInfo(key).Price; p != nil {
			return p
		}

		recipe, ok := byReward[key]
		if !ok {
			return nil
		}

		for _, s := range stack {
			if s == key {
				return nil
			}
		}

		total := 0.0

		for _, ing := range recipe.Ingredients {
			part := unitPrice(ing.ItemKey, append(append([]string{}, stack...), key))
			if part == nil {
				return nil
			}

			total += *part * ing.Quantity
		}

		// Per item: the ingredients of one craft are shared by everything it gives.
		total /= amountOf(recipe)

		return &total
	}

	itemKeys := map[string]bool{}

	for _, recipe := range recipes {
		out := RecipeOut{Key: recipe.Key, Label: recipe.Label, Level: recipe.Level, Category: recipe.Category, Ingredients: []IngredientOut{}}

		if out.Label == "" {
			out.Label = recipe.Key
		}

		cost := 0.0
		missing := 0

		for _, ing := range recipe.Ingredients {
			info := priceInfo(ing.ItemKey)
			price := unitPrice(ing.ItemKey, nil)
			itemKeys[ing.ItemKey] = true

			var total *float64
			if price != nil {
				total = ptr(*price * ing.Quantity)
				cost += *total
			} else {
				missing++
			}

			out.Ingredients = append(out.Ingredients, IngredientOut{
				ItemKey:     ing.ItemKey,
				Name:        itemName(ing.ItemKey),
				Quantity:    ing.Quantity,
				Total:       total,
				ManualPrice: info.ManualPrice,
				AutoPrice:   info.AutoPrice,
				Price:       price,
			})
		}

		reward := rewardOf(recipe)
		out.RewardKey = reward
		out.Amount = amountOf(recipe)
		out.YieldChance = recipe.YieldChance
		out.Missing = missing

		if reward != "" {
			info := priceInfo(reward)
			out.Market = &info
		}

		for _, ing := range out.Ingredients {
			if _, ok := byReward[ing.ItemKey]; ok {
				out.UsesPotions = true
			}
		}

		// costPtr is the ingredient cost of one craft (used for gold per hour). Cost and
		// CostDouble are per item, so they compare with the item's market price.
		var costPtr *float64
		if missing == 0 {
			costPtr = ptr(cost)
			out.Cost = ptr(cost / amountOf(recipe))
		}

		// Per-hour numbers use your current bonuses as reported by the skill API.
		sell := priceInfo(reward).Price
		amount := amountOf(recipe)

		if recipe.CycleMs != nil && *recipe.CycleMs != 0 {
			crafts := 3_600_000 / *recipe.CycleMs
			qty := crafts * amount
			qtyDouble := qty * (1 + recipe.YieldChance/100)

			out.CraftsPerHour = ptr(crafts)
			out.QtyPerHour = ptr(qty)
			out.QtyPerHourDouble = ptr(qtyDouble)

			if costPtr != nil {
				out.CostDouble = ptr(*out.Cost / (1 + recipe.YieldChance/100))

				if sell != nil {
					out.GoldPerHour = ptr(qty**sell - crafts**costPtr)
					out.GoldPerHourDouble = ptr(qtyDouble**sell - crafts**costPtr)
				}
			}
		}

		view.Recipes = append(view.Recipes, out)
	}

	// Runes first, then tools, then the rest; each group by level. The tables that list the
	// items follow this order.
	sort.SliceStable(view.Recipes, func(i, j int) bool {
		a, b := view.Recipes[i], view.Recipes[j]

		if ra, rb := categoryRank(a.Category), categoryRank(b.Category); ra != rb {
			return ra < rb
		}

		if (a.Level == nil) != (b.Level == nil) {
			return a.Level != nil
		}

		if a.Level != nil && *a.Level != *b.Level {
			return *a.Level < *b.Level
		}

		return a.Label < b.Label
	})

	// Potions are listed in their own table, not as ingredients.
	potionKeys := map[string]bool{}

	for _, r := range view.Recipes {
		potionKeys[r.RewardKey] = true
		view.Potions = append(view.Potions, ItemOut{ItemKey: r.RewardKey, Name: r.Label, PriceInfo: priceInfo(r.RewardKey)})
	}

	keys := []string{}

	for key := range itemKeys {
		if !potionKeys[key] {
			keys = append(keys, key)
		}
	}

	sort.Strings(keys)

	for _, key := range keys {
		view.Items = append(view.Items, ItemOut{ItemKey: key, Name: itemName(key), PriceInfo: priceInfo(key)})
	}

	return view, nil
}

// setManualPrice stores or (nil) removes a user-entered price.
func (a *App) setManualPrice(key string, price *float64) error {
	if !keyRE.MatchString(key) {
		return userErr("Invalid item key")
	}

	if price == nil {
		_, err := a.db.Exec("DELETE FROM manual_prices WHERE item_key = ?", key)
		return err
	}

	if math.IsNaN(*price) || math.IsInf(*price, 0) || *price < 0 {
		return userErr("Price must be a non-negative number")
	}

	_, err := a.db.Exec("INSERT OR REPLACE INTO manual_prices (item_key, price) VALUES (?, ?)", key, *price)

	return err
}
