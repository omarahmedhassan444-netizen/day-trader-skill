---
name: trader-journal
description: >-
  Trade Journal. Logs each closed paper trade to trader/journal.csv: date,
  ticker, entry, stop, target, shares, exit price, profit or loss in dollars
  and in R, and a one-line note. Can fill the ticker, entry, stop, target and
  date from trader/plan.json (trader-exit). Prints a weekly summary: number
  of trades, win rate, average R and the largest loss. Long side only.
  Refuses a stop at or above the entry, since there is no R without risk.
  Use when the user says "log this trade", "journal it", "I got stopped
  out", "I hit my target", "record the exit", "how did I do this week",
  "weekly review", "what's my win rate", "average R", or after a trade
  from /trader-broker closes.
argument-hint: "log --exit PRICE --shares N [--from-plan] [--note TEXT] | week [--date YYYY-MM-DD]"
---

# trader-journal

The Trade Journal is where a trade ends up after it is closed. Every trade
gets one row, written the same way, so the week can be read honestly: how
many trades, how many won, the average result in R, and the worst loss. R is
the risk a share you planned at the stop, so a result in R tells you whether
you followed the plan, whatever the share price. Most people who day trade
lose money, and a journal is how you find out whether you are one of them
before real money does it for you.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/journal.py" log --from-plan --shares 8 --exit 370.04 --note "ORB, took 1R"
python3 "${CLAUDE_SKILL_DIR}/journal.py" log --ticker TSLA --entry 364.18 --stop 358.32 \
    --target 370.04 --shares 8 --exit 358.32 --note "faded back into the range" --date 2026-10-20
python3 "${CLAUDE_SKILL_DIR}/journal.py" week                    # this week, Monday to Sunday
python3 "${CLAUDE_SKILL_DIR}/journal.py" week --date 2026-10-19  # the week holding that day
python3 "${CLAUDE_SKILL_DIR}/journal.py" week --json
```

Other flags: `--journal PATH` (default `trader/journal.csv`), `--plan PATH`
(default `trader/plan.json`), `--json` on `log` too.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | with `--from-plan`: the ticker, entry, stop, target and date trader-exit planned |
| `trader/journal.csv` | one row per closed trade, appended; the header is written the first time |

The columns, in order:

| column | what |
| --- | --- |
| `date` | the trade's day, YYYY-MM-DD |
| `ticker` | upper case |
| `entry`, `stop`, `target` | to the cent; the target can be blank |
| `shares` | a whole number |
| `exit_price` | where you got out, to the cent |
| `pnl_usd` | (exit minus entry) x shares, to the cent |
| `pnl_r` | (exit minus entry) / (entry minus stop), to 0.01 R |
| `note` | one line; line breaks are folded into spaces |

## How the numbers work

- **R.** 1R is entry minus the planned stop. A clean stop-out is -1R, a 1R
  target is +1R. Worse than -1R means the stop filled past its price (a gap,
  a fast market) or the stop never reached the broker; the tool says so.
- **Win rate.** A win is a trade with a profit above $0. Break-even is not a
  win.
- **Average R.** The mean of the week's `pnl_r`, to 0.01 R.
- **Largest loss.** The single most negative `pnl_usd` of the week, with its
  R, ticker and date. "none" when nothing lost.
- **The week** runs Monday to Sunday and is picked by `--date` (any day in
  it) or today.

## Steps

1. When a trade closes (target, stop, or flat by the close), get the fill
   price from the broker or trader-broker's log. Use the real fill, not the
   planned price.
2. If the plan is still in `trader/plan.json`, run `journal.py log
   --from-plan --shares N --exit PRICE --note "..."`. Otherwise give
   `--ticker`, `--entry`, `--stop` (and `--target`) yourself.
3. Ask the user for the one-line note if they have not given one: why in,
   why out, and whether they followed the plan.
4. Read the row back: P&L in dollars and in R.
5. At the end of the week (or when asked), run `journal.py week` and read
   back the trades, win rate, average R and the largest loss. Point out a
   loss worse than -1R: that is a rule broken, not bad luck.

## Example: the reel

The TSLA trade from trader-exit and trader-budget: 8 shares, entry 364.18,
stop 358.32, target 370.04. Say it hit the target, and the next day a
second TSLA trade on the same plan was stopped out:

```
$ journal.py log --from-plan --shares 8 --exit 370.04 --note "ORB, took the 1R target"
Logged 2026-10-19 TSLA: 8 shares, entry 364.18, stop 358.32, exit 370.04
  P&L $46.88 (+1.00R)  note: ORB, took the 1R target
Wrote trader/journal.csv.

$ journal.py week --date 2026-10-19
Week of 2026-10-19 to 2026-10-25
  Trades        2
  Win rate      50.0% (1 of 2)
  Average R     +0.00R
  Total         $0.00
  Largest loss  -$46.88 (-1.00R) TSLA on 2026-10-20
```

One win and one loss of the same size: a coin flip at 1R nets nothing, just
as the Time Machine warned.

## Rules

- Log every closed trade, the losers most of all.
- Use the real exit price, not the plan's.
- The stop in the row is the one planned before the entry. Never rewrite it
  after the fact to make a loss look smaller in R.
- Long side only, like the rest of v2.

## What it never does

It never places an order, never reads or touches the broker, never edits or
deletes a row it already wrote (fix a mistake by editing the CSV yourself),
and never calls a good week a sign of a good strategy.

## Safety

- Education and tooling, not financial advice. A good week is not a prediction of the next one.
- Paper trading by default. A paper result is not a live result: real fills, slippage and nerves
  are worse.
- Nothing in this pack places an order except trader-broker, and only when the user typed an
  explicit yes for that one order. This journal only records trades that already closed.
