#!/usr/bin/env python3
"""Earnings reader for trader-analyst: EGX edition.

Reads an EGX company's reported numbers from TradingView's fundamentals and
lays out what changed, quarter by quarter: revenue, gross, operating and net
margins, net income, diluted EPS, and growth against the same quarter a year
earlier. It also shows the last report date, the next one, and how the last
report compared with the analysts' estimate when there is one.

    python3 facts.py COMI
    python3 facts.py COMI ETEL --quarters 8
    python3 facts.py COMI --json
    python3 facts.py COMI ETEL TMGH --metrics trader/metrics.json

Source: TradingView's quote session for EGX:<TICKER> (one connection for
every ticker), through trader-egx/egx_data.py. Fields used, newest first:
  fiscal_period_end_fq_h          the end of each fiscal quarter
  total_revenue_fq_h              revenue (for banks: total income)
  gross_profit_fq_h               gross profit (blank for banks)
  oper_income_fq_h                operating income
  net_income_fq_h                 net income
  earnings_per_share_diluted_fq_h diluted EPS
  earnings_per_share_fq / _forecast_fq_h, revenue_forecast_fq_h   estimates
  earnings_release_next_date      the next report
Amounts are in EGP (fundamental_currency_code); the report says so when not.
The numbers come from the companies' filings as TradingView's data provider
collects them: open the company's disclosure on egx.com.eg before you rely on
a number. --offline DIR reads quote-<TICKER>.json(.gz) from DIR instead.

--metrics FILE merges one row per ticker into a JSON file (for notes or a sheet)
(TTM revenue and EPS, margins, growth, P/E, dividend yield).

Numbers only: this tool never says whether a stock is a buy. Not financial advice.
"""

import argparse
import datetime as _dt
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-egx"))
import egx_data as E  # noqa: E402

FIELDS = {"revenue": "total_revenue_fq_h", "gross": "gross_profit_fq_h", "operating": "oper_income_fq_h",
          "net_income": "net_income_fq_h", "eps": "earnings_per_share_diluted_fq_h"}


class FactsError(Exception):
    pass


def _date(ts):
    return E.utc_of(ts).date() if isinstance(ts, (int, float)) else None


def _at(arr, i):
    return arr[i] if isinstance(arr, list) and i < len(arr) else None


