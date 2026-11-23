---
name: trader-exit
description: >-
  Exit Planner for EGX. Sets the stop and the target BEFORE you enter, so you
  know when to get out: the stop at the opening-range midpoint or low, a
  percent, a price or an ATR multiple; the target at 1R, 2R or a price; and a
  flat-by time (14:20 Cairo, ten minutes before the close). Prices in EGP to
  the EGX price step (0.01, or 0.001 under 1 EGP). Refuses a stop above the
  entry, a target below it, or a stop too tight to survive the spread. Writes
  trader/plan.json for the Time Machine, the budget and the alerts. Use when
  the user says "where's my stop", "set a stop", "what's my target", "plan the
  exit", "how much am I risking a share", or after /trader-entry triggers.
argument-hint: "[--stop or-mid|or-low|pct:X|price:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit

Every trade gets its exit before its entry: where it is wrong (the stop),
where it is done (the target), and when it ends no matter what (flat by
14:20). The user types the stop and target into the broker app; this tool
only does the arithmetic.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                                   # from trader/entry.json: or-mid, 2R
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol COMI --entry 122.41 --or-high 122.00 --or-low 121.51
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5 --offline COMI.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" show
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from `/trader-entry` (symbol, price, time, opening range) |
| `trader/config.json` | `flat_by` (14:20 Cairo) and `min_reward_risk` (1.0). Optional |
| TradingView via `trader-egx` | the session's bars, for an `atr` stop only |
| `trader/plan.json` | with `--write`: entry, stop, target, risk a share, reward:risk, flat_by |

## The rules

- A long's stop rounds DOWN to the price step, away from the entry; a target
  rounds half up.
- Risk a share = entry minus stop. Under 0.05 % of the price is refused: the
  spread alone would stop it out.
- Flat by 14:20 Cairo, win or lose. On a short Ramadan session (close 13:30)
  it moves up to 13:20. `/trader-alerts` warns 5 minutes before (14:15).
- A level setup has no opening range: give a stop.
- Longs only.

## Steps

1. After `/trader-entry` triggers, run `exit.py plan --write` (or with the
   user's stop and target).
2. Read the plan back in one line: entry, stop, target, risk a share, flat by.
3. Hand off: `/trader-timemachine` replays it, `/trader-budget` sizes it.

## Example: COMI, 3 November 2026

```
Exit plan: COMI long, Tue 2026-11-03, opening-range breakout on 5-minute bars
  Entry         EGP 122.41 at 10:25 (bar 4)
  Stop          EGP 121.75  opening-range midpoint: (EGP 122.00 + EGP 121.51) / 2 = 121.755, rounded down
  Target        EGP 123.07  1R (one times the risk)
  Risk a share  EGP 0.66 (0.54% of the entry)
  Flat by       14:20 Cairo, win or lose: no overnight hold (the sell warning comes 5+ minutes before)
```

## What it never does

It never places an order, never moves a stop further away, and never plans a short.

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
- A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
