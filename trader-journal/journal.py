#!/usr/bin/env python3
"""Trade Journal for trader-journal: logs each paper trade to
trader/journal.csv and prints a weekly summary. Standard library only, no
network. Long side only, like the rest of v2. It never places an order.

    python3 journal.py log --ticker TSLA --entry 364.18 --stop 358.32 --target 370.04 \\
        --shares 8 --exit 370.04 --note "ORB, took the 1R target" --date 2026-10-19
    python3 journal.py log --from-plan --shares 8 --exit 358.32 --note "stopped out"
    python3 journal.py week                       # this week (Monday to Sunday)
    python3 journal.py week --date 2026-10-19     # the week holding that day
    python3 journal.py week --json

Each row: date, ticker, entry, stop, target, shares, exit price, profit or
loss in dollars and in R, and a one-line note.

  pnl_usd   (exit - entry) x shares, to the cent
  pnl_r     (exit - entry) / (entry - stop), to 0.01 R. 1R is the risk a
            share you planned at the stop, so -1R is a clean stop-out and
            anything under -1R means the stop filled worse than its price.

--from-plan fills the ticker, entry, stop, target and date from
trader/plan.json (trader-exit); any flag you give wins over the plan.

The week summary: number of trades, win rate (a win is a trade with a
profit above $0; break-even counts as not a win), average R, and the
largest loss in dollars (with its R, ticker and date).

Refused, with the reason: a stop at or above the entry (there is no R
without risk), shares that are not a whole number above 0, a price that is
not a number above 0, a date that is not YYYY-MM-DD.

Exit codes: log 0 = logged, 1 = refused or an error. week 0 = a summary
(even an empty week), 1 = an error.
"""

import argparse
import csv
import datetime
import json
import os
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

FIELDS = ["date", "ticker", "entry", "stop", "target", "shares", "exit_price", "pnl_usd", "pnl_r", "note"]
CENT = Decimal("0.01")
NOTE = "A paper result is not a live result: real fills, slippage and nerves are worse."


class JournalError(Exception):
    pass


# ---------------------------------------------------------------- numbers (Decimal, to the cent)

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().lstrip("$").replace(",", ""))
    except (InvalidOperation, ValueError):
        raise JournalError("%s is not a number: %r" % (what, x))


def cents(x):
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def money(x):
    x = D(x).quantize(CENT)
    return ("-$%s" if x < 0 else "$%s") % format(abs(x), ",f")


def r_text(x):
    return "%+.2fR" % float(x)


def price(x, what):
    v = cents(D(x, what))
    if v <= 0:
        raise JournalError("%s must be above 0: %s" % (what, x))
    return v


def parse_date(text):
    try:
        return datetime.datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise JournalError("date must be YYYY-MM-DD: %r" % (text,))


def one_line(text):
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- the trade

def compute(entry, stop, exit_price, shares):
    """P&L in dollars and in R for one long trade."""
    entry, stop, exit_price = price(entry, "entry"), price(stop, "stop"), price(exit_price, "exit price")
    try:
        n = int(D(shares, "shares"))
    except (ValueError, ArithmeticError):
        raise JournalError("shares is not a number: %r" % (shares,))
    if D(shares) != n or n <= 0:
        raise JournalError("shares must be a whole number above 0: %s" % shares)
    if stop >= entry:
        raise JournalError("the stop (%s) must be under the entry (%s): there is no R without risk"
                           % (stop, entry))
    move = exit_price - entry
    pnl = cents(move * n)
    r = (move / (entry - stop)).quantize(CENT, rounding=ROUND_HALF_UP)
    return {"entry": entry, "stop": stop, "exit_price": exit_price, "shares": n, "pnl_usd": pnl, "pnl_r": r}


def build_row(a):
    plan = {}
    if a.from_plan:
        if not os.path.exists(a.plan):
            raise JournalError("no plan at %s: give --ticker, --entry and --stop instead" % a.plan)
        try:
            with open(a.plan, encoding="utf-8") as fh:
                plan = json.load(fh)
        except ValueError as e:
            raise JournalError("%s is not valid JSON: %s" % (a.plan, e))
    ticker = a.ticker or plan.get("symbol")
    entry = a.entry if a.entry is not None else plan.get("entry")
    stop = a.stop if a.stop is not None else plan.get("stop")
    target = a.target if a.target is not None else plan.get("target")
    day = a.date or plan.get("date") or datetime.date.today().isoformat()
    for name, v in (("--ticker", ticker), ("--entry", entry), ("--stop", stop)):
        if v is None or v == "":
            raise JournalError("missing %s (or use --from-plan with a trader/plan.json)" % name)
    t = compute(entry, stop, a.exit, a.shares)
    return {
        "date": parse_date(day).isoformat(),
        "ticker": str(ticker).strip().upper(),
        "entry": str(t["entry"]),
        "stop": str(t["stop"]),
        "target": str(price(target, "target")) if target not in (None, "") else "",
        "shares": str(t["shares"]),
        "exit_price": str(t["exit_price"]),
        "pnl_usd": str(t["pnl_usd"]),
        "pnl_r": str(t["pnl_r"]),
        "note": one_line(a.note),
    }


