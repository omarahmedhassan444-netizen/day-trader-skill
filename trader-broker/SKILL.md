---
name: trader-broker
description: >-
  Order Tickets for EGX, entered by hand. Your broker (Telda) has no API, so
  this skill never places anything: it prints each order that passed
  /trader-risk as a step-by-step ticket to type into the app (buy, stop-loss,
  take-profit, flat by 14:20), records your explicit yes for one ticket at a
  time, records what you did (placed, skipped, failed) and the fill, and logs
  paper trades without anything being entered. Also builds the sell ticket to
  get flat before the close. Use when the user says "place it", "buy it",
  "give me the ticket", "what do I enter in Telda", "I bought it", "record
  the fill", "get me out", or after /trader-risk passes.
argument-hint: "[show | approve --index N --reply \"yes\" | result | fill | flatten | log]"
---

# trader-broker

The last step, by hand. The ticket says exactly what to tap in the app;
the log keeps what was decided and filled, so the risk check knows today's
trades.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                          # every order, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed     # or skipped / failed
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol COMI --side buy --qty 306 --price 122.45
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol COMI --qty 306
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

## The flow

1. `show`. A ticket is READY only if `/trader-risk` passed THIS exact order
   today. Show the user the ticket word for word, including the 15-minute
   delay and every warning.
2. Ask for a yes for that one ticket. `approve` refuses "sure", "ok",
   "yes to all", or a yes that names another stock or amount.
3. Paper (`"paper": true`, the default): do NOT enter it in the app. Record
   `result --status placed`, then `fill` with the price the user would have
   paid. Real money: the user enters it in Telda following the steps, then
   `result --status placed` (or `skipped`, `failed`) and `fill` with the
   price the app shows.
4. While it is open: `/trader-alerts` warns at GET READY levels, the stop,
   the target, 5 minutes before flat-by and at flat-by. `flatten` prints the
   sell ticket.

## Example ticket

```
ORDER 1 of 1, PAPER: practice only, do NOT enter it in Telda
  BUY 306 COMI, market, good for today
  ...
  To enter it yourself:
    1. In Telda: BUY 306 shares of COMI, a MARKET order, good for today.
    2. Stop-loss: SELL 306 COMI if the price falls to 121.75. If Telda has no stop order for EGX,
       trader-alerts rings at 121.75 and you sell by hand.
    3. Take-profit: SELL 306 COMI at 123.07 (a limit order, or sell by hand when trader-alerts rings).
    4. Flat by 14:20 Cairo: sell whatever is still open (the warning comes 5+ minutes before).
    5. Check the live price in Telda first: the charts here are 15 minutes late.
```

## What it never does

It never connects to a broker, never asks for or stores a login, PIN, card
or account number, and never treats one yes as a yes for more than one ticket.

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
