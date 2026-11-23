---
name: trader-alerts
description: >-
  Price Alerts and the 5-minute warnings for EGX. Rings when a price, a stop,
  a target or a time hits, and warns BEFORE each one: a GET READY halfway to
  the stop and halfway to the target, and a warning 5 minutes before the
  flat-by time (14:15 for 14:20 Cairo). Knows the EGX clock (Sunday to
  Thursday, 10:00 to 14:30, 13:30 in Ramadan, holidays). Quotes are
  TradingView's, 15 minutes late. Use when the user says "alert me", "warn me
  before", "ring at the stop", "remind me to sell before the close", "is the
  market open", "watch my trade", or after /trader-broker.
argument-hint: "[clock | quote TICKER | add --from-plan | check | list]"
---

# trader-alerts

The warnings that give the user time to act in the broker app. An alert is
information, not an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                       # is EGX open? (Cairo)
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote COMI --write          # quotes -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan             # every warning for the planned trade
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ETEL --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                       # ring what hit, once
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
```

## What add --from-plan sets (COMI: entry 122.41, stop 121.75, target 123.07, flat by 14:20)

| alert | at | says |
| --- | --- | --- |
| GET READY (stop side) | 122.08, halfway to the stop | open the broker app |
| stop | 121.75 | sell: the stop is hit |
| GET READY (target side) | 122.74, halfway to the target | open the broker app |
| target | 123.07 | take the profit |
| 5-minute warning | 14:15 Cairo | get the sell ready |
| flat by | 14:20 Cairo | sell what is still open |

On a short Ramadan session the time alerts move up with the close (13:15, 13:20).

## Notifications: Mac sound and your phone

Every ALERT pops up on the Mac with a sound. For your phone, run once:

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" notify-setup     # makes a private ntfy channel, saved in trader/config.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" notify-test      # a test alert to the Mac and the phone
```

Then install the free **ntfy** app on the phone and subscribe to the channel
it prints. Stop and flat-by alerts arrive as urgent. `/trader-entry watch`
also notifies on GET READY, TRIGGERED and LATE. The channel name is the only
key: anyone who knows it can read the alerts, so it is never shared. A failed
notification never stops the alerts: it is printed as a note.

## Steps

1. After the trade is entered (or logged as paper), run `add --from-plan`.
2. Keep one session watching: run `check` every minute or two (`/loop`) while
   the trade is open, and relay each ALERT at once.
3. Remind the user the quotes are 15 minutes late: if Telda offers a stop
   order, set the stop there too.

## What it never does

It never sells anything; it tells the user when to.

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
