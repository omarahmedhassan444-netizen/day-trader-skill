#!/usr/bin/env python3
"""Chart Scanner for trader-scanner: scan every EGX chart for setups.

    python3 scan.py universe                          # every EGX stock -> trader/universe.txt
    python3 scan.py daily                             # before the open: daily setups -> trader/watchlist.json
    python3 scan.py daily --symbols COMI,ETEL,HRHO    # or --watchlist FILE, or --limit 100
    python3 scan.py orb                               # after 10:20 Cairo: opening-range breakouts so far
    python3 scan.py orb --at 10:25 --or-minutes 15    # cut the clock at 10:25 Cairo
    python3 scan.py orb --offline wall5m.json.gz --now "2026-11-03 10:25" --min-price 0

universe  TradingView's stock screener for EGX (scanner.tradingview.com, one
          request): every common stock and depositary receipt listed on the
          Egyptian Exchange, most traded first. One SYMBOL|Name a line.

daily     Pre-market scan of daily CLOSES (TradingView 1-day bars, 90 sessions,
          through trader-egx/egx_data.py, about 50 tickers a connection).
          Liquidity: the last close >= --min-price (1 EGP) and the average
          volume of the last 10 sessions >= --min-volume (100,000 shares).
          Setups, from closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Today's daily bar is dropped until the session's close.

orb       Opening-range breakouts, 5-minute bars. The range is the first
          --or-minutes (15) of the session (10:00, 10:05 and 10:10 bars). A hit
          is the first completed bar after the range to CLOSE above the range
          high, at that bar's close time. EGX is thinner than New York: a
          5-minute stretch with no trades has no bar, so a chart is usable when
          its first bar (10:00) is there and at least 80 % of the bars up to
          the cutoff traded. Floors: --min-price 1 (last close), --min-volume 0.

Bars: a bar counts only once it is complete (start + 5 minutes <= the clock).
Times are Cairo. --now pins the clock ("2026-11-03 10:25", Cairo; ...Z for UTC).

Data: TradingView, free and DELAYED 15 MINUTES without a login. A breakout
the scanner sees happened at least 15 minutes ago: check the live price in
your broker app. Tickers that fail are listed, never dropped silently.

--offline PATH reads files instead of the network: universe wants a scanner
JSON (scan-egypt.json); daily and orb take one bundle file
{"COMI": <chart json>, ...} or a folder of <T>.json chart files.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Not financial advice. Exit code 0 when the scan found something, 1 when it
did not or could not run.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-egx"))
import egx_data as E  # noqa: E402

BAR_MIN = 5
BATCH = 50          # tickers per TradingView connection
MIN_DAILY_BARS = 30
MIN_FILL = 0.80     # share of 5-minute bars that must have traded for an orb chart to count
DEFAULT_UNIVERSE = "trader/universe.txt"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
OPEN = E.DEFAULT_OPEN
CLOSE = E.DEFAULT_CLOSE

SETUPS = [
    ("breakout_20d", "20-day breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- time

to_local = E.to_cairo
local_to_utc = E.cairo_to_utc


def ts_to_local(ts):
    return to_local(E.utc_of(ts))


def parse_now(text):
    if not text:
        return E.utc_now()
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ScanError('--now wants a time like "2026-11-03 10:25" (Cairo) or ...Z (UTC)')
    if dt.tzinfo is None:
        return local_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants a Cairo time like 10:25, not %r" % (flag, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def trading_day(d):
    return E.trading_day(d)


def session(d):
    return E.session(d)


def clock_label(t):
    return "%02d:%02d" % (t.hour, t.minute)


def day_label(d):
    return d.strftime("%a %Y-%m-%d")


# ---------------------------------------------------------------- data

def series_from_chart(doc):
    """Chart JSON (egx_data's Yahoo-v8 layout) -> {"t","o","h","l","c","v","meta"}."""
    try:
        r = doc["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        raise ScanError("a chart is not in the expected shape")
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    return {"t": list(r.get("timestamp") or []), "o": q.get("open") or [], "h": q.get("high") or [],
            "l": q.get("low") or [], "c": q.get("close") or [], "v": q.get("volume") or [],
            "meta": r.get("meta") or {}}


def series_from_egx(ser):
    bars = ser["bars"]
    return {"t": [b["t"] for b in bars], "o": [b["o"] for b in bars], "h": [b["h"] for b in bars],
            "l": [b["l"] for b in bars], "c": [b["c"] for b in bars], "v": [b["v"] for b in bars],
            "meta": {"symbol": ser["symbol"], "delaySeconds": ser.get("delay_seconds")}}


def fetch_many(symbols, interval, n_bars, a=None):
    """-> ({SYM: series}, {SYM: error}), BATCH tickers a connection, one attempt each."""
    data, failed = {}, {}
    for k in range(0, len(symbols), BATCH):
        part = symbols[k:k + BATCH]
        try:
            r = E.chart(part, interval, n_bars)
        except E.EGXDataError as e:
            for s in part:
                failed[s] = str(e)
            continue
        for s, ser in r["ok"].items():
            data[s] = series_from_egx(ser)
        failed.update(r["errors"])
        if a is not None and len(symbols) > BATCH:
            progress(a, "  %d of %d tickers" % (min(k + BATCH, len(symbols)), len(symbols)))
    return data, failed


def _read_maybe_gz(path):
    return E.read_text(path)


def load_offline(path):
    """A bundle {SYM: chart json} (.json or .json.gz) or a folder of <T>.json charts."""
    data, failed = {}, {}
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            m = re.match(r"^([A-Z0-9]+)\.json(\.gz)?$", name)
            if m:
                data[m.group(1)] = series_from_chart(json.loads(_read_maybe_gz(os.path.join(path, name))))
        return data, failed
    if not os.path.exists(path):
        raise ScanError("no offline file %s" % path)
    doc = json.loads(_read_maybe_gz(path))
    if isinstance(doc, dict) and "chart" in doc:
        s = series_from_chart(doc)
        data[str(s["meta"].get("symbol") or "X").upper()] = s
        return data, failed
    for sym, d in (doc or {}).items():
        if isinstance(d, dict) and "chart" in d:
            data[sym.upper()] = series_from_chart(d)
        else:
            failed[sym.upper()] = str(d)
    return data, failed


# ---------------------------------------------------------------- the stock list

def build_universe(rows):
    """Scanner rows -> [(SYM, name)] of EGX stocks, most traded first (by the 10-day average volume x price)."""
    out = []
    for r in rows:
        if r.get("type") not in ("stock", "dr"):
            continue
        sym = str(r.get("symbol") or "").upper()
        if not re.match(r"^[A-Z0-9]{1,8}$", sym):
            continue
        val = (r.get("close") or 0) * (r.get("average_volume_10d_calc") or r.get("volume") or 0)
        out.append((val, sym, " ".join(str(r.get("description") or "").split())))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [(s, n) for _, s, n in out]


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_bars(series, now_utc):
    """[(date, close, volume)] of COMPLETE daily bars: today's bar dropped until EGX closes."""
    now = to_local(now_utc)
    today = now.date()
    s = session(today)
    today_done = s is None or now.time() >= s[1]
    out = {}
    for i, ts in enumerate(series["t"]):
        c = series["c"][i] if i < len(series["c"]) else None
        if c is None:
            continue
        d = ts_to_local(ts).date()
        if d > today or (d == today and not today_done):
            continue
        v = series["v"][i] if i < len(series["v"]) else 0
        out[d] = (float(c), v or 0)
    return [(d, c, v) for d, (c, v) in sorted(out.items())]


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def scan_daily(data, symbols, now_utc, min_price, min_volume):
    """Pure. data = {SYM: series}. Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_bars(s, now_utc)
        if not bars:
            counts["short"] += 1
            continue
        price = bars[-1][1]
        vols = [v for _, _, v in bars[-10:]]
        avg_vol = sum(vols) / float(len(vols)) if vols else 0
        if price < min_price or (min_volume > 0 and avg_vol < min_volume):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c, _ in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 3 if closes[-1] < 1 else 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "volume": int(avg_vol), "level": round(nums["high20"], 3 if closes[-1] < 1 else 2)})
    hits.sort(key=lambda h: (-(h["price"] * (h["volume"] or 0)), h["symbol"]))  # most pounds traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` complete at `cutoff` (naive Cairo time), on the grid,
    keyed by slot. -> {slot: bar}."""
    s = session(day)
    close_t = s[1] if s else CLOSE
    out = {}
    for i, ts in enumerate(series["t"]):
        c = series["c"][i] if i < len(series["c"]) else None
        if c is None:
            continue
        lt = ts_to_local(ts)
        if lt.date() != day or lt.second or lt.minute % BAR_MIN:
            continue
        if lt.time() < OPEN or lt.time() >= close_t:
            continue
        if lt + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue
        k = int((lt - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
        hs, ls, vs = series.get("h") or [], series.get("l") or [], series.get("v") or []
        out[k] = {"start": lt, "slot": k, "c": float(c),
                  "h": float(hs[i]) if i < len(hs) and hs[i] is not None else float(c),
                  "l": float(ls[i]) if i < len(ls) and ls[i] is not None else float(c),
                  "v": (vs[i] or 0) if i < len(vs) else 0}
    return out


def usable(bars, day, cutoff, n_range):
    """The 10:00 bar is there and at least MIN_FILL of the bars up to the cutoff traded."""
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    s = session(day)
    if s:
        want = min(want, int((datetime.datetime.combine(day, s[1]) - slot(day, 0)).total_seconds()
                             // (BAR_MIN * 60)))
    if want <= n_range or 0 not in bars:
        return False
    return len(bars) >= MIN_FILL * want


def orb_hit(bars, n_range):
    """The first completed bar after the range to CLOSE above the range high."""
    rng = [bars[k] for k in sorted(bars) if k < n_range]
    if not rng:
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    nd = 3 if oh < 1 else 2
    for k in sorted(bars):
        if k < n_range:
            continue
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], nd), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, nd), "or_low": round(ol, nd), "level": round(oh, nd),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff naive Cairo) or raise ScanError with the plain message."""
    now = to_local(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No EGX session on %s (%s). Run orb on a trading day (Sunday to Thursday) after the "
                        "opening range, or replay a past day with --offline." % (day_label(day), why))
    s = session(day)
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, s[1])
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s Cairo)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the %s close on %s." % (at.strftime("%H:%M"), clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("EGX closed at %s Cairo on %s. Replay the session with --at 10:25."
                            % (clock_label(close_dt), day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s Cairo: the %d-minute opening range runs 10:00 to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s Cairo, after %s, your last time for new entries (no_entries_after): a "
                        "breakout found now is expired. Run orb tomorrow after %s, or replay today with --at 10:25."
                        % (clock_label(cutoff), until.strftime("%H:%M"), clock_label(first_close)))
    return day, cutoff


def scan_orb(data, symbols, day, cutoff, or_minutes, min_price=0.0, min_volume=0):
    """data = {SYM: series} with highs and lows. Returns (hits, counts)."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0}
    hits = []
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        last = bars[max(bars)]
        vol = sum(b["v"] for b in bars.values())
        if last["c"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        h = orb_hit(bars, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"], "volume": int(vol)}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts


# ---------------------------------------------------------------- files

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan.py universe` first, or pass --symbols" % path)
        syms = symbols_from_file(path)
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def money(x):
    return ("EGP {:,.3f}" if 0 < x < 1 else "EGP {:,.2f}").format(x)


def _px(x):
    return ("%.3f" if 0 < float(x) < 1 else "%.2f") % float(x)


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def _failed_lines(failed, show=10):
    if not failed:
        return []
    items = sorted(failed.items())
    out = ["%d ticker%s failed (listed, not dropped):" % (len(items), "" if len(items) == 1 else "s")]
    for s, e in items[:show]:
        out.append("  %-6s %s" % (s, e))
    if len(items) > show:
        out.append("  ... and %d more (--json lists them all)." % (len(items) - show))
    return out


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    if a.offline:
        if not os.path.exists(a.offline):
            raise ScanError("no offline file %s" % a.offline)
        rows = E.parse_scan(_read_maybe_gz(a.offline))
    else:
        try:
            rows = E.universe()
        except E.EGXDataError as e:
            raise ScanError(str(e))
    kept = build_universe(rows)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    if a.json:
        print(json.dumps({"count": len(kept), "rows": len(rows), "out": None if a.no_write else a.out}, indent=2))
    else:
        print("TradingView's EGX screener: %d rows, %d stocks kept (most traded first)." % (len(rows), len(kept)))
        if kept:
            print("Top 5: " + ", ".join("%s (%s)" % (s, n[:30]) for s, n in kept[:5]) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        progress(a, "Fetching daily bars for %d EGX tickers (TradingView, %d connection%s)..."
                 % (len(symbols), (len(symbols) + BATCH - 1) // BATCH, "" if len(symbols) <= BATCH else "s"))
        data, failed = fetch_many(symbols, "1d", 90, a)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_volume)
    failed = {s: e for s, e in (failed or {}).items() if s in symbols}
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "market": "EGX", "currency": "EGP", "as_of": as_of, "universe": len(symbols),
           "scanned": counts["scanned"], "failed": len(failed), "failed_symbols": failed, "source": "tradingview",
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    local = to_local(now)
    print("Daily scan, %s Cairo, closes through %s (TradingView daily bars)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %d of %d charts. %d passed the liquidity filter (EGP %s+ and %s+ shares a day on average)%s; "
          "%d scanned." % (counts["fetched"], len(symbols), counts["fetched"] - counts["illiquid"], _n(a.min_price),
                           "{:,}".format(a.min_volume),
                           (", %d had under %d sessions" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
                           counts["scanned"]))
    for line in _failed_lines(failed):
        print(line)
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (price x volume):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-6s %14s  RSI %5.1f  %s" % (h["symbol"], money(h["price"]), h["rsi"],
                                              ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news TICKER.")
    return 0 if hits else 1


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or "14:00", "--until")
    data = failed = None
    explicit_day = None
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_local(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        today = to_local(parse_now(None)).date()
        back, d = 0, day
        while d < today:
            if session(d):
                back += 1
            d += datetime.timedelta(days=1)
        n = E.bars_for_days("5m", back + 1)
        progress(a, "Fetching 5-minute bars for %d EGX tickers (TradingView, %d connection%s)..."
                 % (len(symbols), (len(symbols) + BATCH - 1) // BATCH, "" if len(symbols) <= BATCH else "s"))
        data, failed = fetch_many(symbols, "5m", n, a)
    hits, counts = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume)
    failed = {s: e for s, e in (failed or {}).items() if s in symbols}
    as_of = "%s %s Cairo" % (day.isoformat(), cutoff.strftime("%H:%M"))
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "market": "EGX", "currency": "EGP", "as_of": as_of, "universe": len(symbols),
           "scanned": counts["scanned"], "failed": len(failed), "failed_symbols": failed, "source": "tradingview",
           "or_minutes": a.or_minutes, "interval": "%dm" % BAR_MIN,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    print("Opening-range breakout scan, %s %s Cairo: %d-minute range (10:00 to %s), 5-minute bars, "
          "TradingView (15 min late)." % (day_label(day), clock_label(cutoff), a.or_minutes,
                                          clock_label(slot(day, a.or_minutes // BAR_MIN))))
    print("Fetched %d of %d charts; %d usable%s%s." % (
        counts["fetched"], len(symbols), counts["scanned"],
        (", %d too thin (under %d%% of bars traded)" % (counts["unusable"], int(MIN_FILL * 100)))
        if counts["unusable"] else "",
        (", %d under the price / volume floor" % counts["illiquid"]) if counts["illiquid"] else ""))
    for line in _failed_lines(failed):
        print(line)
    if hits:
        print("%d of %d charts closed above their opening-range high by %s:"
              % (len(hits), counts["scanned"], clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %6s  %-6s %14s  range %s to %s  %+.2f%% over" % (
                h["at"], h["symbol"], money(h["price"]), _px(h["or_low"]), _px(h["or_high"]), h["pct_over"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news TICKER and /trader-entry TICKER.")
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (trader-scanner): EGX daily setups and opening-range "
                                             "breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="every EGX stock -> trader/universe.txt")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--offline", metavar="FILE", help="a saved scanner JSON instead of the network")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_volume):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated tickers instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of tickers")
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price, help="EGP")
        p.add_argument("--min-volume", type=int, default=min_volume, help="shares")
        p.add_argument("--offline", metavar="PATH", help="fixture bundle or folder instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-11-03 10:25" Cairo, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="pre-market setups from daily closes")
    common(p, 1.0, 100000)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    common(p, 1.0, 0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--at", help="cut the clock at this Cairo time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config no_entries_after, 14:00)")
    p.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
