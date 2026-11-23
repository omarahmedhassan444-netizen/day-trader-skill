#!/usr/bin/env python3
"""Price alerts and the EGX clock for trader-alerts.

    python3 alerts.py clock                          # is EGX open? next open / close (Cairo)
    python3 alerts.py quote COMI --write             # quotes -> trader/prices.json
    python3 alerts.py quote --holdings --write       # every ticker in your portfolio and plan
    python3 alerts.py add --from-plan                # stop, target, GET READY warnings, flat-by
    python3 alerts.py add --symbol COMI --stop 121.75
    python3 alerts.py add --symbol COMI --target 123.07
    python3 alerts.py add --at 14:15 --label "5-minute warning: sell by 14:20" --before-close
    python3 alerts.py add --symbol ETEL --pct 3
    python3 alerts.py add --opening-bell
    python3 alerts.py check                          # rings what hit, once; exit 1 if nothing rang
    python3 alerts.py check --now "2026-11-03 14:15" --offline tests/fixtures/trader-alerts
    python3 alerts.py list
    python3 alerts.py reset comi-plan-stop

Rule types:
  above / target  the price is at or above "price"
  below / stop    the price is at or below "price"
  warn_above      GET READY: the price is at or above "price" (on the way to a target)
  warn_below      GET READY: the price is at or below "price" (on the way to a stop)
  pct_move        the price moved "pct" percent or more from the previous close
                  (or from "ref" if you give one); "direction": "up" or "down"
  opening_bell    EGX opened today (rings once per trading day)
  time            the Cairo clock passed "at" (HH:MM) on a trading day
                  (rings once per trading day); "before_close": true moves it
                  earlier on a short (Ramadan) session by as much as the close
                  moved, so a 14:15 warning rings at 13:15 when EGX closes 13:30

The 5-minute rule. add --from-plan writes, for the plan's day:
  - a GET READY warning halfway from the entry to the stop, and halfway to the target
  - the stop and the target themselves
  - a time warning 5 minutes before flat_by (14:15 for 14:20), then flat_by itself
so you hear about every action at least 5 minutes before you have to take it
in your broker app, whenever the clock or the price allows it. Prices here are
15 MINUTES LATE: a price alert can ring after the live price has moved on.

A rule with "date" is only watched on that Cairo date (rules from a plan are
for the plan's day only). An above / below alert rings ONCE and then stays
quiet until you reset it, unless the rule has "rearm": true (it re-arms when
the price crosses back). A pct_move alert rings at most once per day.

Quotes: TradingView through trader-egx/egx_data.py, one connection for all
tickers (free, DELAYED 15 MINUTES without a login; for alerts only, never for
timing an order: the price in your broker app is the one that fills).
--offline DIR reads DIR/tradingview-<TICKER>.json (a quote's fields) instead.

EGX calendar: Sunday to Thursday, 10:00 to 14:30 Cairo (13:30 in Ramadan),
holidays from TradingView's EGX session data (trader-egx/calendar.json).
Not financial advice.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-egx"))
import egx_data as E  # noqa: E402

HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
OPEN = E.DEFAULT_OPEN
CLOSE = E.DEFAULT_CLOSE
LEAD_MIN = 5         # minutes of warning before a timed action
WARN_SHARE = 0.5     # GET READY halfway from the entry to the stop or the target


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- time and the EGX clock

to_local = E.to_cairo
local_to_utc = E.cairo_to_utc


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as Cairo time."""
    if not text:
        return E.utc_now()
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise AlertError("--now wants a time like \"2026-11-03 14:15\" (Cairo) or ...Z (UTC)")
    if dt.tzinfo is None:
        return local_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    return E.trading_day(d)


def session(d):
    return E.session(d)


