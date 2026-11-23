#!/usr/bin/env python3
"""EGX data for every tool in this pack: prices, the session calendar, the
stock list, fundamentals and headlines for the Egyptian Exchange (EGX).

ALL price fetching goes through this file. The other tools import it.

    python3 egx_data.py status                         # is EGX open, and how late is the data?
    python3 egx_data.py chart COMI --interval 5m --bars 60
    python3 egx_data.py chart COMI ETEL HRHO --interval 1d --bars 30
    python3 egx_data.py chart COMI --interval 5m --bars 3500 --save COMI-5m.json.gz
    python3 egx_data.py quote COMI                      # last price, EPS, next earnings date
    python3 egx_data.py universe                        # every EGX stock, with volume
    python3 egx_data.py calendar                        # today's session and the holiday list
    python3 egx_data.py calendar --refresh              # re-read hours and holidays from TradingView

Data: TradingView. Without a login, the public feed.
  prices, session, holidays  wss://data.tradingview.com/socket.io/websocket
  stock list, screening      https://scanner.tradingview.com/egypt/scan
  headlines                  https://news-headlines.tradingview.com/v2/headlines
Without a TradingView login the EGX feed is DELAYED 15 MINUTES (TradingView
reports "delayed_streaming_900"). With your own TradingView account and its
Egyptian Exchange real-time subscription ($14.95 a month on TradingView's data
page), run `egx_data.py login` once on your own computer and the same tools
get real-time data ("streaming"). Every result says how late it is.

    python3 egx_data.py login                           # save your TradingView session (hidden input)
    python3 egx_data.py logout                          # remove it
The session is kept in ~/.config/egx-trader/tradingview.json (owner-only),
or the path in EGX_TV_CREDENTIALS. Never in the repo, never in a chat.

Rules this file keeps:
  - One attempt per request. No retry loops. A failure names the host and the
    reason, and the caller decides what to do.
  - A stock that fails is reported, never silently dropped: chart() returns
    {"ok": {...}, "errors": {...}} and the tools print the errors.
  - Times: Africa/Cairo (UTC+2, UTC+3 in summer time). The rule is built in
    (no zoneinfo), so it runs on Python 3.8.

Needs the websocket-client package for prices and quotes:  pip install websocket-client
The stock list and headlines use the standard library only.
"""

import argparse
import datetime
import gzip
import json
import os
import random
import re
import string
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CALENDAR_FILE = os.path.join(HERE, "calendar.json")      # shipped with the pack
LOCAL_CALENDAR = os.path.join("trader", "egx_calendar.json")   # written by calendar --refresh

WS_HOST = "data.tradingview.com"
PRO_HOST = "prodata.tradingview.com"    # where TradingView sends signed-in, paid users
WS_URL = "wss://%s/socket.io/websocket" % WS_HOST
SCAN_HOST = "scanner.tradingview.com"
NEWS_HOST = "news-headlines.tradingview.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TIMEOUT = 20
MAX_BARS = 5000          # TradingView's limit per request

INTERVALS = {"1m": ("1", 60), "5m": ("5", 300), "15m": ("15", 900), "30m": ("30", 1800),
             "60m": ("60", 3600), "1d": ("1D", 86400), "1wk": ("1W", 604800)}
INTRADAY = ("1m", "5m", "15m", "30m", "60m")

EXCHANGE = "EGX"
CURRENCY = "EGP"
TZ_NAME = "Africa/Cairo"
EPOCH = datetime.datetime(1970, 1, 1)

# the session when no calendar file says otherwise
DEFAULT_OPEN = datetime.time(10, 0)
DEFAULT_CLOSE = datetime.time(14, 30)
TRADING_WEEKDAYS = (6, 0, 1, 2, 3)      # Python weekday(): Sunday=6, Monday=0 ... Thursday=3


class EGXDataError(Exception):
    """A request failed. The message names the host and the reason."""


# ---------------------------------------------------------------- Cairo time

def _last_weekday(year, month, weekday):
    """The last `weekday` (Python numbering, Monday=0) of a month."""
    if month == 12:
        d = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        d = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    return d - datetime.timedelta(days=(d.weekday() - weekday) % 7)


def cairo_offset(utc):
    """Hours from UTC for Cairo: +3 in summer time, +2 otherwise.
    Egypt brought summer time back in 2023: it starts at 00:00 on the last
    Friday of April and ends at 24:00 on the last Thursday of October.
    From 2015 to 2022 Egypt kept +2 all year."""
    y = utc.year
    if y < 2023:
        return 2
    start = datetime.datetime.combine(_last_weekday(y, 4, 4), datetime.time(0, 0)) - datetime.timedelta(hours=2)
    end = datetime.datetime.combine(_last_weekday(y, 10, 3), datetime.time(0, 0)) + datetime.timedelta(hours=21)
    naive = utc.replace(tzinfo=None)
    return 3 if start <= naive < end else 2


def to_cairo(utc):
    """Naive UTC -> naive Cairo wall-clock time."""
    return utc.replace(tzinfo=None) + datetime.timedelta(hours=cairo_offset(utc))


def cairo_to_utc(local):
    """Naive Cairo wall-clock time -> naive UTC."""
    for off in (3, 2):
        utc = local - datetime.timedelta(hours=off)
        if cairo_offset(utc) == off:
            return utc
    return local - datetime.timedelta(hours=2)    # the skipped hour at the switch


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc.replace(tzinfo=None) - EPOCH).total_seconds())


