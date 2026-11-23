#!/usr/bin/env python3
"""Insider and major-shareholder tracker for trader-insiders: EGX edition.

On EGX, big holders and insiders must disclose when they buy or sell, and
EGX publishes block trades, mandatory tender offers and treasury-share buys.
Reuters carries these as short headlines ("... Acquires 5.20% Stake In
Egypt's Eastern Company - EGX"). This tool reads them, turns each one into a
row (who, what, how much, which stock, when), shows every new one once, and
flags the ones in stocks you hold or watch.

    python3 insiders.py                              # the Egyptian market feed + your tickers
    python3 insiders.py --tickers COMI EAST FWRY     # those stocks' own feeds too
    python3 insiders.py --holder fidelity            # rows that name this holder
    python3 insiders.py --kind stake,sale --days 30 --all
    python3 insiders.py --json

Kinds:
  stake     a holder's stake went up or down, with the new percent when given
  sale      a holder sold a number of shares (and for how much)
  purchase  a holder bought a number of shares
  block     a block trade: a large lot that changed hands in one deal
  tender    a mandatory tender offer (MTO) or a voluntary offer, and the FRA's decision
  treasury  a company buying back its own shares (treasury shares)

Counted only when the headline says it was disclosed to EGX or the FRA
("- EGX", "- FRA"), or when it is a tender offer or a treasury-share buy. A
company buying another company abroad is a deal, not an insider trade, and is
left to /trader-news.

Sources: TradingView's headlines (Reuters and others), through
trader-egx/egx_data.py:
  https://news-headlines.tradingview.com/v2/headlines?category=stock&market_country=EG
  https://news-headlines.tradingview.com/v2/headlines?symbol=EGX:<TICKER>
State: trader/state/insiders.json remembers what you have seen, so a row shows
once. --offline DIR reads tv-EG.json and tv-<TICKER>.json from DIR.

It is fast, not early: a disclosure comes after the trade. These are public
filings, not inside information. Not financial advice.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-egx"))
import egx_data as E  # noqa: E402

KEEP_DAYS = 120
KINDS = ("stake", "sale", "purchase", "block", "tender", "treasury")

NUM = r"([\d,.]+)\s*(Mln|Million|Bln|Billion|K|Thousand)?"
PATTERNS = [
    ("stake", re.compile(r"^(?P<holder>.+?)\s+(?P<verb>Acquires|Buys|Takes)\s+(?P<pct>[\d.]+)%\s+Stake\s+In\s+"
                         r"(?P<company>.+?)\s*-\s*(?:EGX|FRA)\s*$", re.I)),
    ("stake", re.compile(r"^(?P<holder>.+?)\s+(?P<verb>Raises|Increases|Ups|Boosts|Reduces|Cuts|Lowers|Trims)\s+"
                         r"(?:Its\s+)?Stake\s+In\s+(?P<company>.+?)\s+To\s+(?P<pct>[\d.]+)%\s*-\s*(?:EGX|FRA)\s*$",
                         re.I)),
    ("sale", re.compile(r"^(?P<holder>.+?)\s+Sells\s+" + NUM.replace("(", "(?P<n>", 1).replace("(Mln", "(?P<unit>Mln", 1)
                        + r"\s+Shares\s+In\s+(?P<company>.+?)(?:\s+For\s+EGP\s+(?P<value>[\d.,]+\s*(?:Mln|Million|Bln|Billion)?))?"
                        r"\s*-\s*(?:EGX|FRA)\s*$", re.I)),
    ("purchase", re.compile(r"^(?P<holder>.+?)\s+(?:Buys|Acquires|Purchases)\s+"
                            + NUM.replace("(", "(?P<n>", 1).replace("(Mln", "(?P<unit>Mln", 1)
                            + r"\s+Shares\s+(?:In|Of)\s+(?P<company>.+?)(?:\s+For\s+EGP\s+(?P<value>[\d.,]+\s*"
                            r"(?:Mln|Million|Bln|Billion)?))?\s*-\s*(?:EGX|FRA)\s*$", re.I)),
    ("block", re.compile(r"^(?P<company>.+?)'s\s+" + NUM.replace("(", "(?P<n>", 1).replace("(Mln", "(?P<unit>Mln", 1)
                         + r"\s+Shares\s+Block\s+Traded\s+For\s+EGP\s+(?P<value>[\d.,]+\s*(?:Mln|Million|Bln|Billion)?)"
                         r"\s*-\s*(?:EGX|FRA)\s*$", re.I)),
    ("tender", re.compile(r"(?:Mandatory|Voluntary)\s+(?:Tender\s+)?Offer|\bMTO\b", re.I)),
    ("treasury", re.compile(r"Treasury\s+Shares|\bBuy\s?back\b", re.I)),
]
SUFFIX = re.compile(r"-\s*(?:EGX|FRA)\s*$", re.I)


class InsiderError(Exception):
    pass


def to_local(utc):
    return E.to_cairo(utc)


def parse_now(text):
    if not text:
        return E.utc_now()
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(t)
    except ValueError:
        raise InsiderError('--now wants a time like "2026-11-23 10:00" (Cairo) or ...Z (UTC)')
    if dt.tzinfo is None:
        return E.cairo_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def _amount(n, unit):
    try:
        x = float(str(n).replace(",", ""))
    except (TypeError, ValueError):
        return None
    u = (unit or "").lower()
    if u in ("mln", "million"):
        x *= 1e6
    elif u in ("bln", "billion"):
        x *= 1e9
    elif u in ("k", "thousand"):
        x *= 1e3
    return x


def _value(text):
    if not text:
        return None
    m = re.match(r"([\d.,]+)\s*(Mln|Million|Bln|Billion)?", text.strip(), re.I)
    return _amount(m.group(1), m.group(2)) if m else None


def classify(item):
    """One headline -> a row, or None when it is not an insider / major-holder disclosure."""
    title = " ".join(item["title"].split())
    egx_syms = [s.split(":")[-1] for s in item.get("symbols", []) if s.startswith("EGX:")]
    if not egx_syms:
        return None
    for kind, rx in PATTERNS:
        m = rx.search(title)
        if not m:
            continue
        if kind in ("stake", "sale", "purchase", "block") and not SUFFIX.search(title):
            continue
        gd = m.groupdict()
        row = {"kind": kind, "symbol": egx_syms[0], "symbols": egx_syms, "title": title,
               "holder": (gd.get("holder") or "").strip() or None,
               "company": (gd.get("company") or "").strip() or None,
               "pct": float(gd["pct"]) if gd.get("pct") else None,
               "shares": _amount(gd.get("n"), gd.get("unit")) if gd.get("n") else None,
               "value_egp": _value(gd.get("value")),
               "direction": None, "time": item.get("published"), "source": item.get("provider"),
               "url": item.get("url"), "id": item.get("id") or title}
        verb = (gd.get("verb") or "").lower()
        if kind == "stake":
            row["direction"] = "down" if verb in ("reduces", "cuts", "lowers", "trims") else "up"
        elif kind == "sale":
            row["direction"] = "down"
        elif kind == "purchase":
            row["direction"] = "up"
        elif kind == "tender":
            row["status"] = ("rejected" if re.search(r"\breject", title, re.I) else
                             "approved" if re.search(r"\bapprov", title, re.I) else "proposed")
        return row
    return None


def fingerprint(row):
    return hashlib.sha1(("%s|%s" % (row["id"], row["title"])).encode("utf-8")).hexdigest()[:16]


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    out = []
    for h in (load_json(watchlist) or {}).get("hits", []):
        if h.get("symbol"):
            out.append(h["symbol"].upper())
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.append(pl["symbol"].upper())
    for p in (load_json(portfolio) or {}).get("positions", []):
        s = p.get("ticker") or p.get("symbol")
        if s:
            out.append(s.upper())
    return sorted(set(out))


def holdings(portfolio="trader/portfolio.json"):
    return sorted({(p.get("ticker") or p.get("symbol") or "").upper()
                   for p in (load_json(portfolio) or {}).get("positions", []) if p.get("ticker") or p.get("symbol")})


def gather(tickers, offline=None, errors=None):
    """Headlines from the market feed plus each ticker's feed -> unique items."""
    errors = [] if errors is None else errors
    items = []

    def read(sym):
        if offline:
            p = os.path.join(offline, "tv-%s.json" % (sym or "EG"))
            if not os.path.exists(p):
                if sym:
                    return []
                raise InsiderError("no file %s" % p)
            with open(p, encoding="utf-8") as fh:
                return E.parse_news(fh.read())
        return E.headlines(sym)
    for sym in [None] + list(tickers):
        try:
            items += read(sym)
        except (E.EGXDataError, InsiderError) as e:
            errors.append("headlines%s: %s" % ((" for " + sym) if sym else " (Egyptian market)", e))
    seen, out = set(), []
    for it in items:
        k = it["id"] or it["title"]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def select(rows, state, now, days=30, show_all=False, holder=None, kinds=None):
    seen = state.setdefault("seen", {})
    cutoff = E.ts_of(now - _dt.timedelta(days=days))
    out = []
    for r in rows:
        if r["time"] and r["time"] < cutoff:
            continue
        if r["time"] and r["time"] > E.ts_of(now) + 600:
            continue
        if holder and not (r["holder"] and holder.lower() in r["holder"].lower()):
            continue
        if kinds and r["kind"] not in kinds:
            continue
        r["new"] = fingerprint(r) not in seen
        if r["new"] or show_all:
            out.append(r)
    out.sort(key=lambda r: r["time"] or 0, reverse=True)
    return out


