---
name: trader-entry
description: >-
  Entry Timer for EGX. Watches a setup bar by bar and tells you the moment to
  get in, with a GET READY warning before it. Reads TradingView 1-minute or
  5-minute bars (15 minutes late), builds the opening range (the first 15
  minutes, 10:00 to 10:15 Cairo) and calls the entry on the first completed
  bar that CLOSES above the range high, or above a level you give it. Says
  get ready, waiting, triggered (with the time and the price), late, expired
  or not open yet, and writes trader/entry.json for /trader-exit. Long side
  only. It never places an order. Use when the user says "tell me when to get
  in", "watch COMI", "has it broken out", "entry signal", "time my entry",
  "check my watchlist", or after /trader-scanner and /trader-news.
argument-hint: "[TICKER] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. A
price that pokes above the high in the middle of a bar is not an entry. A bar
that CLOSES above it is. Before that, when the last close comes within 1 % of
the level, it says GET READY so the user can open the broker app and have the
ticket ready: the 5-minute warning.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check COMI                  # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check COMI --write          # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check COMI --setup level --level 123.50
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch COMI --until 14:00    # one line per closed bar, GET READY, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                   # every hit in trader/watchlist.json
```

Other flags: `--or-minutes N` (15), `--near-pct X` (GET READY within X %
under the level, 1.0; 0 turns it off), `--min-volume-x X`, `--json`,
`--offline FILE`, `--now "2026-11-03 10:25"` (Cairo) to replay a day.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits, for `watchlist` |
| `trader/config.json` | `no_entries_before` (10:15) and `no_entries_after` (14:00), Cairo time. Optional |
| TradingView via `trader-egx` | today's bars, 15 minutes late |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, price, level, range, delay |

## The rule

- **Opening range (orb).** The high and low of the 10:00, 10:05 and 10:10
  five-minute bars. The level to beat is the range high.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. The time is that bar's close (the 10:20 bar closes at 10:25).
- **GET READY** when the last close is within `--near-pct` (1 %) under the
  level. `watch` prints it once, before the trigger.
- **LATE.** In `watch`, a trigger with no GET READY at least 5 minutes before
  it is marked LATE. In `check`, a trigger more than one bar old is LATE.
  Either way: skip it, or recheck the live price.
- **The delay.** The bars are 15 minutes late, so a trigger the user sees
  happened at least 15 minutes ago. Say it every time.
- **EGX is thin.** A 5-minute stretch with no trades has no bar.
- **Time limits.** No entries before 10:15 or after 14:00. On a short Ramadan
  session (close 13:30) the cutoff moves up to 13:00.

## Steps

1. Get the symbol, or run `entry.py watchlist` after `/trader-scanner`.
   Check `/trader-news` first.
2. Run `entry.py check SYMBOL` and say the status in one line.
3. To wait for it, run `entry.py watch SYMBOL --until 14:00` in the
   background. Relay GET READY the moment it prints: that is the user's
   5-minute warning to open the broker app.
4. When it triggers, say the time and the price, that the price is 15 minutes
   old, and that nothing was placed. If it is LATE, say so first.
5. Hand off to `/trader-exit` to set the stop and target BEFORE anything is bought.

## Example: COMI, Tuesday 3 November 2026

```
10:15 Cairo  GET READY: within 0.07% of EGP 122.00. Open your broker app and have the ticket ready; ...
10:15 bar  closed EGP 121.99  EGP 0.01 under EGP 122.00
10:20 bar  closed EGP 122.41  TRIGGERED: closed above EGP 122.00 (+0.34%). Get in now if you take it: 10:25, EGP 122.41
Prices are 15 minutes late. Check the live price in your broker app before you act.
```

Ten minutes of warning: not LATE.

## What it never does

It never places an order, never sizes a position, never sets the stop, never
moves the level after the fact, and never counts a bar that has not closed.

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