# ---------------------------------------------------------------- the session calendar

def _hhmm(s):
    return datetime.time(int(s[:2]), int(s[2:4]))


def _tv_days(spec):
    """TradingView day digits (1=Sunday ... 7=Saturday) -> Python weekdays."""
    return tuple(sorted(((int(ch) + 5) % 7) for ch in spec if ch.isdigit()))


def parse_tv_session(text):
    """TradingView's session string -> [{"until": "YYYY-MM-DD" or None, "open", "close", "days"}].
    "1;1000-1430:12345#20220403/1;1000-1330:12345#20220424/1;1000-1430:12345" means
    10:00-14:30 Sunday to Thursday before 2022-04-03, 10:00-13:30 from then until
    2022-04-24, and 10:00-14:30 after that."""
    out = []
    for seg in (text or "").split("/"):
        seg = seg.strip()
        if not seg:
            continue
        until = None
        if "#" in seg:
            seg, until = seg.split("#", 1)
            until = "%s-%s-%s" % (until[:4], until[4:6], until[6:8])
        if ";" in seg:
            seg = seg.split(";", 1)[1]
        m = re.match(r"^(\d{4})-(\d{4}):(\d+)$", seg.split(",")[0])
        if not m:
            continue
        out.append({"until": until, "open": m.group(1)[:2] + ":" + m.group(1)[2:],
                    "close": m.group(2)[:2] + ":" + m.group(2)[2:], "days": list(_tv_days(m.group(3)))})
    return out


def parse_tv_corrections(text):
    """"1000-1330:20260219;1000-1430:20240310" -> {"2026-02-19": ["10:00", "13:30"], ...}"""
    out = {}
    for part in (text or "").split(";"):
        m = re.match(r"^\s*(\d{4})-(\d{4}):([\d,]+)\s*$", part)
        if not m:
            continue
        for d in m.group(3).split(","):
            out["%s-%s-%s" % (d[:4], d[4:6], d[6:8])] = [m.group(1)[:2] + ":" + m.group(1)[2:],
                                                         m.group(2)[:2] + ":" + m.group(2)[2:]]
    return out


def calendar_from_symbol_info(info, fetched_at=None):
    """A calendar document from TradingView's symbol info (symbol_resolved or a quote)."""
    session = info.get("session")
    for sub in info.get("subsessions") or []:
        if sub.get("id") == "regular" and sub.get("session"):
            session = sub["session"]
            corrections = sub.get("session-correction")
            break
    else:
        corrections = info.get("session-correction")
    hol = sorted("%s-%s-%s" % (d[:4], d[4:6], d[6:8])
                 for d in (info.get("session_holidays") or "").split(",") if re.match(r"^\d{8}$", d))
    return {"source": "TradingView symbol info for EGX", "fetched_at": fetched_at,
            "timezone": info.get("timezone") or TZ_NAME, "sessions": parse_tv_session(session),
            "corrections": parse_tv_corrections(corrections), "holidays": hol,
            "known_through": hol[-1][:4] if hol else None}


_CAL = {}


def load_calendar(path=None):
    """The calendar: trader/egx_calendar.json when calendar --refresh wrote one, else the shipped file."""
    for p in ([path] if path else [LOCAL_CALENDAR, CALENDAR_FILE]):
        if p and os.path.exists(p):
            if p in _CAL:
                return _CAL[p]
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["_path"] = p
            _CAL[p] = doc
            return doc
    return {"sessions": [], "corrections": {}, "holidays": [], "known_through": None, "_path": None}


def calendar_known(day, cal=None):
    """True when the holiday list is known to cover this date's year."""
    cal = cal or load_calendar()
    k = cal.get("known_through")
    return bool(k) and day.year <= int(k)


def trading_day(day, cal=None):
    """(is_trading_day, reason) for a Cairo date."""
    cal = cal or load_calendar()
    if day.weekday() not in TRADING_WEEKDAYS:
        return False, "weekend (EGX trades Sunday to Thursday)"
    if day.isoformat() in set(cal.get("holidays") or []):
        return False, "EGX holiday"
    return True, ""


def session(day, cal=None):
    """(open_time, close_time) in Cairo time, or None when EGX is closed all day."""
    cal = cal or load_calendar()
    ok, _ = trading_day(day, cal)
    if not ok:
        return None
    iso = day.isoformat()
    fix = (cal.get("corrections") or {}).get(iso)
    if fix:
        return _hhmm(fix[0].replace(":", "")), _hhmm(fix[1].replace(":", ""))
    for seg in cal.get("sessions") or []:
        if seg.get("until") is None or iso < seg["until"]:
            if day.weekday() not in seg.get("days", TRADING_WEEKDAYS):
                return None
            return _hhmm(seg["open"].replace(":", "")), _hhmm(seg["close"].replace(":", ""))
    return DEFAULT_OPEN, DEFAULT_CLOSE


def is_short_day(day, cal=None):
    """True on a shortened session (Ramadan: 10:00-13:30)."""
    s = session(day, cal)
    return bool(s) and s[1] < DEFAULT_CLOSE


