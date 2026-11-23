# The Day Trader skill: EGX edition

12 Claude skills for day trading on the Egyptian Exchange (EGX): scan every chart, track news and disclosures, time every entry and exit, and replay every outcome before you risk a pound.

An EGX fork of [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill),
the Trader skill v2. Same method; Egyptian market, Cairo time, Egyptian pounds,
and tickets you enter by hand.

The tools are plain Python that read TradingView's free EGX data: prices,
the EGX session calendar, the stock list, fundamentals and headlines, plus
Daily News Egypt. One package to install (`websocket-client`), no signup, no
API key.

**The free data is 15 minutes late.** Without a TradingView login, TradingView's
EGX feed is delayed 15 minutes. Every signal, quote and ticket says how late
its data is. For real-time data, subscribe to TradingView's Egyptian Exchange
real-time data ($14.95 a month on its Market Data page) and run
`python3 trader-egx/egx_data.py login` once on your own computer (see
`trader-egx/SKILL.md`). The price in your broker app is the one that fills:
check it before you act.

**Nothing in this pack places an order.** Your broker (Telda) has no API.
Every order is a ticket you type into the app yourself, after `/trader-risk`
passes it and you say yes to that one ticket. Paper trading is the default.

**Alerts reach your phone.** `trader-alerts/alerts.py notify-setup` links the free ntfy app, and every alert also pops up on the Mac with a sound.

**Every action gets a 5-minute warning** whenever the clock or the price
allows it: GET READY before an entry trigger, halfway to your stop and your
target, and 5 minutes before you must be flat. A trigger without one is LATE.

## Install

Paste this into Claude:

```
https://github.com/omarahmedhassan444-netizen/day-trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add omarahmedhassan444-netizen/day-trader-skill
/plugin install day-trader-skill@day-trader-skill
```

Or copy the folders (keep `trader-egx`: every tool imports it):

```bash
git clone https://github.com/omarahmedhassan444-netizen/day-trader-skill.git
cp -r day-trader-skill/trader-* ~/.claude/skills/
pip install websocket-client
```

The tools need Python 3.8 or newer. The network must reach
`data.tradingview.com`, `scanner.tradingview.com`,
`news-headlines.tradingview.com` and `www.dailynewsegypt.com`.

## The twelve