def market_status(now_utc):
    local = to_local(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    st = {"now_cairo": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
          "weekday": local.strftime("%A"), "calendar_known": E.calendar_known(d),
          "open": False, "reason": reason, "short_session": None}
    s = session(d)
    if s:
        o, c = s
        st["opens"], st["closes"] = o.strftime("%H:%M"), c.strftime("%H:%M")
        if c < CLOSE:
            st["short_session"] = "short session, EGX closes %s" % c.strftime("%H:%M")
        if o <= local.time() < c:
            st["open"] = True
            st["reason"] = "regular session"
        elif local.time() < o:
            st["reason"] = "before the open"
        else:
            st["reason"] = "after the close"
    if s and local.time() < s[0]:
        st["next_open"] = "%s %s" % (d.isoformat(), s[0].strftime("%H:%M"))
    else:
        st["next_open"] = E.next_open(d)
    return st


# ---------------------------------------------------------------- quotes

def parse_tv_quote(fields, sym):
    """TradingView quote fields -> a quote. lp is the last price, lp_time its time."""
    if not isinstance(fields, dict):
        raise AlertError("TradingView sent something unexpected for %s" % sym)
    price = fields.get("lp")
    if price is None:
        raise AlertError("TradingView has no price for %s" % sym)
    t = fields.get("lp_time")
    when = E.utc_of(t).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(t, (int, float)) else ""
    return {"symbol": sym.upper(), "price": float(price), "open": fields.get("open_price"),
            "high": fields.get("high_price"), "low": fields.get("low_price"),
            "prev_close": fields.get("prev_close_price"), "time": when,
            "currency": fields.get("currency_code") or "EGP", "source": "tradingview",
            "delay_seconds": E.delay_seconds(fields.get("update_mode"))}


def _offline_fields(folder, sym):
    base = os.path.join(folder, "tradingview-%s.json" % sym)
    for p in (base, base + ".gz"):
        if os.path.exists(p):
            opener = gzip.open if p.endswith(".gz") else open
            with opener(p, "rt", encoding="utf-8") as fh:
                return json.load(fh)
    raise AlertError("no offline quote file %s" % base)


def get_quotes(symbols, offline=None):
    """{SYM: quote} and [errors]. Live: one TradingView connection for every ticker."""
    quotes, errors = {}, []
    syms = [s.upper() for s in symbols]
    if offline:
        for sym in syms:
            try:
                quotes[sym] = parse_tv_quote(_offline_fields(offline, sym), sym)
            except AlertError as e:
                errors.append(str(e))
        return quotes, errors
    try:
        r = E.quote(syms)
    except E.EGXDataError as e:
        return {}, [str(e)]
    for sym, fields in r["ok"].items():
        try:
            quotes[sym] = parse_tv_quote(fields, sym)
        except AlertError as e:
            errors.append(str(e))
    errors.extend("%s: %s" % (s, e) for s, e in sorted(r["errors"].items()))
    return quotes, errors


def write_prices(path, quotes, now_utc, source="tradingview"):
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for s, q in quotes.items():
        prices[s] = q["price"]
        detail[s] = {"prev_close": q.get("prev_close"), "time": q.get("time"), "source": q.get("source"),
                     "delay_seconds": q.get("delay_seconds"), "fetched": stamp}
    doc = {"as_of": stamp, "source": source, "currency": "EGP",
           "note": ("Delayed quotes (15 minutes) for alerts and sizing checks. The price in your broker app is "
                    "the one that fills. detail.time is the last trade's time, detail.fetched when it was read."),
           "prices": prices, "detail": detail}
    _write(path, doc)
    return doc


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (path, e))


# ---------------------------------------------------------------- rules

LEVEL_TYPES = {"above", "below", "stop", "target", "warn_above", "warn_below"}
PRICE_TYPES = LEVEL_TYPES | {"pct_move", "opening_bell", "time"}
NO_QUOTE = {"opening_bell", "time"}


