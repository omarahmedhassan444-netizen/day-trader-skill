---
name: trader-egx
description: >-
  EGX Data. The one place every tool in this pack gets its Egyptian Exchange
  data from: TradingView prices (1-minute to weekly bars), quotes and
  fundamentals, the EGX session calendar (Sunday to Thursday, 10:00 to 14:30
  Cairo, 13:30 in Ramadan, EGX holidays), the list of every EGX stock, and
  per-stock headlines. Says how late the data is (15 minutes without a
  TradingView login). Use when the user asks "is EGX open", "what time does
  the market open", "is tomorrow a holiday", "get me COMI's price", "how late
  is the data", "refresh the holidays", or when another trader skill needs EGX
  data and something looks wrong.
argument-hint: "[status | chart TICKER | quote TICKER | universe | calendar]"
---

# trader-egx

Every other trader skill imports `egx_data.py` from this folder. All price
fetching goes through it, so there is one place that knows the EGX calendar,
Cairo time and the data's delay.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" status                    # open or closed, minutes left, data delay
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" chart COMI --interval 5m --bars 60
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" chart COMI ETEL HRHO --interval 1d --bars 30
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" quote COMI                # last price, EPS, next earnings date
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" universe                  # every EGX stock, most traded first
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" calendar                  # today's session and this year's holidays
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" calendar --refresh        # re-read hours and holidays from TradingView
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" login                     # real-time: save your TradingView session (own computer)
python3 "${CLAUDE_SKILL_DIR}/egx_data.py" logout
```

Needs one package for prices and quotes: `pip install websocket-client`. The
stock list and headlines use the standard library only. Python 3.8 or newer.

## Where the data comes from

| what | host |
| --- | --- |
| bars, quotes, fundamentals, session hours, holidays | `data.tradingview.com` (websocket); `prodata.tradingview.com` when signed in |
| your login: the session cookie swapped for a token | `www.tradingview.com` |
| the stock list and screening | `scanner.tradingview.com` |
| headlines per stock and for the Egyptian market | `news-headlines.tradingview.com` |

Without a TradingView login TradingView reports `delayed_streaming_900`: EGX
prices are **15 minutes late**. Every chart and quote carries the delay
TradingView reports, and every tool repeats it. The price in the broker app is
the one that fills.

## Real-time data (your TradingView subscription)

TradingView sells Egyptian Exchange real-time data for $14.95 a month (its
Market Data page; it may also need a paid TradingView plan). With it:

1. On tradingview.com: sign in and buy the Egyptian Exchange real-time data
   (Profile, Market data subscriptions).
2. **On your own computer**, in a terminal: `python3 trader-egx/egx_data.py login`.
   It explains how to copy the `sessionid` and `sessionid_sign` cookies from
   your browser and reads them with hidden input. They are saved in
   `~/.config/egx-trader/tradingview.json` (owner-only), never in the repo.
3. `python3 trader-egx/egx_data.py status` should then say
   "TradingView login: signed in as ..., EGX real-time". Every tool then gets
   real-time bars and quotes (from `prodata.tradingview.com`) and stops saying
   "15 min late".

Rules for the login:
- Never ask the user to paste the cookies into a chat, and never write them to
  a file in the repo or an environment setting: only `egx_data.py login`, on
  the user's own computer.
- Signing out of TradingView in the browser ends the saved session: run
  `login` again. A failed or expired login never stops the tools: they fall
  back to the delayed feed and say why.
- `egx_data.py logout` removes it.
- Real-time data on TradingView is licensed for the subscriber's own use.
  Using it through these tools is at the user's own risk under TradingView's
  terms; it is never to be shared or redistributed.

## The calendar

- Sessions: Sunday to Thursday, 10:00 to 14:30 Cairo. In Ramadan 10:00 to
  13:30 (TradingView's session rules, with its one-day corrections).
- Holidays: TradingView's EGX holiday list, shipped in `calendar.json`
  (through 2026). `calendar --refresh` writes a newer copy to
  `trader/egx_calendar.json`, which then wins. Outside the known years every
  tool says the holiday list does not cover the date: check egx.com.eg.
- Cairo time: UTC+2, and UTC+3 in summer time (last Friday of April to the
  last Thursday of October, since 2023). Built in, no time zone files.

## Rules this module keeps

- One attempt per request, with an overall time limit. No retry loops. A
  failure names the host and the reason.
- A ticker that fails is reported by name, never silently dropped.
- EGX price steps: 0.01 EGP, and 0.001 under 1 EGP.

## Steps

1. For "is the market open": run `status` and say it in one line, with the
   minutes left and the data delay.
2. For a price: run `quote TICKER` and give the price with its time and
   "15 minutes late", then point the user to their broker app for the live one.
3. If a host is blocked, say which one and stop: the environment's network
   settings need it (the list above).
4. Once a year, or when a holiday is announced, run `calendar --refresh`.

## Example

```
$ python3 egx_data.py chart COMI ETEL NOPE --interval 5m --bars 3 --show 1
COMI  3 5m bars, DELAYED 15 min
  2026-11-22 14:25  O 125.01  H 125.01  L 125.01  C 125.01  V 118978
ETEL  3 5m bars, DELAYED 15 min
  2026-11-22 14:25  O 137.00  H 137.00  L 137.00  C 137.00  V 83781
NOPE  ERROR: TradingView: s1 resolve error (is NOPE an EGX ticker?)
```

## What it never does

It never places or prepares an order, never logs in anywhere, and never
hides a ticker that failed.

## Safety

- Not financial advice. Data is free, unofficial and 15 minutes late.
- If a website is blocked, name the exact host and stop. Do not retry in a loop.