| command | job | what it does |
| --- | --- | --- |
| `/trader-egx` | EGX Data | Prices, the EGX calendar (Sunday to Thursday, 10:00 to 14:30 Cairo, Ramadan hours, holidays), the stock list and the data's delay. Every tool fetches through it. |
| `/trader-scanner` | Chart Scanner | Scans every EGX chart for setups: opening-range breakouts, 20-day breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Tracks headlines and EGX disclosures on every stock you watch and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar, says GET READY, then the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop, your target and your flat-by time before you enter. |
| `/trader-timemachine` | Time Machine | Replays every possible outcome before you risk a pound: 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | Checks every trade against your limits, the Cairo entry window and EGX's same-day (T+0) list. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade, fees included, in EGP. |
| `/trader-broker` | Order Tickets | Prints the ticket you type into Telda, step by step, and logs what you did. Paper by default. |
| `/trader-alerts` | Price Alerts | Warns before the stop, the target and the close, and rings when they hit. |
| `/trader-analyst` | Earnings Analyst | Reads quarterly results and sums up what changed, with the next report date. |
| `/trader-insiders` | Insider Tracker | Reads EGX disclosures of big holders and insiders: stake changes, block trades, tender offers, buybacks. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: GET READY, stop, target, 5-minute warning, flat by the close         (while it is open)
news / analyst / insiders                                                    (on a schedule)
```

An example with real data: COMI (Commercial International Bank), Tuesday
3 November 2026.

1. **`/trader-scanner`** at 10:30 Cairo: of the 60 most traded EGX stocks, 35
   had closed above their 15-minute opening range. COMI is one: range 121.51
   to 122.00.
2. **`/trader-news`**: COMI's Q3 results were out a week earlier (profit
   EGP 23.76 bn, EPS ahead of the estimate); the next report is due around
   24 February 2027, so a day trade is not exposed to it.
3. **`/trader-entry`** watched COMI bar by bar. At 10:15 it said GET READY:
   the 10:10 bar closed at 121.92, 0.07 % under the range high. At 10:25 it
   said TRIGGERED: the 10:20 bar closed above the range at 122.41. Ten
   minutes of warning. (Live, those bars arrive 15 minutes late.)
4. **`/trader-exit`** set the plan: stop at the range's midpoint, 121.75, so
   0.66 a share at risk; target one times that risk, 123.07; flat by 14:20
   either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times from
   COMI's own 5-minute bars over the previous 60 sessions (seed 19): target
   first 44.1 %, stop first 55.1 %, still open at the close 0.8 %. The same
   rule on those 60 real sessions fired 30 times: 9 target, 16 stop, 5
   neither. Its verdict: leans toward the stop. That is the point of looking
   before you risk a pound.
6. **`/trader-budget`** with an EGP 50,000 budget and an EGP 500 max loss:
   the 75 % position cap binds at 306 shares (EGP 37,457.46); the loss at the
   stop is EGP 201.96.
7. **`/trader-risk`** checks it: the max loss, the daily loss limit, trades
   per day, reward to risk, the 10:15 to 14:00 Cairo window, and whether COMI
   is on EGX's same-day list. PASS or BLOCK, with the reason.
8. **`/trader-broker`** prints the ticket for Telda: buy 306 COMI at market,
   the stop at 121.75, the take-profit at 123.07, flat by 14:20. You say yes
   to that one ticket and type it in yourself (or log it as paper).
9. **`/trader-alerts`** warns at 122.08 and 122.74 (halfway to the stop and
   the target), rings at the stop or the target, warns at 14:15 and rings at
   14:20 to sell whatever is still open.

## The tools

```bash
python3 trader-egx/egx_data.py status                                  # is EGX open, how late is the data
python3 trader-scanner/scan.py universe                                # every EGX stock
python3 trader-scanner/scan.py daily                                   # pre-market setups
python3 trader-scanner/scan.py orb --at 10:30                          # opening-range breakouts so far
python3 trader-news/news.py --tickers COMI --hours 12                  # headlines, disclosures, today's move
python3 trader-entry/entry.py check COMI --setup orb                   # get-ready / waiting / triggered
python3 trader-entry/entry.py watch COMI --every 30                    # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # warnings, stop, target, flat by
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py COMI                                   # quarters, growth, EPS
python3 trader-insiders/insiders.py --tickers COMI EAST                # stake changes, block trades
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real EGX data: COMI's 5-minute
bars for the 62 sessions to 3 November 2026, the 60 most traded stocks that
day, TradingView's EGX screener, quotes and fundamentals, headlines and
Daily News Egypt's feed. They reproduce every number in the example above.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**You enter every order.** Telda has no API, and this pack never asks for a
login, PIN, card or account number. `/trader-broker` prints a ticket only
after `/trader-risk` passes that exact order, and only one ticket per yes.

**Paper first.** `"paper": true` is the default in `config.example.json`:
the tickets say "do NOT enter it" and the trades are logged as practice.

**The data is unofficial, and free data is 15 minutes late.** TradingView's EGX
feed without a login is delayed 15 minutes (real-time with your own
subscription and `egx_data.py login`, licensed for your own use only), and the scanner and the entry
timer read 1-minute or 5-minute bars, not every trade. EGX is thinner than
the big markets: a 5-minute stretch with no trades has no bar. Check
anything that matters against your broker app before you act.

**Same-day selling.** On EGX you can sell a stock the day you buy it only if
it is on EGX's intraday (T+0) trading list. Put that list in
`"same_day_list"` and `/trader-risk` blocks day trades in anything else.

**EGX rules change.** Hours, Ramadan sessions, price limits, tick sizes and
the T+0 list are set by EGX and the FRA. The calendar comes from
TradingView; check egx.com.eg before relying on any rule.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 sessions of 5-minute bars. The future can do things those
sessions never did: results, a halt, a price limit, a headline.

**A stop is a plan, not a guarantee.** In a fast market or across a gap a
stop can fill well past its price, and if your app has no stop order you
sell by hand when the alert rings.

**Disclosures are public, not inside information.** `/trader-insiders`
reads what holders disclosed to EGX after they traded.

**Taxes and fees.** Set `"fee_pct"` to your broker's commission plus EGX and
tax charges, so the max loss includes them. Nothing here is tax advice.

## Files

```
trader-egx/         SKILL.md, egx_data.py, calendar.json
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-insiders/    SKILL.md, insiders.py
tests/              unittest suite + offline fixtures
```

MIT. Original by [Jake Schincariol](https://opusjake.ai); EGX edition in this fork.