def _hhmm(text):
    m = HHMM.match(str(text or "").strip())
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _px(x):
    """EGX prices: 0.01 EGP steps, 0.001 under 1 EGP."""
    return ("%.3f" if 0 < abs(float(x)) < 1 else "%.2f") % float(x)


def validate(rule):
    t = rule.get("type")
    if t not in PRICE_TYPES:
        raise AlertError("rule %s: unknown type %r" % (rule.get("id"), t))
    if t not in NO_QUOTE and not rule.get("symbol"):
        raise AlertError("rule %s needs a symbol" % rule.get("id"))
    if t in LEVEL_TYPES and not isinstance(rule.get("price"), (int, float)):
        raise AlertError("rule %s needs a numeric price" % rule.get("id"))
    if t == "pct_move" and not isinstance(rule.get("pct"), (int, float)):
        raise AlertError("rule %s needs a numeric pct" % rule.get("id"))
    if t == "time" and _hhmm(rule.get("at")) is None:
        raise AlertError("rule %s needs \"at\" as a Cairo time like \"14:15\"" % rule.get("id"))
    if rule.get("date") and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(rule["date"])):
        raise AlertError("rule %s: date must be YYYY-MM-DD" % rule.get("id"))


def active(rule, today):
    """A rule with a "date" is watched on that Cairo date only."""
    return not rule.get("date") or str(rule["date"]) == today


def _rang_today(stamp, today):
    """Did a rule that rang at this UTC stamp ring on this Cairo date?"""
    if not stamp:
        return False
    try:
        utc = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return stamp[:10] == today
    return to_local(utc).date().isoformat() == today


def ring_time(rule, status):
    """The time a time rule rings today; "before_close" moves it up with a short session's close."""
    at = _hhmm(rule.get("at"))
    if rule.get("before_close") and status.get("closes"):
        c = _hhmm(status["closes"])
        if c and c < CLOSE:
            day = datetime.date(2000, 1, 1)
            shift = datetime.datetime.combine(day, CLOSE) - datetime.datetime.combine(day, c)
            at = (datetime.datetime.combine(day, at) - shift).time()
    return at


LEVEL_WORDS = {"stop": "stop", "target": "target", "warn_below": "GET READY level",
               "warn_above": "GET READY level"}


