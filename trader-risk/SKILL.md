---
name: trader-risk
description: >-
  Risk Manager for EGX. Checks every proposed order in trader/orders.json
  against your limits before you enter it: max loss per trade and per day,
  trades per day, reward to risk, a stop on every entry, the Cairo entry
  window (10:15 to 14:00, earlier in Ramadan), EGX holidays and weekends
  (Friday and Saturday), position size, cash and your blocked list, and
  whether the stock is on EGX's same-day (T+0) list. PASS or BLOCK with the
  reasons, recorded with a fingerprint so /trader-broker only shows an order
  as ready if that exact order passed today. Use when the user says "check
  this trade", "is this within my limits", "risk check", or after /trader-budget.
argument-hint: "[--orders trader/orders.json] [--now HH:MM]"
---

# trader-risk

The last check before a ticket. Every rule is the user's own, from
`trader/config.json`. Amounts in EGP.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                      # writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-11-03 10:26"
python3 "${CLAUDE_SKILL_DIR}/check.py" --json --no-write
```

## What blocks an entry

| rule | blocks when |
| --- | --- |
| loss | qty x (entry - stop) is over `max_loss_per_trade_egp` |
| daily loss | today's max losses plus this one are over `max_daily_loss_egp` |
| trades | `max_trades_per_day` entries already today |
| stop | no stop, or a stop on the wrong side |
| reward:risk | under `min_reward_risk` |
| window | before 10:15, after 14:00 Cairo (13:00 on a 13:30 Ramadan close), or EGX closed (Friday, Saturday, a holiday) |
| same-day | the stock is not on your `same_day_list`. With an empty list it is a warning on every buy: EGX lets you sell a stock the day you buy it only if it is on EGX's intraday (T+0) list |
| size, cash, position, blocked, duplicate | the usual caps |

An exit (a sell of what you hold) is never blocked by the day limits:
getting out must always be possible.

## Steps

1. Run `check.py` after `/trader-budget --write`.
2. Say PASS or BLOCK for each order with the reasons, and every warning
   (especially same-day and the 15-minute data delay).
3. On PASS, hand off to `/trader-broker` for the ticket. On BLOCK, stop.

## Example

```
PASS  #1 buy COMI 306 shares, entry
          entry 122.41 (the plan's entry), stop 121.75, target 123.07: risk 0.66 a share
          max loss EGP 201.96 (limit EGP 500), reward:risk 1.00 (min 1)
        ~ same-day: check that COMI is on EGX's intraday (T+0) trading list before you buy ...
1 passed, 0 blocked at 2026-11-03 10:26 Cairo.
```

## What it never does

It never places, edits or approves an order, and never loosens a rule.

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
