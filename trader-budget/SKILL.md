---
name: trader-budget
description: >-
  Budget Manager for EGX. Sizes every day trade to your budget and your max
  loss per trade, in Egyptian pounds: whole shares, the smallest of the max
  loss (risk a share plus fees), the position cap, the max order and the cash
  after your buffer. Writes one order ticket (entry, stop-loss, take-profit)
  to trader/orders.json for /trader-risk and /trader-broker. Refuses zero
  shares, a reward:risk under your minimum, or another day's plan. Also
  splits a budget across a list of EGX stocks by weight. Use when the user
  says "how many shares", "size this trade", "what can I afford", "position
  size", "my max loss is 500", or after /trader-exit and /trader-timemachine.
argument-hint: "[--budget EGP] [--limit [PRICE]] [--write]"
---

# trader-budget

How many shares: the most that keeps the loss at the stop inside the max
loss per trade, and inside the position cap, the max order and the cash.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                          # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                  # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 122.40 --write   # a limit order at 122.40
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights COMI=40,ETEL=20,TMGH=20,HRHO=20 --budget 50000
```

## The config (`trader/config.json`, from `config.example.json`)

| key | example | meaning |
| --- | --- | --- |
| `budget` | 50000 | EGP for day trading |
| `max_loss_per_trade_egp` | 500 | the most one trade may lose at its stop |
| `max_daily_loss_egp` | 1500 | the most all of today's trades may lose |
| `max_position_pct`, `max_order_egp`, `cash_buffer_pct` | 75, 40000, 10 | size caps |
| `fee_pct` | 0.0 | your broker's commission plus EGX and tax charges, percent of each side |
| `no_entries_before`, `no_entries_after`, `flat_by` | 10:15, 14:00, 14:20 | Cairo time |
| `same_day_list` | [] | the EGX stocks you may sell the same day (T+0 list) |
| `broker`, `manual`, `paper` | telda, true, true | orders are entered by hand; paper first |

Older configs with `*_usd` keys still work: the amounts are read as EGP.

## Steps

1. Make sure `trader/config.json` exists (copy `config.example.json` and set
   the budget and the max loss with the user). Ask for `fee_pct` from their
   Telda fee schedule.
2. Run `size.py --write` and say: shares, cost, which limit binds, max loss
   at the stop.
3. Hand off to `/trader-risk`.

## Example: COMI, 3 November 2026 (entry 122.41, stop 121.75)

```
  max loss      EGP 500 max loss / 0.66 risk a share                       757 shares
  position cap  75% of EGP 50,000 = EGP 37,500 / 122.41                    306 shares  <- binds
Size: 306 shares of COMI = EGP 37,457.46 at 122.41. Max loss at the stop: 306 x 0.66 = EGP 201.96.
Proposed order (you enter it yourself in your broker app; nothing has been placed):
  buy 306 COMI, market, day, take-profit 123.07, stop-loss 121.75
```

## What it never does

It never places an order, never uses margin, and never sizes a short.

## Safety

- Not financial advice. This is software that reads public data and does arithmetic. A PASS is a rules
  check, not a recommendation. Most people who day trade lose money.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order. The broker (Telda) has no API: every order is a ticket the user
  types in by hand, and only after trader-risk passed that exact order today and the user said an explicit
  yes to that one ticket in the chat. Never ask for logins, PINs, card or account details.
- Prices are TradingView's free EGX feed, DELAYED 15 MINUTES. Say so on every signal and ticket, and tell
  the user to check the live price in their broker app before acting. Never present a delayed price as live.
- The 5-minute rule: every action gets a warning at least 5 minutes before the user has to act, whenever
  the clock or the price allows it. A trigger that came without one is LATE: skip it or recheck it.
- No options, margin, short selling or leverage. A day trade needs a stock on EGX's intraday (T+0) list,
  the only stocks that can be sold the same day they are bought.
