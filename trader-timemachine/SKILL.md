---
name: trader-timemachine
description: >-
  Time Machine for EGX. Replays a planned day trade before you risk a pound:
  10,000 simulated runs of the rest of the session from your entry, each
  5-minute bar drawn from the stock's own bars at that time of day over its
  last 60 EGX sessions, plus the same rule re-run on every one of those real
  sessions. Reports target first, stop first and still open at the close,
  the price range at the close, the average R, an honest one-line verdict and
  an ASCII fan chart. Same seed, same numbers. Use when the user says "what
  are the odds", "replay it", "time machine", "backtest this trade", "should
  I take it" or after /trader-exit.
argument-hint: "[--until 14:20] [--seed 19] [--replays 10000]"
---

# trader-timemachine

Before the money: what usually happens after an entry like this, on this
stock, at this time of day? Two answers: 10,000 simulated sessions built from
the stock's own recent 5-minute bars, and every past session where the same
rule actually fired.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                     # trader/plan.json, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write             # also trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 14:20       # end each replay at the flat-by time
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart                   # ASCII fan chart
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol COMI --date 2026-11-03 --entry 122.41 \
    --stop 121.75 --target 123.07 --entry-time 10:25
```

## How it works on EGX

- About 60 regular sessions of 5-minute bars (54 bars, 10:00 to 14:30 Cairo)
  from TradingView in one request.
- EGX is thinner than New York: a 5-minute stretch with no trades has no bar.
  A session counts when at least 90 % of its bars traded; each missing bar is
  filled flat at the previous close (no trade, no move) and the report says
  how many. Short Ramadan sessions and thin days are left out.
- The stop is checked before the target in every bar (the conservative call).
- Verdict: the target's share of the replays that hit a side, against the
  break-even share for the target's R (50 % at 1R, 33 % at 2R).

## Steps

1. After `/trader-exit`, run `timemachine.py run --until 14:20`.
2. Give the user the three numbers (target first, stop first, neither), the
   history line, and the verdict exactly as printed.
3. Say what it is: a reality check from the last 60 sessions, not a forecast.
4. Hand off to `/trader-budget`.

## Example: COMI, 3 November 2026 (entry 122.41, stop 121.75, target 123.07)

```
  target first   44.1 %
  stop first     55.1 %
  neither        0.8 %    still open at 14:30
History: the same rule ... on each of those 60 real sessions fired 30 times: 9 target, 16 stop, 5 neither.
Leans toward the stop: target first 44.1 % vs stop first 55.1 %.
88 of the pool's 3240 bars had no trades and were filled flat at the previous close.
```

An honest "leans toward the stop" is the point: look before you risk a pound.

## What it never does

It never predicts, never places anything, and never changes the plan.

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
- A simulation is not a prediction: earnings, news, EGX price limits and halts can do what the last 60
  sessions never did.