def evaluate(rules, quotes, status, state, now_utc):
    """Pure function. Returns (rings, new_state). Each ring: {id, symbol, message}."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    local = to_local(now_utc)
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        if not active(r, today):
            continue
        typ = {"stop": "below", "target": "above", "warn_below": "below", "warn_above": "above"}.get(
            r["type"], r["type"])
        if typ == "opening_bell":
            if status["open"] and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": "", "message": "Opening bell: EGX is open (%s Cairo)%s."
                              % (status["now_cairo"], (", " + status["short_session"])
                                 if status.get("short_session") else "")})
                rung[rid] = stamp
            continue
        if typ == "time":
            at = ring_time(r, status)
            if status.get("opens") and local.time() >= at and not _rang_today(rung.get(rid), today):
                label = r.get("label") or "time alert"
                msg = "%s%s: it is %s Cairo (set for %s)" % (label[:1].upper(), label[1:],
                                                             local.strftime("%H:%M"), at.strftime("%H:%M"))
                msg += (", EGX closes at %s." % status["closes"]) if status["open"] else ", EGX has closed."
                sym = str(r.get("symbol") or "").upper()
                if sym and r.get("action") == "flatten":
                    msg += (" If %s is still open, sell it now in your broker app (ticket.py flatten --symbol %s "
                            "prints the ticket)." % (sym, sym))
                elif sym and r.get("action") == "warn":
                    msg += " Open your broker app now and get the %s sell ready." % sym
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
            continue
        sym = r["symbol"].upper()
        q = quotes.get(sym)
        if not q:
            continue
        p = q["price"]
        late = " (15 min late)" if q.get("delay_seconds") else ""
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = LEVEL_WORDS.get(r["type"], "alert level")
                head = "GET READY: " if r["type"].startswith("warn_") else ""
                rings.append({"id": rid, "symbol": sym, "message": "%s%s %s EGP %s%s: %s your %s %s%s."
                              % (head, sym, "is" if status["open"] else "last", _px(p), late, word, label,
                                 _px(r["price"]), (" (" + r["note"] + ")") if r.get("note") else "")})
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close") or q.get("open")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": sym, "message": "%s moved %+.2f%% to EGP %s%s (from %s)."
                              % (sym, move, _px(p), late, _px(ref))})
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.3f" % x if abs(x) < 1 else "%.2f" % x)


def _minus(hhmm, minutes):
    t = _hhmm(hhmm)
    dt = datetime.datetime.combine(datetime.date(2000, 1, 1), t) - datetime.timedelta(minutes=minutes)
    return dt.strftime("%H:%M")


def rules_from_plan(plan, cfg_flat_by=None):
    """trader/plan.json -> GET READY warnings, the stop, the target, a 5-minute warning before
    flat_by and flat_by itself, all for the plan's day."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise AlertError("the plan has no symbol (run trader-exit first)")
    sym = str(plan["symbol"]).upper()
    lo = sym.lower()
    out = []
    for key in ("stop", "target"):
        v = plan.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            raise AlertError("the plan has no %s (run trader-exit)" % key)
    entry, stop, target = plan.get("entry"), float(plan["stop"]), float(plan["target"])
    sc = 1000 if stop < 1 else 100
    if isinstance(entry, (int, float)) and stop < entry < target:
        warn_lo = round(entry - WARN_SHARE * (entry - stop), 3 if sc == 1000 else 2)
        warn_hi = round(entry + WARN_SHARE * (target - entry), 3 if sc == 1000 else 2)
        out.append({"id": "%s-plan-warn-stop" % lo, "symbol": sym, "type": "warn_below", "price": warn_lo,
                    "note": "halfway to the stop %s: open your broker app" % _px(stop)})
        out.append({"id": "%s-plan-warn-target" % lo, "symbol": sym, "type": "warn_above", "price": warn_hi,
                    "note": "halfway to the target %s: open your broker app" % _px(target)})
    for key, word in (("stop", "plan stop"), ("target", "plan target")):
        label = plan.get("%s_label" % key)
        out.append({"id": "%s-plan-%s" % (lo, key), "symbol": sym, "type": key, "price": float(plan[key]),
                    "note": word + (": " + label if label else "")})
    flat = plan.get("flat_by") or cfg_flat_by or "14:20"
    if _hhmm(flat) is None:
        raise AlertError("the plan's flat_by %r is not a time like 14:20" % flat)
    out.append({"id": "%s-flat-by-warning" % lo, "symbol": sym, "type": "time", "at": _minus(flat, LEAD_MIN),
                "label": "%d-minute warning: sell by %s" % (LEAD_MIN, flat), "before_close": True,
                "action": "warn"})
    out.append({"id": "%s-flat-by" % lo, "symbol": sym, "type": "time", "at": flat,
                "label": "flat by close", "before_close": True, "action": "flatten"})
    if plan.get("date"):
        for r in out:
            r["date"] = str(plan["date"])
    return out


# ---------------------------------------------------------------- commands

def holdings(a):
    syms = []
    pf = _read(a.portfolio, {})
    for p in pf.get("positions", []) if isinstance(pf, dict) else []:
        syms.append(str(p.get("ticker") or p.get("symbol") or "").upper())
    plan = _read(a.plan, {})
    if isinstance(plan, dict) and plan.get("symbol"):
        syms.append(str(plan["symbol"]).upper())
    return [s for s in syms if s]


def _dedupe(items):
    out = []
    for s in items:
        if s and s not in out:
            out.append(s)
    return out