def remember(rows, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in rows:
        seen.setdefault(fingerprint(r), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def _big(x):
    if x is None:
        return None
    if x >= 1e9:
        return "%.2f bn" % (x / 1e9)
    if x >= 1e6:
        return "%.1f m" % (x / 1e6)
    return "{:,.0f}".format(x)


def describe(r):
    if r["kind"] == "stake":
        return "%s %s its stake in %s%s" % (r["holder"] or "a holder", "raised" if r["direction"] == "up" else "cut",
                                            r["symbol"], (" to %.3g%%" % r["pct"]) if r["pct"] is not None else "")
    if r["kind"] in ("sale", "purchase"):
        return "%s %s %s shares of %s%s" % (r["holder"] or "a holder", "sold" if r["kind"] == "sale" else "bought",
                                            _big(r["shares"]) or "?", r["symbol"],
                                            (" for EGP %s" % _big(r["value_egp"])) if r["value_egp"] else "")
    if r["kind"] == "block":
        return "block trade: %s shares of %s for EGP %s" % (_big(r["shares"]) or "?", r["symbol"],
                                                           _big(r["value_egp"]) or "?")
    if r["kind"] == "tender":
        return "tender offer for %s (%s)" % (r["symbol"], r.get("status", "proposed"))
    return "treasury shares: %s buying back its own stock" % r["symbol"]


def render(rows, errors, now, mine=()):
    out = []
    if not rows:
        out.append("Nothing new.")
    for r in rows:
        when = to_local(E.utc_of(r["time"])).strftime("%Y-%m-%d %H:%M Cairo") if r["time"] else "time unknown"
        flag = "YOURS " if r["symbol"] in mine else ""
        out.append("%s%s  %-6s %-8s %s" % (flag, when, r["symbol"], r["kind"].upper(), describe(r)))
        out.append("      \"%s\" (%s)%s" % (r["title"], r["source"] or "?", ("  " + r["url"]) if r["url"] else ""))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Public disclosures, read quickly: the trade came first. Open the filing before you act. "
               "Not financial advice." % to_local(now).strftime("%Y-%m-%d %H:%M Cairo"))
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="EGX insider and major-shareholder tracker (trader-insiders).")
    ap.add_argument("--tickers", nargs="*", help="also read these stocks' own feeds (default: watchlist, plan, "
                                                 "portfolio)")
    ap.add_argument("--holder", help="only rows that name this holder (part of the name)")
    ap.add_argument("--kind", help="only these kinds, comma separated: %s" % ", ".join(KINDS))
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--all", action="store_true", help="show seen rows too")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/insiders.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        kinds = None
        if a.kind:
            kinds = [k.strip().lower() for k in a.kind.split(",") if k.strip()]
            bad = [k for k in kinds if k not in KINDS]
            if bad:
                raise InsiderError("unknown kind %s (use %s)" % (", ".join(bad), ", ".join(KINDS)))
        tickers = [t.upper() for t in a.tickers] if a.tickers else watch_from_files(a.portfolio, a.watchlist, a.plan)
        errors = []
        items = gather(tickers, a.offline, errors)
    except InsiderError as e:
        print("insiders.py: %s" % e, file=sys.stderr)
        return 1
    rows = [r for r in (classify(it) for it in items) if r]
    state = load_json(a.state) or {}
    shown = select(rows, state, now, a.days, a.all, a.holder, kinds)
    mine = set(holdings(a.portfolio)) | set(tickers)
    if a.json:
        print(json.dumps({"checked": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": shown, "notes": errors},
                         indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, mine))
    if not a.dry_run:
        remember(rows, state, now)
        d = os.path.dirname(a.state)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(r["new"] for r in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
