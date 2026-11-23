"""trader-egx tests: egx_data.py, the one module every tool fetches through.
Fixtures in tests/fixtures/trader-egx/ (real TradingView answers, 22-23 Nov 2026):

socket-COMI-NOPE-5m.json  the raw websocket reads for a 3-bar 5-minute COMI chart
                          and an unknown ticker (NOPE) asked on the same socket
scan-egypt.json           scanner.tradingview.com/egypt/scan: 298 EGX listings
news-COMI.json            news-headlines for EGX:COMI, first 25 items
quote-COMI.json.gz        the quote session's fields for COMI (about 1,500)
"""

import datetime
import gzip
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

E = load("trader-egx", "egx_data")
XDIR = os.path.join(FIX, "trader-egx")
DT = datetime.datetime


def fx(name):
    p = os.path.join(XDIR, name)
    if p.endswith(".gz"):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def frames():
    return [f for raw in json.loads(fx("socket-COMI-NOPE-5m.json")) for f in E.split_frames(raw)]


class CairoTime(unittest.TestCase):
    def test_winter_is_utc_plus_2(self):
        self.assertEqual(E.cairo_offset(DT(2026, 11, 23, 8, 0)), 2)
        self.assertEqual(E.to_cairo(DT(2026, 11, 3, 8, 25)), DT(2026, 11, 3, 10, 25))

    def test_summer_time_rule(self):
        # 2026: last Friday of April is 24 April, last Thursday of October is 29 October
        self.assertEqual(E.cairo_offset(DT(2026, 4, 23, 21, 59)), 2)
        self.assertEqual(E.cairo_offset(DT(2026, 4, 23, 22, 0)), 3)     # 00:00 Cairo, 24 April
        self.assertEqual(E.cairo_offset(DT(2026, 10, 29, 20, 59)), 3)
        self.assertEqual(E.cairo_offset(DT(2026, 10, 29, 21, 0)), 2)    # 24:00 Cairo, 29 October
        self.assertEqual(E.cairo_offset(DT(2022, 7, 1)), 2)             # no summer time 2015-2022

    def test_round_trip(self):
        for local in (DT(2026, 1, 5, 10, 0), DT(2026, 7, 7, 14, 30), DT(2027, 3, 1, 9, 59)):
            self.assertEqual(E.to_cairo(E.cairo_to_utc(local)), local)

    def test_matches_the_system_zone_database_when_present(self):
        try:
            from zoneinfo import ZoneInfo   # tests only: the tools stay on Python 3.8
            z = ZoneInfo("Africa/Cairo")
        except Exception:
            self.skipTest("no zone database here")
        t = DT(2023, 1, 1)
        while t < DT(2028, 1, 1):
            want = DT(t.year, t.month, t.day, t.hour, tzinfo=datetime.timezone.utc).astimezone(z).utcoffset()
            self.assertEqual(E.cairo_offset(t), int(want.total_seconds() // 3600), t)
            t += datetime.timedelta(hours=7)


class Calendar(unittest.TestCase):
    def test_tradingview_session_string(self):
        s = E.parse_tv_session("1;1000-1430:12345#20260222/1;1000-1330:12345#20260322/1;1000-1430:12345")
        self.assertEqual(s[0], {"until": "2026-02-22", "open": "10:00", "close": "14:30", "days": [0, 1, 2, 3, 6]})
        self.assertEqual(s[1]["close"], "13:30")
        self.assertIsNone(s[2]["until"])

    def test_corrections(self):
        self.assertEqual(E.parse_tv_corrections("1000-1330:20260219;1000-1430:20240310"),
                         {"2026-02-19": ["10:00", "13:30"], "2024-03-10": ["10:00", "14:30"]})

    def test_shipped_calendar(self):
        cal = E.load_calendar(E.CALENDAR_FILE)
        self.assertGreater(len(cal["holidays"]), 100)
        self.assertTrue(E.calendar_known(datetime.date(2026, 12, 1), cal))
        d = datetime.date
        self.assertEqual(E.session(d(2026, 11, 3), cal), (datetime.time(10, 0), datetime.time(14, 30)))
        self.assertEqual(E.session(d(2026, 2, 19), cal), (datetime.time(10, 0), datetime.time(13, 30)))  # correction
        self.assertEqual(E.session(d(2026, 3, 1), cal)[1], datetime.time(13, 30))                      # Ramadan
        self.assertIsNone(E.session(d(2026, 3, 22), cal))                                              # Eid
        self.assertIsNone(E.session(d(2026, 11, 6), cal))                                              # Friday
        self.assertEqual(E.next_open(d(2026, 11, 5), cal), "2026-11-08 10:00")

    def test_calendar_from_symbol_info(self):
        info = [f for f in frames() if isinstance(f, dict) and f.get("m") == "symbol_resolved"][0]["p"][2]
        cal = E.calendar_from_symbol_info(info)
        self.assertEqual(cal["timezone"], "Africa/Cairo")
        self.assertIn("2026-10-08", cal["holidays"])
        self.assertTrue(cal["sessions"])


class Socket(unittest.TestCase):
    def test_frames_and_heartbeat(self):
        out = E.split_frames('~m~4~m~~h~7~m~16~m~{"m":"x","p":[]}')
        self.assertEqual(out, ["~h~7", {"m": "x", "p": []}])

    def test_parse_series(self):
        fr = [f for f in frames() if isinstance(f, dict)]
        comi = [f for f in fr if (f.get("p") or [""])[0] == "cs_aaaaaaaaaaaa"]
        r = E.parse_series(comi)
        self.assertIsNone(r["error"])
        self.assertEqual(len(r["bars"]), 3)
        self.assertEqual(r["update_mode"], "delayed_streaming_900")
        self.assertEqual(E.delay_seconds(r["update_mode"]), 900)
        last = r["bars"][-1]
        self.assertEqual((last["c"], last["v"]), (125.01, 118978))
        self.assertEqual(E.to_cairo(E.utc_of(last["t"])), DT(2026, 11, 22, 14, 25))

    def test_unknown_ticker_is_an_error_not_a_silent_drop(self):
        fr = [f for f in frames() if isinstance(f, dict)]
        bad = [f for f in fr if (f.get("p") or [""])[0] == "cs_bbbbbbbbbbbb"]
        self.assertIn("resolve error", E.parse_series(bad)["error"])

    def test_delay_notes(self):
        self.assertEqual(E.delay_seconds("streaming"), 0)
        self.assertIsNone(E.delay_seconds(None))
        self.assertEqual(E.delay_note(900), "DELAYED 15 min")
        self.assertEqual(E.delay_note(0), "real-time")

    def test_chart_json_shape(self):
        bars = [{"t": 1792404000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]
        doc = E.to_chart_json({"symbol": "COMI", "interval": "5m", "bars": bars, "delay_seconds": 900,
                               "update_mode": "delayed_streaming_900", "prev_close": 1.2})
        m = doc["chart"]["result"][0]["meta"]
        self.assertEqual((m["symbol"], m["currency"], m["exchangeTimezoneName"], m["delaySeconds"]),
                         ("COMI", "EGP", "Africa/Cairo", 900))
        self.assertEqual(m["regularMarketTime"], 1792404300)   # the bar's close
        self.assertEqual(m["previousClose"], 1.2)


class NoNetwork(unittest.TestCase):
    """A blocked or missing connection says which host, once, and does not loop."""

    def test_missing_websocket_package_is_named(self):
        import builtins
        real = builtins.__import__

        def fake(name, *a, **k):
            if name == "websocket":
                raise ImportError(name)
            return real(name, *a, **k)
        builtins.__import__ = fake
        try:
            with self.assertRaises(E.EGXDataError) as cm:
                E._connect()
            self.assertIn("pip install websocket-client", str(cm.exception))
        finally:
            builtins.__import__ = real

    def test_bad_symbols_are_reported(self):
        r = E.chart(["not a ticker!"], "5m", 3)
        self.assertEqual(r["ok"], {})
        self.assertIn("not a ticker!", r["errors"])

    def test_norm_symbol(self):
        self.assertEqual(E.norm_symbol("egx:comi"), "COMI")
        self.assertEqual(E.norm_symbol("COMI.CA"), "COMI")


class ScannerAndNews(unittest.TestCase):
    def test_scan(self):
        rows = E.parse_scan(fx("scan-egypt.json"))
        self.assertEqual(len(rows), 298)
        comi = [r for r in rows if r["symbol"] == "COMI"][0]
        self.assertEqual(comi["currency"], "EGP")
        self.assertIn("Commercial International Bank", comi["description"])

    def test_news(self):
        items = E.parse_news(fx("news-COMI.json"))
        self.assertEqual(len(items), 25)
        self.assertTrue(all(i["title"] and i["published"] for i in items))
        self.assertTrue(any("EGX:COMI" in i["symbols"] for i in items))
        self.assertIn("symbol=EGX:COMI", E.news_url("comi"))
        self.assertIn("market_country=EG", E.news_url())

    def test_quote_has_fundamentals(self):
        q = json.loads(fx("quote-COMI.json.gz"))["COMI"]
        for k in ("lp", "update_mode", "earnings_per_share_fq_h", "total_revenue_fq_h", "fiscal_period_end_fq_h",
                  "earnings_release_next_date"):
            self.assertIn(k, q)


class Cli(unittest.TestCase):
    def test_calendar_command_offline(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = E.main(["calendar"])
        self.assertEqual(code, 0)
        self.assertIn("EGX holidays in", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class Login(unittest.TestCase):
    """Your TradingView session for real-time data: kept outside the repo, owner-only, never required."""

    PAGE_OUT = 'x<script>var is_authenticated = false;\n var user = {"username":"Guest","following":"0"};</script>'
    PAGE_IN = ('x<script>var is_authenticated = true;\n var user = {"username":"omar","auth_token":"eyJ.abc.def",'
               '"is_pro":true};</script> "username":"OANDA"')

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "cfg", "tradingview.json")
        self.old_env = os.environ.get(E.CRED_ENV)
        os.environ[E.CRED_ENV] = self.path
        E._AUTH.update(token=None, user=None, checked=False, note=None)
        E.LAST["delay"] = None

    def tearDown(self):
        import shutil
        if self.old_env is None:
            os.environ.pop(E.CRED_ENV, None)
        else:
            os.environ[E.CRED_ENV] = self.old_env
        E._AUTH.update(token=None, user=None, checked=False, note=None)
        E.LAST["delay"] = None
        shutil.rmtree(self.tmp)

    def test_page_parsing(self):
        self.assertEqual(E.parse_auth_page(self.PAGE_OUT), (None, "Guest"))
        self.assertEqual(E.parse_auth_page(self.PAGE_IN), ("eyJ.abc.def", "omar"))
        self.assertEqual(E.parse_auth_page("no user here"), (None, None))

    def test_saved_owner_only_outside_the_repo(self):
        E.save_cred({"sessionid": "s1", "sessionid_sign": "v2", "other": "x"})
        self.assertEqual(E.cred_path(), self.path)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        self.assertEqual(E.load_cred(), {"sessionid": "s1", "sessionid_sign": "v2"})
        self.assertFalse(self.path.startswith(ROOT))

    def test_no_login_means_the_anonymous_delayed_feed(self):
        self.assertEqual(E.auth_token(), E.ANON_TOKEN)
        self.assertEqual(E.delay_phrase(), " (15 min late)")

    def test_a_good_login_is_used(self):
        E.save_cred({"sessionid": "s1"})
        real = E.fetch_auth_token
        E.fetch_auth_token = lambda cred, timeout=20: ("eyJ.abc.def", "omar")
        try:
            self.assertEqual(E.auth_token(), "eyJ.abc.def")
            self.assertEqual(E.login_lines(0), ["TradingView login: signed in as omar, EGX real-time."])
            self.assertIn("no EGX real-time data subscription", E.login_lines(900)[0])
        finally:
            E.fetch_auth_token = real

    def test_a_bad_login_falls_back_and_says_why(self):
        E.save_cred({"sessionid": "expired"})
        real = E.fetch_auth_token

        def bad(cred, timeout=20):
            raise E.EGXDataError("TradingView did not accept the saved session (signed out or expired)")
        E.fetch_auth_token = bad
        try:
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(E.auth_token(), E.ANON_TOKEN)
            self.assertIn("Using the delayed feed", err.getvalue())
            self.assertIn("did not accept", E.login_lines(900)[0])
        finally:
            E.fetch_auth_token = real

    def test_signed_in_connects_to_the_pro_host_first(self):
        import types
        seen = []

        def fake_create(url, header=None, timeout=None):
            seen.append(url)
            if "prodata" in url:
                raise OSError("refused")
            return "socket"
        fake = types.ModuleType("websocket")
        fake.create_connection = fake_create
        import sys as _sys
        real_mod = _sys.modules.get("websocket")
        _sys.modules["websocket"] = fake
        E._AUTH.update(token="eyJ.abc.def", checked=True)
        try:
            self.assertEqual(E._connect(), "socket")
            self.assertEqual(seen, ["wss://prodata.tradingview.com/socket.io/websocket",
                                    "wss://data.tradingview.com/socket.io/websocket"])
        finally:
            if real_mod is None:
                _sys.modules.pop("websocket", None)
            else:
                _sys.modules["websocket"] = real_mod

    def test_delay_words_follow_the_feed(self):
        self.assertEqual(E.delay_phrase(900), " (15 min late)")
        self.assertEqual(E.delay_phrase(0), "")
        self.assertIn("real-time", E.late_sentence(0))
        self.assertIn("15 min late", E.late_sentence(900))
        E.LAST["delay"] = 0
        self.assertEqual(E.delay_phrase(), "")


class RealTimeWords(unittest.TestCase):
    def test_broker_ticket_drops_the_delay_line_on_real_time(self):
        ticket = load("trader-broker", "ticket")
        order = {"symbol": "COMI", "side": "buy", "qty": "1", "type": "market", "time_in_force": "day"}
        old = dict(E.LAST)
        try:
            E.LAST["delay"] = 0
            ticket.E.LAST["delay"] = 0
            self.assertEqual(ticket.entry_steps(order, {})[-1], "Check the live price in Telda first.")
            ticket.E.LAST["delay"] = 900
            self.assertIn("15 min late", ticket.entry_steps(order, {})[-1])
        finally:
            ticket.E.LAST.update(old)