def cmd_clock(a):
    st = market_status(parse_now(a.now))
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        print("Cairo %s (%s): EGX %s, %s." % (st["now_cairo"], st["weekday"],
                                              "OPEN" if st["open"] else "closed", st["reason"]))
        if st.get("opens"):
            print("Today's session: %s to %s%s." % (st["opens"], st["closes"],
                                                    (" (%s)" % st["short_session"]) if st.get("short_session") else ""))
        if not st["open"] and st.get("next_open"):
            print("Next open: %s Cairo." % st["next_open"])
        if not st["calendar_known"]:
            print("The EGX holiday list does not cover this date: run egx_data.py calendar --refresh, "
                  "and check egx.com.eg.")
    return 0 if st["open"] else 1


def cmd_quote(a):
    syms = _dedupe([s.upper() for s in a.symbols] + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("which tickers? alerts.py quote COMI ETEL, or --holdings")
    now = parse_now(a.now)
    quotes, errors = get_quotes(syms, a.offline)
    if a.write and quotes:
        write_prices(a.write, quotes, now)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2))
    else:
        for s in syms:
            q = quotes.get(s)
            if q:
                chg = ""
                if q.get("prev_close"):
                    chg = " (%+.2f%% vs prev close %s)" % (100.0 * (q["price"] / q["prev_close"] - 1),
                                                           _px(q["prev_close"]))
                t = q.get("time", "")
                if t:
                    t = to_local(datetime.datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")).strftime("%H:%M Cairo")
                print("%-6s EGP %s%s  %s  [%s, %s]" % (s, _px(q["price"]), chg, t, q["source"],
                                                       E.delay_note(q.get("delay_seconds"))))
        for e in errors:
            print("error: " + e)
        if a.write and quotes:
            print("Wrote %d price(s) to %s." % (len(quotes), a.write))
        print("Quotes are free and 15 minutes late: for alerts only. The price in your broker app is the one "
              "that fills.")
    return 0 if quotes else 1


def cmd_check(a):
    doc = _read(a.rules, None)
    if not doc or not doc.get("rules"):
        raise AlertError("no rules in %s: add some with alerts.py add --from-plan" % a.rules)
    rules = doc["rules"]
    now = parse_now(a.now)
    status = market_status(now)
    watched = [r for r in rules if active(r, status["date"])]
    syms = _dedupe([str(r.get("symbol", "")).upper() for r in watched if r.get("type") not in NO_QUOTE])
    quotes, errors = (get_quotes(syms, a.offline) if syms else ({}, []))
    state = _read(a.state, {})
    rings, state = evaluate(rules, quotes, status, state, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes:
            write_prices(a.prices_out, quotes, now)
    if a.json:
        print(json.dumps({"market": status, "rings": rings, "errors": errors,
                          "quotes": {s: q["price"] for s, q in quotes.items()}}, indent=2))
    else:
        print("EGX %s (%s Cairo, %s)." % ("OPEN" if status["open"] else "closed", status["now_cairo"],
                                          status["reason"]))
        for r in rings:
            print("ALERT " + r["message"])
        if not rings:
            print("Nothing hit. %d rule(s) watched today%s." % (
                len(watched), (", " + ", ".join("%s EGP %s" % (s, _px(quotes[s]["price"])) for s in syms
                                                if s in quotes)) if quotes else ""))
        for e in errors:
            print("error: " + e)
        if rings:
            print("An alert is information, not an order. Check the live price in your broker app, act there "
                  "yourself, then record any fill: ticket.py fill.")
    return 0 if rings else 1


def cmd_add(a):
    doc = _read(a.rules, {"rules": []})
    doc.setdefault("rules", [])
    if a.from_plan:
        plan = _read(a.plan, None)
        if plan is None:
            raise AlertError("no plan at %s (run trader-exit first)" % a.plan)
        cfg = _read(a.config, {}) if a.config else {}
        new = rules_from_plan(plan, (cfg or {}).get("flat_by"))
        ids = {r["id"] for r in new}
        doc["rules"] = [r for r in doc["rules"] if r.get("id") not in ids] + new
        state = _read(a.state, {})
        if state.get("rung"):
            for i in ids:
                state["rung"].pop(i, None)
            _write(a.state, state)
        _write(a.rules, doc)
        for r in new:
            print("Added %s: %s" % (r["id"], json.dumps(r)))
        print("Run alerts.py check every minute or two while the trade is open (keep a session watching, or "
              "/loop). Quotes are 15 minutes late: set the stop in your broker app too if it allows one.")
        return 0
    if a.opening_bell:
        rule = {"id": a.id or "opening-bell", "type": "opening_bell"}
    elif a.at:
        if _hhmm(a.at) is None:
            raise AlertError("--at wants a Cairo time like 14:15")
        rule = {"id": a.id or ("time-%s" % a.at.replace(":", "")), "type": "time", "at": a.at,
                "label": a.label or "time alert"}
        if a.symbol:
            rule["symbol"] = a.symbol.upper()
        if a.before_close:
            rule["before_close"] = True
    else:
        if not a.symbol:
            raise AlertError("--symbol is required (or --from-plan, --at, --opening-bell)")
        sym = a.symbol.upper()
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.target is not None:
            rule = {"type": "target", "price": a.target}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        else:
            raise AlertError("give one of --above, --below, --stop, --target or --pct")
        val = rule.get("price", rule.get("pct"))
        rule = dict({"id": a.id or ("%s-%s-%s" % (sym.lower(), rule["type"], _n(val)))}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    if a.date:
        rule["date"] = a.date
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"]):
        raise AlertError("a rule with id %s already exists" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Added %s: %s" % (rule["id"], json.dumps(rule)))
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []})
    state = _read(a.state, {})
    if not doc.get("rules"):
        print("No rules yet.")
        return 1
    for r in doc["rules"]:
        rid = r.get("id")
        rung = state.get("rung", {}).get(rid)
        print("%-26s %s%s" % (rid, json.dumps({k: v for k, v in r.items() if k != "id"}),
                              ("  (rang %s)" % rung) if rung else ""))
    return 0


def cmd_reset(a):
    state = _read(a.state, {})
    if a.id not in state.get("rung", {}):
        print("%s has not rung (nothing to reset)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price alerts and the EGX clock (trader-alerts).")
    sub = ap.add_subparsers(dest="cmd")

    def net(p):
        p.add_argument("--offline", metavar="DIR", help="read tradingview-<TICKER>.json files, no network")
        p.add_argument("--now", help="pretend it is this time (no offset = Cairo time)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("clock", help="is EGX open?")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote", help="latest delayed quotes")
    net(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="add tickers from your portfolio and plan")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH",
                   help="save to prices.json (default trader/prices.json)")
    p = sub.add_parser("check", help="check every rule, ring what hit")
    net(p)
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--prices-out", default="trader/prices.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    g.add_argument("--stop", type=float)
    g.add_argument("--target", type=float)
    g.add_argument("--pct", type=float)
    g.add_argument("--opening-bell", action="store_true")
    g.add_argument("--at", metavar="HH:MM", help="a time alert, Cairo time")
    g.add_argument("--from-plan", action="store_true",
                   help="GET READY warnings, stop, target, 5-minute warning and flat-by from the plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json", help="flat_by when the plan has none")
    p.add_argument("--label", help="what a time alert says, e.g. \"flat by close\"")
    p.add_argument("--before-close", action="store_true",
                   help="a time alert that moves up with a short (Ramadan) session's close")
    p.add_argument("--date", help="watch this rule on this Cairo date only (YYYY-MM-DD)")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="list the rules")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p = sub.add_parser("reset", help="re-arm a rule that rang")
    p.add_argument("id")
    p.add_argument("--state", default="trader/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "check": cmd_check, "add": cmd_add,
                "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except AlertError as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
