#!/usr/bin/env python3
"""News monitor for trader-news: EGX edition.

Watches headlines and company disclosures for the EGX stocks you watch or
hold and prints only what is new since the last check, newest first, with how
far each stock has moved today and a flag on anything that touches your tickers.

    python3 news.py                                  # watchlist + plan + portfolio tickers
    python3 news.py --tickers COMI ETEL HRHO
    python3 news.py --tickers COMI --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-local                       # TradingView headlines only
    python3 news.py --no-moves                       # skip the price line per ticker

Your tickers, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json.

Sources:
  - TradingView's headlines for each ticker (one request each), through
    trader-egx/egx_data.py: Reuters (including EGX disclosures such as stake
    changes, marked "- EGX"), Zawya, company results write-ups, and the
    company's own regulatory filings where it has a London listing (LSE "REG -").
    https://news-headlines.tradingview.com/v2/headlines?symbol=EGX:<TICKER>
  - Daily News Egypt business RSS (one request), matched to your tickers by
    company name: https://www.dailynewsegypt.com/category/business/feed/
  - The move: TradingView's quote for each ticker (one connection for all),
    the last price against yesterday's close. 15 MINUTES LATE.

Important items get a "!": a disclosure to EGX, an LSE regulatory filing,
results, a stake change, a tender offer, a capital increase or a trading halt.

Times print in Cairo time. State: trader/state/news.json remembers what you
have seen (30 days), so an item shows once. Offline: --offline DIR reads
tv-<TICKER>.json, tradingview-<TICKER>.json (quote) and dne-business.xml from
DIR. The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice. Not financial advice.
"""

import argparse
import datetime as _dt
import email.utils
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-egx"))
import egx_data as E  # noqa: E402

DNE_URL = "https://www.dailynewsegypt.com/category/business/feed/"
UA = E.UA
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving

KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bprofits?\b|\bEPS\b|\bguidance\b|\brevenues?\b|\b[1-4]Q\d\d\b|\bQ[1-4]\b|\b9M\b|\bH[12]\b"),
    ("disclosure", r"-\s*EGX\b|\bREG\s*-|\bdisclos"),
    ("stake", r"\bstake\b|\bshareholding\b|\bmandatory tender offer\b|\btender offer\b|\bMTO\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bdeal\b|\bIPO\b"),
    ("capital", r"\bdividends?\b|\bbuyback\b|\btreasury shares\b|\bcapital (?:increase|reduction)\b|\bstock split\b|"
                r"\bbonus shares\b|\bsecuriti[sz]ation\b|\bbond\b|\bsukuk\b"),
    ("meeting", r"\bEGM\b|\bOGM\b|\bgeneral (?:assembly|meeting)\b|\bboard\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint"),
    ("regulator", r"\bFRA\b|\bFinancial Regulatory Authority\b|\bEGX\b|\bsuspen|\bhalt"),
    ("risk", r"\blawsuit\b|\bcourt\b|\bdefault\b|\bdelist|\bfine[sd]?\b|\bloss(?:es)?\b"),
    ("macro", r"\bCBE\b|\bCentral Bank of Egypt\b|\binterest rates?\b|\binflation\b|\bIMF\b|\bdevaluation\b|"
              r"\bexchange rate\b|\bT-bills?\b"),
]
IMPORTANT = re.compile(r"-\s*EGX\b|\bREG\s*-|\bresults?\b|\bprofits?\b|\bstake\b|\btender offer\b|"
                       r"\bcapital increase\b|\bsuspen|\bhalt", re.I)


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_local(utc):
    """An aware or naive UTC datetime -> naive Cairo time."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return E.to_cairo(utc)


def parse_time(value):
    """RFC 822, ISO 8601 or unix seconds -> aware UTC datetime (None when unknown).
    An ISO time without an offset is Cairo time."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return _dt.datetime.fromtimestamp(int(value), _dt.timezone.utc)
    v = str(value).strip()
    try:
        d = email.utils.parsedate_to_datetime(v)
        if d is not None:
            return d.astimezone(_dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return E.cairo_to_utc(d).replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


# ---------------------------------------------------------------- files

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[sym.upper()] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[sym.upper()] = p.get("name") or out.get(sym.upper())
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """Live: one attempt per request. Offline: files from a folder."""

    def __init__(self, offline=None):
        self.offline = offline

    def _file(self, name):
        p = os.path.join(self.offline, name)
        if not os.path.exists(p):
            raise NewsError("no file %s" % p)
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def headlines(self, ticker):
        if self.offline:
            return E.parse_news(self._file("tv-%s.json" % ticker))
        try:
            return E.headlines(ticker)
        except E.EGXDataError as e:
            raise NewsError(str(e))

    def dne(self):
        if self.offline:
            return self._file("dne-business.xml")
        req = urllib.request.Request(DNE_URL, headers={"User-Agent": UA, "Accept": "application/rss+xml"})
        try:
            with urllib.request.urlopen(req, timeout=E.TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise NewsError("www.dailynewsegypt.com answered HTTP %d" % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise NewsError("could not reach www.dailynewsegypt.com: %s" % getattr(e, "reason", e))

    def quotes(self, tickers):
        if self.offline:
            ok, errors = {}, {}
            for t in tickers:
                try:
                    ok[t] = json.loads(self._file("tradingview-%s.json" % t))
                except NewsError as e:
                    errors[t] = str(e)
            return {"ok": ok, "errors": errors}
        try:
            return E.quote(tickers)
        except E.EGXDataError as e:
            raise NewsError(str(e))


def items_from_tv(heads, ticker):
    out = []
    for h in heads:
        out.append({"id": "tv:" + (h["id"] or h["url"] or h["title"]), "ticker": ticker, "kind": "headline",
                    "source": h["provider"] or "tradingview", "title": h["title"], "summary": "",
                    "link": h["url"], "time": parse_time(h["published"]) if h["published"] else None,
                    "symbols": [s.split(":")[-1] for s in h["symbols"] if s.startswith("EGX:")]})
    return out


def parse_dne(data):
    """Daily News Egypt RSS -> items (ticker filled in by tag())."""
    try:
        root = ET.fromstring(data.encode("utf-8") if isinstance(data, str) else data)
    except ET.ParseError as exc:
        raise NewsError("Daily News Egypt sent something that is not RSS: %s" % exc)
    out = []
    for it in root.findall("./channel/item"):
        title = " ".join((it.findtext("title") or "").split())
        if not title:
            continue
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        out.append({"id": "dne:" + ((it.findtext("guid") or "").strip() or link or title), "ticker": "",
                    "kind": "headline", "source": "dailynewsegypt", "title": title,
                    "summary": " ".join(desc.split())[:400], "link": link,
                    "time": parse_time(it.findtext("pubDate")), "symbols": []})
    return out


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    try:
        r = fetcher.quotes(sorted(watch))
    except NewsError as exc:
        errors.append("prices: %s" % exc)
        return []
    out = []
    for t in sorted(watch):
        f = r["ok"].get(t)
        if not f:
            if t in r["errors"] and not (fetcher.offline and "no file" in r["errors"][t]):
                errors.append("price for %s: %s" % (t, r["errors"][t]))
            continue
        lp, pc = f.get("lp"), f.get("prev_close_price")
        if lp is None or not pc:
            continue
        t_ = f.get("lp_time")
        out.append({"ticker": t, "price": float(lp), "prev_close": float(pc),
                    "pct": round(100.0 * (float(lp) / float(pc) - 1), 2),
                    "time": parse_time(t_) if t_ else None, "delay_seconds": E.delay_seconds(f.get("update_mode")),
                    "name": f.get("description")})
        if f.get("description") and not watch.get(t):
            watch[t] = f.get("description")
    return out


def short_name(name):
    """"Commercial International Bank - Egypt (CIB) S.A.E." -> "Commercial International Bank"."""
    n = re.split(r"\s+-\s+|\(|,|\bS\.?A\.?E\b", name or "")[0]
    n = re.sub(r"\b(Company|Co\.?|Holding|Group|For|Of|The|Egypt|Egyptian|Inc\.?|Corp\.?)\b\s*$", "", n.strip(),
               flags=re.I).strip()
    return n


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [r"\(%s\)" % re.escape(t), r"\bEGX\s*:\s*%s\b" % re.escape(t)]
        sn = short_name(name)
        if sn and len(sn) >= 4:
            alts.append(r"\b%s\b" % re.escape(sn).replace(r"\ ", r"[\s-]"))
        abbr = re.search(r"\(([A-Z]{2,6})\)", name or "")
        if abbr and abbr.group(1) != t:
            alts.append(r"\b%s\b" % re.escape(abbr.group(1)))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your tickers each item touches, why it matters, and whether it is important."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        touches.update(s for s in it.get("symbols", []) if s in watch)
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        it["important"] = bool(IMPORTANT.search(it["title"]))
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, local=True, errors=None):
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        try:
            items += items_from_tv(fetcher.headlines(t), t)
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    if local:
        try:
            items += [it for it in parse_dne(fetcher.dne())]
        except NewsError as exc:
            errors.append("Daily News Egypt: %s" % exc)
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window that touch your tickers; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if not it.get("touches"):
            continue
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def _px(x):
    return ("{:,.3f}" if 0 < x < 1 else "{:,.2f}").format(x)


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s Cairo" % to_local(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        late = " (15 min late)" if m.get("delay_seconds") else ""
        out.append("%-6s EGP %s %+.2f%% vs yesterday's close %s%s%s  %s" % (
            m["ticker"], _px(m["price"]), m["pct"], _px(m["prev_close"]), when, late, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_local(it["time"]).strftime("%Y-%m-%d %H:%M Cairo") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-15s %s" % (mark, when, who, it["source"][:15], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        if it["link"]:
            extra.append(it["link"])
        if extra:
            out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_local(now).strftime("%Y-%m-%d %H:%M Cairo"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="News monitor for trader-news: EGX headlines and disclosures.")
    ap.add_argument("--tickers", nargs="*", help="default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-local", action="store_true", help="skip Daily News Egypt")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="time, for reproducible runs (no offset = Cairo)")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        watch = {t.upper(): None for t in args.tickers}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)   # also learns company names
    items = collect(fetcher, watch, local=not args.no_local, errors=errors)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