def quarters(q):
    """Quote fields -> [{"end", "period", "revenue", "gross", "operating", "net_income", "eps"}], newest first.
    The period is the calendar quarter the fiscal quarter ends in."""
    ends = q.get("fiscal_period_end_fq_h") or []
    out = []
    for i, ts in enumerate(ends):
        d = _date(ts)
        if d is None:
            continue
        # the label comes from the quarter's own end date: TradingView's earnings_* lists are
        # shorter and can be offset from the *_fq_h figures, so they are not used per row
        row = {"end": d.isoformat(), "period": "%d-Q%d" % (d.year, (d.month - 1) // 3 + 1)}
        for k, f in FIELDS.items():
            v = _at(q.get(f), i)
            row[k] = float(v) if isinstance(v, (int, float)) else None
        out.append(row)
    return out


def pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return round(100.0 * (a / b - 1) if b > 0 else 100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def analyse(q, n=6):
    """One company's quote fields -> the result (pure)."""
    qs = quarters(q)
    if not qs:
        raise FactsError("TradingView has no quarterly numbers for this ticker")
    rows = []
    for i, r in enumerate(qs[:n]):
        prev = qs[i + 4] if i + 4 < len(qs) else None
        rows.append(dict(r, revenue_yoy=pct(r["revenue"], prev and prev["revenue"]),
                         net_income_yoy=pct(r["net_income"], prev and prev["net_income"]),
                         eps_yoy=pct(r["eps"], prev and prev["eps"]),
                         gross_margin=margin(r["gross"], r["revenue"]),
                         operating_margin=margin(r["operating"], r["revenue"]),
                         net_margin=margin(r["net_income"], r["revenue"])))

    def ttm(k, start=0):
        vals = [x[k] for x in qs[start:start + 4]]
        return sum(vals) if len(vals) == 4 and None not in vals else None
    est = (q.get("earnings_per_share_forecast_fq_h") or [None])[0]
    rev_est = (q.get("revenue_forecast_fq_h") or [None])[0]
    last = qs[0]
    out = {"name": q.get("description"), "sector": q.get("sector"), "industry": q.get("industry"),
           "currency": q.get("fundamental_currency_code") or "EGP", "price": q.get("lp"),
           "quarters": rows, "period_end": last["end"], "period": last["period"],
           "last_report": _date(q.get("earnings_release_date")).isoformat()
           if isinstance(q.get("earnings_release_date"), (int, float)) else None,
           "next_report": _date(q.get("earnings_release_next_date")).isoformat()
           if isinstance(q.get("earnings_release_next_date"), (int, float)) else None,
           "revenue_ttm": ttm("revenue"), "net_income_ttm": ttm("net_income"), "eps_ttm": ttm("eps"),
           "revenue_ttm_yoy": pct(ttm("revenue"), ttm("revenue", 4)),
           "net_income_ttm_yoy": pct(ttm("net_income"), ttm("net_income", 4)),
           "pe_ttm": q.get("price_earnings_ttm"), "dividend_yield": q.get("dividends_yield_current"),
           "dps_fy": q.get("dps_common_stock_prim_issue_fy"), "market_cap": q.get("market_cap_basic"),
           "eps_estimate": est, "revenue_estimate": rev_est}
    eps_rep = q.get("earnings_per_share_fq")
    if isinstance(est, (int, float)) and isinstance(eps_rep, (int, float)):
        out["eps_reported_vs_estimate"] = {"reported": eps_rep, "estimate": est,
                                           "surprise_pct": pct(eps_rep, est)}
    return out


def changes(result):
    """Plain sentences: what changed in the newest quarter."""
    r = result["quarters"][0]
    cur = result["currency"]
    lines = []

    def money(v):
        if v is None:
            return "n/a"
        a = abs(v)
        if a >= 1e9:
            s = "%.2f bn" % (v / 1e9)
        elif a >= 1e6:
            s = "%.1f m" % (v / 1e6)
        else:
            s = "{:,.0f}".format(v)
        return "%s %s" % (cur, s)
    if r["revenue"] is not None:
        lines.append("Revenue %s in %s%s." % (money(r["revenue"]), r["period"],
                                             (", %+.1f%% on a year earlier" % r["revenue_yoy"])
                                             if r["revenue_yoy"] is not None else ""))
    if r["net_income"] is not None:
        lines.append("Net income %s%s, a %s net margin." % (
            money(r["net_income"]), (" (%+.1f%% on a year earlier)" % r["net_income_yoy"])
            if r["net_income_yoy"] is not None else "",
            ("%.1f%%" % r["net_margin"]) if r["net_margin"] is not None else "n/a"))
    if r["eps"] is not None:
        lines.append("Diluted EPS %s %.2f%s." % (cur, r["eps"], (" (%+.1f%% on a year earlier)" % r["eps_yoy"])
                                                 if r["eps_yoy"] is not None else ""))
    s = result.get("eps_reported_vs_estimate")
    if s and s["surprise_pct"] is not None:
        lines.append("Against the analysts' estimate: EPS %.2f vs %.2f expected (%+.1f%%)." % (
            s["reported"], s["estimate"], s["surprise_pct"]))
    if result.get("next_report"):
        lines.append("Next report expected around %s: a day trade held into it carries that risk." %
                     result["next_report"])
    return lines


def _m(v):
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e9:
        return "%.2fbn" % (v / 1e9)
    if a >= 1e6:
        return "%.1fm" % (v / 1e6)
    return "{:,.0f}".format(v)


def _p(v):
    return "n/a" if v is None else "%.1f%%" % v


def _pm(v):
    return "n/a" if v is None else "%+.1f%%" % v


def render(ticker, result):
    out = ["%s  %s  (%s, %s; amounts in %s)" % (ticker, result.get("name") or "", result.get("sector") or "?",
                                               result.get("industry") or "?", result["currency"])]
    out.append("  %-8s %-10s %12s %8s %12s %8s %8s %8s %8s %9s" % (
        "quarter", "ended", "revenue", "yoy", "net income", "yoy", "net mgn", "op mgn", "EPS", "EPS yoy"))
    for r in result["quarters"]:
        out.append("  %-8s %-10s %12s %8s %12s %8s %8s %8s %8s %9s" % (
            r["period"], r["end"], _m(r["revenue"]), _pm(r["revenue_yoy"]), _m(r["net_income"]),
            _pm(r["net_income_yoy"]), _p(r["net_margin"]), _p(r["operating_margin"]),
            "n/a" if r["eps"] is None else "%.2f" % r["eps"], _pm(r["eps_yoy"])))
    out.append("  TTM: revenue %s (%s), net income %s (%s), EPS %s; P/E %s; dividend yield %s." % (
        _m(result["revenue_ttm"]), _pm(result["revenue_ttm_yoy"]), _m(result["net_income_ttm"]),
        _pm(result["net_income_ttm_yoy"]), "n/a" if result["eps_ttm"] is None else "%.2f" % result["eps_ttm"],
        "n/a" if result["pe_ttm"] is None else "%.1f" % result["pe_ttm"], _p(result["dividend_yield"])))
    out.append("  Last report %s; next %s." % (result.get("last_report") or "n/a", result.get("next_report") or
                                              "not announced"))
    for line in changes(result):
        out.append("  " + line)
    return out


def metrics_row(ticker, result):
    r = result["quarters"][0]
    return {"ticker": ticker, "name": result.get("name"), "period_end": result["period_end"],
            "currency": result["currency"], "revenue_ttm": result["revenue_ttm"],
            "net_income_ttm": result["net_income_ttm"],
            "eps_ttm": None if result["eps_ttm"] is None else round(result["eps_ttm"], 4),
            "revenue_growth_yoy": r["revenue_yoy"], "net_income_growth_yoy": r["net_income_yoy"],
            "eps_growth_yoy": r["eps_yoy"], "gross_margin": r["gross_margin"],
            "operating_margin": r["operating_margin"], "net_margin": r["net_margin"],
            "pe_ttm": None if result["pe_ttm"] is None else round(result["pe_ttm"], 2),
            "dividend_yield": None if result["dividend_yield"] is None else round(result["dividend_yield"], 2),
            "next_report": result.get("next_report"), "source": "TradingView fundamentals (EGX)"}


def load_offline(folder, ticker):
    for name in ("quote-%s.json" % ticker, "quote-%s.json.gz" % ticker):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            opener = gzip.open if p.endswith(".gz") else open
            with opener(p, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
            return doc.get(ticker, doc) if isinstance(doc, dict) else doc
    raise FactsError("no quote-%s.json in %s" % (ticker, folder))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Earnings reader for trader-analyst (EGX, TradingView fundamentals).")
    ap.add_argument("tickers", nargs="+", help="EGX tickers, e.g. COMI ETEL")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per ticker into this JSON")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="the as_of date for --metrics (YYYY-MM-DD)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    tickers = []
    for t in a.tickers:
        try:
            s = E.norm_symbol(t)
        except E.EGXDataError as e:
            print("facts.py: %s" % e, file=sys.stderr)
            return 1
        if s not in tickers:
            tickers.append(s)
    data, errors = {}, {}
    if a.offline:
        for t in tickers:
            try:
                data[t] = load_offline(a.offline, t)
            except FactsError as e:
                errors[t] = str(e)
    else:
        try:
            r = E.quote(tickers)
        except E.EGXDataError as e:
            print("facts.py: %s" % e, file=sys.stderr)
            return 1
        data, errors = r["ok"], dict(r["errors"])
    results = {}
    for t in tickers:
        if t not in data:
            continue
        try:
            results[t] = analyse(data[t], a.quarters)
        except FactsError as e:
            errors[t] = str(e)
    if a.metrics and results:
        doc = {}
        if os.path.exists(a.metrics):
            with open(a.metrics, encoding="utf-8") as fh:
                doc = json.load(fh)
        rows = {r["ticker"]: r for r in doc.get("companies", [])}
        for t, res in results.items():
            rows[t] = metrics_row(t, res)
        doc = {"as_of": a.now or _dt.date.today().isoformat(), "companies": [rows[k] for k in sorted(rows)]}
        d = os.path.dirname(a.metrics)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.metrics, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    if a.json:
        print(json.dumps({"results": results, "errors": errors}, indent=2, ensure_ascii=False))
    else:
        for t in tickers:
            if t in results:
                for line in render(t, results[t]):
                    print(line)
                print("")
        for t, e in sorted(errors.items()):
            print("%s: %s" % (t, e))
        if a.metrics and results:
            print("Merged %d row(s) into %s." % (len(results), a.metrics))
        print("Numbers from TradingView's fundamentals: check the company's disclosure on egx.com.eg before you "
              "rely on one. Not financial advice.")
    return 0 if results and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