def next_open(day, cal=None):
    """"YYYY-MM-DD HH:MM" of the next session after `day`, or None."""
    nd = day + datetime.timedelta(days=1)
    for _ in range(20):
        s = session(nd, cal)
        if s:
            return "%s %02d:%02d" % (nd.isoformat(), s[0].hour, s[0].minute)
        nd += datetime.timedelta(days=1)
    return None


# ---------------------------------------------------------------- symbols

def norm_symbol(sym):
    """"comi", "EGX:COMI", "COMI.CA" -> "COMI"."""
    s = str(sym).strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    if s.endswith(".CA"):
        s = s[:-3]
    if not re.match(r"^[A-Z0-9_.]{1,12}$", s):
        raise EGXDataError("%r is not an EGX ticker (want something like COMI or ETEL)" % sym)
    return s


# ---------------------------------------------------------------- TradingView socket

def _frame(m, p):
    s = json.dumps({"m": m, "p": p}, separators=(",", ":"))
    return "~m~%d~m~%s" % (len(s), s)


def split_frames(raw):
    """One websocket read -> its messages (dicts) and heartbeats ("~h~N")."""
    out = []
    i = 0
    while i < len(raw):
        m = re.match(r"~m~(\d+)~m~", raw[i:])
        if not m:
            break
        n = int(m.group(1))
        start = i + m.end()
        body = raw[start:start + n]
        i = start + n
        if body.startswith("~h~"):
            out.append(body)
            continue
        try:
            out.append(json.loads(body))
        except ValueError:
            continue
    return out


def _rand(prefix):
    return prefix + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


# ---------------------------------------------------------------- your TradingView login (real-time data)

WEB_HOST = "www.tradingview.com"
CRED_ENV = "EGX_TV_CREDENTIALS"     # a different path for the credentials file, if you want one
COOKIE_NAMES = ("sessionid", "sessionid_sign")
ANON_TOKEN = "unauthorized_user_token"
_AUTH = {"token": None, "user": None, "checked": False, "note": None}
LAST = {"delay": None}              # the delay TradingView reported on the last request


def cred_path():
    """Where the session cookie lives: ~/.config/egx-trader/tradingview.json (never in the repo)."""
    return os.environ.get(CRED_ENV) or os.path.join(os.path.expanduser("~"), ".config", "egx-trader",
                                                    "tradingview.json")


def load_cred(path=None):
    path = path or cred_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not doc.get("sessionid"):
        return None
    return {k: str(doc[k]) for k in COOKIE_NAMES if doc.get(k)}


def save_cred(cred, path=None):
    """Write the cookie with owner-only permissions (0600), in a 0700 folder."""
    path = path or cred_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({k: cred[k] for k in COOKIE_NAMES if cred.get(k)}, fh)
    os.chmod(path, 0o600)
    return path


def parse_auth_page(html):
    """TradingView's page -> (auth_token, username). The page sets `var user = {...}`; a signed-in
    browser's user object carries "auth_token", a signed-out one is "Guest" with none."""
    html = html or ""
    i = html.find("var user = ")
    if i < 0:
        return None, None
    try:
        user, _ = json.JSONDecoder().raw_decode(html[i + len("var user = "):])
    except ValueError:
        return None, None
    if not isinstance(user, dict):
        return None, None
    tok = user.get("auth_token")
    return (str(tok) if tok else None), user.get("username")


