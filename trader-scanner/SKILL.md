---
name: trader-scanner
description: >-
  Chart Scanner for EGX. Scans every stock on the Egyptian Exchange for
  setups. Before the open (from daily closes): 20-day breakouts, bull flags,
  reclaims of the 20-day average, oversold (RSI under 30) and oversold
  bounces. During the session (from 5-minute bars): opening-range breakouts,
  the first close above the 10:00 to 10:15 Cairo range. Builds the stock list
  from TradingView's EGX screener, reads TradingView bars (15 minutes late),
  lists failed tickers by name, and writes trader/watchlist.json for
  /trader-news and /trader-entry. Use when the user says "scan the market",
  "what's breaking out", "find setups", "pre-market scan", "opening range
  breakouts", "what's oversold", or "run the scanner".
argument-hint: "[universe | daily | orb] [--symbols A,B] [--at HH:MM]"
---

# trader-scanner

A scan narrows about 290 EGX stocks to a short list worth looking at. It is a
list of charts that match a rule, not a list of stocks to buy.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                        # every EGX stock -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                           # before 10:00: daily setups
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols COMI,ETEL,HRHO
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                             # after 10:20 Cairo: breakouts so far
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:30 --limit 100
```

Flags: `--min-price` (1 EGP), `--min-volume` (daily: 100,000 shares a day on
average; orb: 0), `--limit N`, `--watchlist FILE`, `--offline FILE`,
`--now "2026-11-03 10:30"` (Cairo), `--json`, `--show N`.

## The rules

- **daily**, from complete daily bars (today's is dropped until 14:30):
  breakout_20d (the last close above every close of the 20 before it),
  bull_flag (up over 10 % in 10 sessions, then 5 closes inside a 5 % range),
  reclaim_ma20, oversold (RSI 14 under 30), oversold_bounce.
- **orb**, from 5-minute bars: the range is the 10:00, 10:05 and 10:10
  bars; a hit is the first completed bar after it to CLOSE above the range
  high. EGX is thin, so a chart counts when its 10:00 bar is there and at
  least 80 % of its bars up to the cutoff traded.
- Data from TradingView, about 50 tickers a connection, one attempt each.
  Every ticker that failed is listed by name.
- A breakout seen on the scanner happened at least 15 minutes earlier.

## Steps

1. Once: `scan.py universe`.
2. Before the open: `scan.py daily`. Give the user the hits, most traded
   first, and say these are charts, not picks.
3. After 10:20: `scan.py orb`. Pick one chart with the user, then
   `/trader-news TICKER` and `/trader-entry TICKER`.

## Example: 3 November 2026, 10:30 Cairo, the 60 most traded stocks

```
Opening-range breakout scan, Tue 2026-11-03 10:30 Cairo: 15-minute range (10:00 to 10:15), 5-minute bars, TradingView (15 min late).
Fetched 60 of 60 charts; 60 usable.
35 of 60 charts closed above their opening-range high by 10:30:
   ...
   10:25  COMI      EGP 122.41  range 121.51 to 122.00  +0.34% over
```

## What it never does

It never ranks a hit as a buy, never places anything, never drops a failed
ticker without saying so.

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
- If a website is blocked, name the exact host and stop. Do not retry in a loop.