# ---------------------------------------------------------------- the file

def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_row(path, row):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


# ---------------------------------------------------------------- the week

def week_of(day):
    monday = day - datetime.timedelta(days=day.weekday())
    return monday, monday + datetime.timedelta(days=6)


def summarize(rows, day):
    monday, sunday = week_of(day)
    week = []
    for r in rows:
        try:
            d = parse_date(r["date"])
        except (JournalError, KeyError):
            continue
        if monday <= d <= sunday:
            week.append(r)
    out = {"week_start": monday.isoformat(), "week_end": sunday.isoformat(), "trades": len(week),
           "wins": 0, "win_rate_pct": None, "average_r": None, "total_usd": "0.00", "largest_loss": None}
    if not week:
        return out
    pnls = [D(r["pnl_usd"], "pnl_usd") for r in week]
    rs = [D(r["pnl_r"], "pnl_r") for r in week]
    wins = sum(1 for p in pnls if p > 0)
    out["wins"] = wins
    out["win_rate_pct"] = float((Decimal(wins) * 100 / len(week)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    out["average_r"] = float((sum(rs) / len(rs)).quantize(CENT, rounding=ROUND_HALF_UP))
    out["total_usd"] = str(cents(sum(pnls)))
    losers = [(p, r) for p, r in zip(pnls, week) if p < 0]
    if losers:
        p, r = min(losers, key=lambda x: x[0])
        out["largest_loss"] = {"pnl_usd": str(p), "pnl_r": r["pnl_r"], "ticker": r["ticker"], "date": r["date"]}
    return out


def describe_week(s):
    lines = ["Week of %s to %s" % (s["week_start"], s["week_end"])]
    if not s["trades"]:
        lines.append("  No trades logged this week.")
        return lines
    lines += [
        "  Trades        %d" % s["trades"],
        "  Win rate      %.1f%% (%d of %d)" % (s["win_rate_pct"], s["wins"], s["trades"]),
        "  Average R     %s" % r_text(s["average_r"]),
        "  Total         %s" % money(s["total_usd"]),
    ]
    big = s["largest_loss"]
    if big:
        lines.append("  Largest loss  %s (%s) %s on %s" % (money(big["pnl_usd"]), r_text(D(big["pnl_r"])),
                                                         big["ticker"], big["date"]))
    else:
        lines.append("  Largest loss  none: no losing trades")
    return lines


# ---------------------------------------------------------------- commands

def cmd_log(a):
    row = build_row(a)
    append_row(a.journal, row)
    if a.json:
        print(json.dumps(row, indent=2))
        return 0
    print("Logged %s %s: %s shares, entry %s, stop %s, exit %s" % (
        row["date"], row["ticker"], row["shares"], row["entry"], row["stop"], row["exit_price"]))
    print("  P&L %s (%s)%s" % (money(row["pnl_usd"]), r_text(D(row["pnl_r"])),
                              ("  note: " + row["note"]) if row["note"] else ""))
    if D(row["pnl_r"]) < -1:
        print("Note: worse than -1R: the stop filled past its price, or the trade had no stop at the broker.")
    print("Wrote %s." % a.journal)
    return 0


def cmd_week(a):
    day = parse_date(a.date) if a.date else datetime.date.today()
    s = summarize(read_rows(a.journal), day)
    if a.json:
        print(json.dumps(s, indent=2))
        return 0
    for line in describe_week(s):
        print(line)
    if s["trades"]:
        print(NOTE)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trade Journal (trader-journal): log paper trades, sum up the week.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("log", help="log one closed trade")
    p.add_argument("--ticker")
    p.add_argument("--entry", help="entry price")
    p.add_argument("--stop", help="the planned stop (sets 1R)")
    p.add_argument("--target", help="the planned target (optional)")
    p.add_argument("--shares", required=True)
    p.add_argument("--exit", required=True, help="exit price")
    p.add_argument("--note", default="", help="one line: why in, why out")
    p.add_argument("--date", help="YYYY-MM-DD (default: the plan's date, or today)")
    p.add_argument("--from-plan", action="store_true", help="fill the rest from trader/plan.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--journal", default="trader/journal.csv")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("week", help="the weekly summary")
    p.add_argument("--date", help="any day in the week, YYYY-MM-DD (default: today)")
    p.add_argument("--journal", default="trader/journal.csv")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"log": cmd_log, "week": cmd_week}[a.cmd](a)
    except JournalError as e:
        print("journal.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