def fetch_auth_token(cred, timeout=TIMEOUT):
    """Swap the saved session cookie for a short-lived websocket token. Raises EGXDataError."""
    cookie = "; ".join("%s=%s" % (k, cred[k]) for k in COOKIE_NAMES if cred.get(k))
    req = urllib.request.Request("https://%s/" % WEB_HOST,
                                 headers={"User-Agent": UA, "Cookie": cookie, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        e.close()
        raise EGXDataError("%s answered HTTP %d" % (WEB_HOST, e.code))
    except (urllib.error.URLError, OSError) as e:
        raise EGXDataError("could not reach %s: %s" % (WEB_HOST, getattr(e, "reason", e)))
    tok, user = parse_auth_page(html)
    if not tok:
        raise EGXDataError("TradingView did not accept the saved session (signed out or expired): run "
                           "egx_data.py login again")
    return tok, user


def auth_token():
    """The token for the websocket: yours when a login is saved and still valid, else the anonymous
    (delayed) one. A failed login never stops the tools: they fall back to the delayed feed and say why."""
    if _AUTH["checked"]:
        return _AUTH["token"] or ANON_TOKEN
    _AUTH["checked"] = True
    cred = load_cred()
    if not cred:
        return ANON_TOKEN
    try:
        _AUTH["token"], _AUTH["user"] = fetch_auth_token(cred)
    except EGXDataError as e:
        _AUTH["note"] = str(e)
        print("egx_data: %s. Using the delayed feed." % e, file=sys.stderr)
    return _AUTH["token"] or ANON_TOKEN


def delay_phrase(seconds="last"):
    """" (15 min late)" for a delayed feed, "" for real-time. seconds="last" uses the last request's
    delay, or 15 minutes when nothing has been fetched yet and no login is saved."""
    if seconds == "last":
        seconds = LAST["delay"]
        if seconds is None:
            seconds = 0 if (_AUTH["token"] or (not _AUTH["checked"] and load_cred())) else 900
    if not seconds:
        return ""
    return " (%d min late)" % (seconds // 60)


def late_sentence(seconds="last"):
    """The sentence the tools print next to a price."""
    ph = delay_phrase(seconds)
    if not ph:
        return "Prices are real-time: still check your broker app before you act."
    return "Prices are %s late. Check the live price in your broker app before you act." % ph.strip(" ()").replace(
        " late", "")


def _connect():
    """The websocket. Signed in: the pro host first (TradingView's home for paid data), then the public one."""
    try:
        from websocket import create_connection
    except ImportError:
        raise EGXDataError("the websocket-client package is missing: pip install websocket-client")
    tok = auth_token()
    hosts = [PRO_HOST, WS_HOST] if tok != ANON_TOKEN else [WS_HOST]
    last = None
    for host in hosts:
        try:
            return create_connection("wss://%s/socket.io/websocket" % host, header=["Origin: https://%s" % WS_HOST],
                                     timeout=TIMEOUT)
        except Exception as e:     # proxy refusals, DNS, TLS: all name the host
            last = "could not reach %s: %s" % (host, e)
    raise EGXDataError(last)


def _converse(ws, wanted, on_msg, deadline=None):
    """Read until on_msg says every wanted key is done. Answers heartbeats.
    Stops at an overall deadline (TIMEOUT seconds by default): heartbeats keep a
    socket alive forever, so the deadline is what ends a request that never finishes."""
    import time as _time
    done = set()
    stop_at = _time.time() + (deadline or TIMEOUT)
    while done != wanted:
        if _time.time() > stop_at:
            missing = ", ".join(sorted(wanted - done))
            raise EGXDataError("%s did not finish %s within %d seconds" % (WS_HOST, missing, deadline or TIMEOUT))
        try:
            raw = ws.recv()
        except Exception as e:
            missing = ", ".join(sorted(wanted - done))
            raise EGXDataError("%s stopped answering before %s finished: %s" % (WS_HOST, missing, e))
        for f in split_frames(raw):
            if isinstance(f, str):
                ws.send("~m~%d~m~%s" % (len(f), f))
                continue
            key = on_msg(f)
            if key:
                done.add(key)


def parse_series(frames):
    """Test hook: the same reader chart() uses, over saved frames.
    -> {"bars": [...], "update_mode": str, "info": {...}, "error": str or None}"""
    out = {"bars": {}, "update_mode": None, "info": {}, "error": None}
    for f in frames:
        if not isinstance(f, dict):
            continue
        m, p = f.get("m"), f.get("p") or []
        if m in ("timescale_update", "du") and len(p) > 1:
            for sid, ser in (p[1] or {}).items():
                if isinstance(ser, dict):
                    for row in ser.get("s") or []:
                        v = row.get("v") or []
                        if len(v) >= 5:
                            out["bars"][int(v[0])] = v
        elif m == "symbol_resolved" and len(p) > 2:
            out["info"] = p[2]
        elif m == "series_completed" and len(p) > 2:
            out["update_mode"] = p[2]
        elif m in ("symbol_error", "series_error", "critical_error"):
            out["error"] = " ".join(str(x) for x in p[2:4]) or m
    bars = []
    for t in sorted(out["bars"]):
        v = out["bars"][t]
        bars.append({"t": int(v[0]), "o": float(v[1]), "h": float(v[2]), "l": float(v[3]), "c": float(v[4]),
                     "v": int(v[5]) if len(v) > 5 and v[5] is not None else 0})
    out["bars"] = bars
    return out


def delay_seconds(update_mode):
    """"delayed_streaming_900" -> 900; "streaming" -> 0; unknown -> None."""
    if not update_mode:
        return None
    m = re.search(r"delayed\w*?_(\d+)$", update_mode)
    if m:
        return int(m.group(1))
    return 0 if "streaming" in update_mode else None


def chart(symbols, interval="5m", n_bars=300):
    """Bars for one or more EGX tickers over ONE connection, one attempt.
    -> {"ok": {SYM: series}, "errors": {SYM: reason}}
    series = {"symbol", "interval", "bars": [{"t","o","h","l","c","v"}], "update_mode",
              "delay_seconds", "info"} with t = the bar's start in unix seconds (UTC)."""
    if interval not in INTERVALS:
        raise EGXDataError("interval must be one of %s" % ", ".join(INTERVALS))
    n_bars = max(1, min(int(n_bars), MAX_BARS))
    syms = []
    errors = {}
    for s in symbols:
        try:
            ns = norm_symbol(s)
            if ns not in syms:
                syms.append(ns)
        except EGXDataError as e:
            errors[str(s)] = str(e)
    if not syms:
        return {"ok": {}, "errors": errors}
    tv_iv = INTERVALS[interval][0]
    ws = _connect()
    frames = {}
    sess = {}
    try:
        ws.send(_frame("set_auth_token", [auth_token()]))
        for s in syms:
            cs = _rand("cs_")
            sess[cs] = s
            frames[s] = []
            ws.send(_frame("chart_create_session", [cs, ""]))
            ws.send(_frame("resolve_symbol", [cs, "sds_sym_1", "=" + json.dumps(
                {"symbol": "%s:%s" % (EXCHANGE, s), "adjustment": "splits", "session": "regular"})]))
            ws.send(_frame("create_series", [cs, "sds_1", "s1", "sds_sym_1", tv_iv, n_bars]))

        def on_msg(f):
            p = f.get("p") or []
            cs = p[0] if p and isinstance(p[0], str) else None
            s = sess.get(cs)
            if not s:
                return None
            frames[s].append(f)
            if f.get("m") in ("series_completed", "series_error", "critical_error"):
                return s
            return None

        _converse(ws, set(syms), on_msg)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    ok = {}
    for s in syms:
        r = parse_series(frames[s])
        if r["error"]:
            errors[s] = "TradingView: %s (is %s an EGX ticker?)" % (r["error"], s)
            continue
        if not r["bars"]:
            errors[s] = "TradingView sent no %s bars for %s" % (interval, s)
            continue
        ok[s] = {"symbol": s, "interval": interval, "bars": r["bars"], "update_mode": r["update_mode"],
                 "delay_seconds": delay_seconds(r["update_mode"]), "info": r["info"]}
        if ok[s]["delay_seconds"] is not None:
            LAST["delay"] = ok[s]["delay_seconds"]
    return {"ok": ok, "errors": errors}


def chart_one(sym, interval="5m", n_bars=300):
    """One ticker; raises EGXDataError instead of returning an error map."""
    r = chart([sym], interval, n_bars)
    s = norm_symbol(sym)
    if s in r["ok"]:
        return r["ok"][s]
    raise EGXDataError(r["errors"].get(s) or next(iter(r["errors"].values()), "no data for %s" % s))


def quote(symbols):
    """Everything TradingView's quote session knows for each ticker: last price
    (lp, lp_time), update_mode, session, holidays and about 1,400 fundamentals
    (earnings_per_share_fq_h, total_revenue_fq_h, net_income_fq_h, ...).
    -> {"ok": {SYM: fields}, "errors": {SYM: reason}}"""
    syms, errors = [], {}
    for s in symbols:
        try:
            ns = norm_symbol(s)
            if ns not in syms:
                syms.append(ns)
        except EGXDataError as e:
            errors[str(s)] = str(e)
    if not syms:
        return {"ok": {}, "errors": errors}
    qs = _rand("qs_")
    vals = dict((s, {}) for s in syms)
    status = {}
    ws = _connect()
    try:
        ws.send(_frame("set_auth_token", [auth_token()]))
        ws.send(_frame("quote_create_session", [qs]))
        ws.send(_frame("quote_add_symbols", [qs] + ["%s:%s" % (EXCHANGE, s) for s in syms]))
        seen = set()

        def on_msg(f):
            p = f.get("p") or []
            if f.get("m") == "qsd" and len(p) > 1 and isinstance(p[1], dict):
                n = str(p[1].get("n", "")).split(":")[-1]
                if n in vals:
                    status[n] = p[1].get("s")
                    vals[n].update(p[1].get("v") or {})
                    if status[n] == "error" and n not in seen:   # no quote_completed follows an error
                        seen.add(n)
                        return n
            elif f.get("m") == "quote_completed" and len(p) > 1:
                n = str(p[1]).split(":")[-1]
                if n in vals and n not in seen:
                    seen.add(n)
                    return n
            return None

        _converse(ws, set(syms), on_msg)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    ok = {}
    for s in syms:
        if status.get(s) == "error" or not vals[s]:
            errors[s] = "TradingView has no quote for %s (is it an EGX ticker?)" % s
        else:
            ok[s] = vals[s]
            d = delay_seconds(vals[s].get("update_mode"))
            if d is not None:
                LAST["delay"] = d
    return {"ok": ok, "errors": errors}


# ---------------------------------------------------------------- Yahoo-shaped charts for the tools

def to_chart_json(series, now_utc=None):
    """A series -> the chart JSON shape the tools read (Yahoo's v8 layout, so the
    tools' parsers and saved fixtures share one format)."""
    bars = series["bars"]
    step = INTERVALS[series["interval"]][1]
    last = bars[-1] if bars else None
    data_time = None
    if last:
        data_time = last["t"] + (step if series["interval"] in INTRADAY else 0)
        if now_utc is not None:
            data_time = min(data_time, ts_of(now_utc))
    prev = None
    if series["interval"] in INTRADAY and bars:
        prev = series.get("prev_close")
    meta = {"symbol": series["symbol"], "exchangeName": EXCHANGE, "currency": CURRENCY,
            "exchangeTimezoneName": TZ_NAME, "dataGranularity": series["interval"],
            "regularMarketTime": data_time, "regularMarketPrice": last["c"] if last else None,
            "previousClose": prev, "chartPreviousClose": prev, "source": "tradingview",
            "updateMode": series.get("update_mode"), "delaySeconds": series.get("delay_seconds")}
    return {"chart": {"result": [{"meta": meta, "timestamp": [b["t"] for b in bars],
                                  "indicators": {"quote": [{"open": [b["o"] for b in bars],
                                                            "high": [b["h"] for b in bars],
                                                            "low": [b["l"] for b in bars],
                                                            "close": [b["c"] for b in bars],
                                                            "volume": [b["v"] for b in bars]}]}}],
                      "error": None}}


def prev_close_of(daily_bars, day):
    """The last daily close before a Cairo date."""
    prev = None
    for b in daily_bars:
        if to_cairo(utc_of(b["t"])).date() < day:
            prev = b["c"]
    return prev


def chart_text(sym, interval, n_bars, now_utc=None, with_prev_close=True):
    """One ticker's chart as JSON text (Yahoo v8 layout). Raises EGXDataError."""
    series = chart_one(sym, interval, n_bars)
    if with_prev_close and interval in INTRADAY:
        try:
            daily = chart_one(sym, "1d", 10)
            today = to_cairo(utc_of(series["bars"][-1]["t"])).date()
            series["prev_close"] = prev_close_of(daily["bars"], today)
        except EGXDataError:
            series["prev_close"] = None
    return json.dumps(to_chart_json(series, now_utc))


def bars_for_days(interval, days):
    """How many bars cover `days` EGX sessions (4.5 hours each), capped at the limit."""
    step = INTERVALS[interval][1]
    if interval in INTRADAY:
        per = (4 * 3600 + 30 * 60) // step + 1
        return min(MAX_BARS, per * (days + 1))
    return min(MAX_BARS, days + 5)


def delay_note(seconds):
    if seconds is None:
        return "delay unknown"
    if seconds <= 0:
        return "real-time"
    return "DELAYED %d min" % (seconds // 60)


# ---------------------------------------------------------------- HTTP: scanner and headlines

def _http(url, data=None, host=None, timeout=TIMEOUT):
    headers = {"User-Agent": UA, "Accept": "application/json", "Origin": "https://www.tradingview.com"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    host = host or url.split("/")[2]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        e.close()
        raise EGXDataError("%s answered HTTP %d" % (host, e.code))
    except urllib.error.URLError as e:
        raise EGXDataError("could not reach %s: %s" % (host, e.reason))
    except OSError as e:
        raise EGXDataError("network error from %s: %s" % (host, e))


UNIVERSE_COLUMNS = ["name", "description", "type", "close", "change", "volume", "average_volume_10d_calc",
                    "market_cap_basic", "sector", "currency", "high", "low", "open"]


def scan_request(columns=None, limit=1000):
    return {"filter": [{"left": "exchange", "operation": "equal", "right": EXCHANGE},
                       {"left": "type", "operation": "in_range", "right": ["stock", "dr"]}],
            "columns": columns or UNIVERSE_COLUMNS, "sort": {"sortBy": "volume", "sortOrder": "desc"},
            "range": [0, limit]}


def parse_scan(text, columns=None):
    """Scanner JSON -> [{"symbol", <column>: value, ...}] (TradingView's "name" column is the ticker)."""
    columns = columns or UNIVERSE_COLUMNS
    try:
        doc = json.loads(text)
    except ValueError:
        raise EGXDataError("%s returned something that is not JSON" % SCAN_HOST)
    rows = []
    for r in doc.get("data") or []:
        d = dict(zip(columns, r.get("d") or []))
        sym = str(r.get("s", "")).split(":")[-1]
        if not sym:
            continue
        d["symbol"] = sym
        rows.append(d)
    return rows


def universe(columns=None):
    """Every EGX stock TradingView lists, most-traded first. One request."""
    text = _http("https://%s/egypt/scan" % SCAN_HOST, scan_request(columns))
    return parse_scan(text, columns)


def news_url(sym=None):
    base = "https://%s/v2/headlines?client=web&lang=en&streaming=false" % NEWS_HOST
    if sym:
        return base + "&symbol=%s:%s" % (EXCHANGE, norm_symbol(sym))
    return base + "&category=stock&market_country=EG"


def parse_news(text):
    """Headlines JSON -> [{"id","title","provider","published","symbols","url"}]."""
    try:
        doc = json.loads(text)
    except ValueError:
        raise EGXDataError("%s returned something that is not JSON" % NEWS_HOST)
    items = doc.get("items", []) if isinstance(doc, dict) else (doc or [])
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        link = it.get("link") or it.get("storyPath") or ""
        if link.startswith("/"):
            link = "https://www.tradingview.com" + link
        out.append({"id": str(it.get("id", "")), "title": " ".join(str(it["title"]).split()),
                    "provider": it.get("provider") or it.get("source") or "",
                    "published": int(it.get("published") or 0),
                    "symbols": [s.get("symbol") for s in it.get("relatedSymbols") or [] if s.get("symbol")],
                    "url": link})
    return out


def headlines(sym=None):
    """Headlines for one EGX ticker, or for the Egyptian market when sym is None."""
    return parse_news(_http(news_url(sym)))


# ---------------------------------------------------------------- notifications (Mac sound + phone)

NTFY_HOST = "ntfy.sh"


def notify_settings(cfg):
    """From trader/config.json: "notify_mac" (true), "notify_sound" ("Glass"), "notify_ntfy_topic" (none)."""
    cfg = cfg or {}
    return {"mac": cfg.get("notify_mac", True), "sound": str(cfg.get("notify_sound") or "Glass"),
            "topic": str(cfg.get("notify_ntfy_topic") or "").strip()}


def _applescript_text(s):
    return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')


def notify(title, body, cfg=None, urgent=False, _run=None, _open=None):
    """Ring an alert: a macOS notification with a sound, and a push to your phone through ntfy
    (https://ntfy.sh/<your topic>) when "notify_ntfy_topic" is set. One attempt each, never raises.
    -> (sent: list of channel names, errors: list of strings)"""
    st = notify_settings(cfg)
    sent, errors = [], []
    if st["mac"] and sys.platform == "darwin":
        import subprocess
        run = _run or subprocess.run
        script = "display notification %s with title %s sound name %s" % (
            _applescript_text(body[:240]), _applescript_text(title[:80]), _applescript_text(st["sound"]))
        try:
            run(["osascript", "-e", script], check=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            sent.append("mac")
        except Exception as e:
            errors.append("Mac notification failed: %s" % e)
    if st["topic"]:
        if not re.match(r"^[A-Za-z0-9_-]{8,64}$", st["topic"]):
            errors.append("notify_ntfy_topic must be 8 to 64 letters, digits, - or _")
        else:
            req = urllib.request.Request("https://%s/%s" % (NTFY_HOST, st["topic"]), data=body.encode("utf-8"),
                                         headers={"Title": title.encode("ascii", "replace").decode("ascii")[:80],
                                                  "Priority": "urgent" if urgent else "high",
                                                  "Tags": "rotating_light" if urgent else "bell"})
            try:
                with (_open or urllib.request.urlopen)(req, timeout=10) as r:
                    r.read()
                sent.append("phone")
            except Exception as e:
                errors.append("phone push to %s failed: %s" % (NTFY_HOST, getattr(e, "reason", e)))
    return sent, errors


def new_topic():
    """A random, hard-to-guess ntfy topic name (anyone who knows it can read the alerts)."""
    return "egx-" + "".join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(20))


# ---------------------------------------------------------------- files

def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    if path.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


# ---------------------------------------------------------------- commands

def status_lines(now_utc, cal=None, delay=None):
    cal = cal or load_calendar()
    local = to_cairo(now_utc)
    day = local.date()
    out = ["EGX  %s Cairo (UTC%+d)" % (local.strftime("%a %Y-%m-%d %H:%M"), cairo_offset(now_utc))]
    s = session(day, cal)
    if not s:
        out.append("Closed today: %s. Next open %s Cairo." % (trading_day(day, cal)[1], next_open(day, cal)))
    else:
        o = datetime.datetime.combine(day, s[0])
        c = datetime.datetime.combine(day, s[1])
        tag = " (short session)" if is_short_day(day, cal) else ""
        if local < o:
            out.append("Opens at %s, closes %s%s." % (s[0].strftime("%H:%M"), s[1].strftime("%H:%M"), tag))
        elif local < c:
            out.append("OPEN until %s%s: %d minutes left." % (s[1].strftime("%H:%M"), tag,
                                                              (c - local).total_seconds() // 60))
        else:
            out.append("Closed for the day at %s. Next open %s Cairo." % (s[1].strftime("%H:%M"), next_open(day, cal)))
    if not calendar_known(day, cal):
        out.append("The holiday list runs through %s only: run calendar --refresh, and check egx.com.eg." %
                   (cal.get("known_through") or "nothing"))
    if delay is not None:
        out.append("TradingView data: %s. Check the live price in your broker app before you act." % delay_note(delay))
    return out


def login_lines(delay):
    if _AUTH["token"]:
        who = ("signed in as %s" % _AUTH["user"]) if _AUTH["user"] else "signed in"
        if delay:
            return ["TradingView login: %s, but EGX is still %s: the account has no EGX real-time data "
                    "subscription yet (TradingView, Profile, Market data subscriptions, Egyptian Exchange)."
                    % (who, delay_note(delay))]
        return ["TradingView login: %s, EGX real-time." % who]
    if _AUTH["note"]:
        return ["TradingView login: %s." % _AUTH["note"]]
    return ["TradingView login: none saved (%s). Run egx_data.py login on your own computer for real-time "
            "data with your subscription." % cred_path()]


def cmd_status(a):
    delay = None
    try:
        delay = chart_one(a.symbol, "1d", 2)["delay_seconds"]
    except EGXDataError as e:
        print("Data check failed: %s" % e)
    for line in status_lines(utc_now(), delay=delay) + login_lines(delay):
        print(line)
    return 0


LOGIN_HELP = """Real-time EGX data with your own TradingView subscription.

1. On tradingview.com, sign in, and buy the Egyptian Exchange real-time data
   (Profile, Market data subscriptions). Without it the feed stays 15 minutes late.
2. In that browser, open the developer tools (F12), Application (Chrome) or
   Storage (Firefox), Cookies, https://www.tradingview.com, and copy the values of
   the cookies "sessionid" and "sessionid_sign".
3. Paste them below. Input is hidden. They are saved only on this computer, in
   %s (owner-only permissions), never in the repo
   and never sent anywhere but tradingview.com. Never paste them into a chat.

Signing out of TradingView in the browser ends this session too: run login again.
"""


def cmd_login(a):
    import getpass
    path = cred_path()
    print(LOGIN_HELP % path)
    sid = getpass.getpass("sessionid (hidden): ").strip()
    sign = getpass.getpass("sessionid_sign (hidden, Enter if there is none): ").strip()
    if not sid:
        raise EGXDataError("no sessionid given: nothing saved")
    cred = {"sessionid": sid}
    if sign:
        cred["sessionid_sign"] = sign
    tok, user = fetch_auth_token(cred)
    _AUTH.update(token=tok, user=user, checked=True, note=None)
    delay = None
    try:
        delay = chart_one(a.symbol, "1d", 2)["delay_seconds"]
    except EGXDataError as e:
        print("Signed in, but the data check failed: %s" % e)
    save_cred(cred, path)
    print("Saved to %s." % path)
    for line in login_lines(delay):
        print(line)
    return 0


def cmd_logout(a):
    path = cred_path()
    if os.path.exists(path):
        os.remove(path)
        print("Removed %s. The tools use the free delayed feed again." % path)
    else:
        print("No saved login at %s." % path)
    return 0


def cmd_chart(a):
    n = a.bars or (bars_for_days(a.interval, a.days) if a.days else 60)
    r = chart(a.symbols, a.interval, n)
    for s, ser in sorted(r["ok"].items()):
        if a.save:
            path = a.save if len(a.symbols) == 1 else os.path.join(a.save, "%s.json.gz" % s)
            write_text(path, json.dumps(to_chart_json(ser)))
            print("%s: %d %s bars -> %s (%s)" % (s, len(ser["bars"]), a.interval, path, delay_note(ser["delay_seconds"])))
            continue
        print("%s  %d %s bars, %s" % (s, len(ser["bars"]), a.interval, delay_note(ser["delay_seconds"])))
        for b in ser["bars"][-a.show:]:
            print("  %s  O %.2f  H %.2f  L %.2f  C %.2f  V %d" % (
                to_cairo(utc_of(b["t"])).strftime("%Y-%m-%d %H:%M"), b["o"], b["h"], b["l"], b["c"], b["v"]))
    for s, e in sorted(r["errors"].items()):
        print("%s  ERROR: %s" % (s, e))
    return 1 if r["errors"] else 0


QUOTE_FIELDS = ["description", "lp", "lp_time", "ch", "chp", "volume", "update_mode", "current_session",
                "market_cap_basic", "price_earnings_ttm", "earnings_per_share_fq", "earnings_release_date",
                "earnings_release_next_date", "fiscal_period_current", "sector", "industry", "currency_code"]


def cmd_quote(a):
    r = quote(a.symbols)
    for s, v in sorted(r["ok"].items()):
        if a.json:
            print(json.dumps({s: v}, ensure_ascii=False))
            continue
        print(s)
        for k in QUOTE_FIELDS:
            if k in v:
                val = v[k]
                if k.endswith(("_time", "_date")) and isinstance(val, (int, float)):
                    val = to_cairo(utc_of(val)).strftime("%Y-%m-%d %H:%M Cairo")
                print("  %-28s %s" % (k, val))
    for s, e in sorted(r["errors"].items()):
        print("%s  ERROR: %s" % (s, e))
    return 1 if r["errors"] else 0


def cmd_universe(a):
    rows = universe()
    if a.save:
        write_text(a.save, json.dumps(rows, ensure_ascii=False, indent=1))
        print("Wrote %d EGX symbols to %s." % (len(rows), a.save))
        return 0
    for r in rows[:a.top]:
        print("%-6s  %-45s  %8s  vol %s" % (r["symbol"], str(r.get("description") or "")[:45],
                                            "%.2f" % r["close"] if r.get("close") is not None else "-",
                                            r.get("volume")))
    print("%d EGX stocks (most-traded first)." % len(rows))
    return 0


def cmd_calendar(a):
    if a.refresh:
        ser = chart_one(a.symbol, "1d", 2)
        doc = calendar_from_symbol_info(ser["info"], utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        if not doc["holidays"] or not doc["sessions"]:
            raise EGXDataError("TradingView sent no session or holiday list; the calendar was not changed")
        path = a.out or LOCAL_CALENDAR
        write_text(path, json.dumps(doc, indent=1) + "\n")
        print("Wrote %s: %d holidays through %s, %d session rules." % (
            path, len(doc["holidays"]), doc["known_through"], len(doc["sessions"])))
        _CAL.clear()
    cal = load_calendar()
    for line in status_lines(utc_now(), cal):
        print(line)
    year = str(to_cairo(utc_now()).year)
    hol = [h for h in cal.get("holidays") or [] if h.startswith(year)]
    print("EGX holidays in %s (%s): %s" % (year, cal.get("_path"), ", ".join(hol) or "none listed"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="EGX data: prices, calendar, stock list (TradingView, 15-min delayed).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("status", help="is EGX open, and how late is the data?")
    p.add_argument("--symbol", default="COMI")
    p = sub.add_parser("chart", help="OHLCV bars for one or more tickers")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--interval", choices=sorted(INTERVALS), default="5m")
    p.add_argument("--bars", type=int, help="how many bars (max %d)" % MAX_BARS)
    p.add_argument("--days", type=int, help="enough bars for this many sessions")
    p.add_argument("--show", type=int, default=5, help="print the last N bars")
    p.add_argument("--save", help="write chart JSON (.json or .json.gz; a folder for several tickers)")
    p = sub.add_parser("quote", help="last price, earnings and fundamentals")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--json", action="store_true", help="every field TradingView sends")
    p = sub.add_parser("universe", help="every EGX stock")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--save")
    p = sub.add_parser("login", help="save your TradingView session for real-time data (run on your own computer)")
    p.add_argument("--symbol", default="COMI")
    sub.add_parser("logout", help="remove the saved TradingView session")
    p = sub.add_parser("calendar", help="session hours and holidays")
    p.add_argument("--refresh", action="store_true", help="re-read them from TradingView")
    p.add_argument("--out")
    p.add_argument("--symbol", default="COMI")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"status": cmd_status, "chart": cmd_chart, "quote": cmd_quote, "universe": cmd_universe,
                "calendar": cmd_calendar, "login": cmd_login, "logout": cmd_logout}[a.cmd](a)
    except EGXDataError as e:
        print("egx_data.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
