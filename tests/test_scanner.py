"""trader-scanner tests on EGX data.

wall5m-2026-11-03.json.gz  5-minute bars on 3 Nov 2026 for the 60 most traded EGX stocks
                           (TradingView, one bundle {SYM: chart json}).
daily-2026-11-02.json.gz   up to 90 daily bars through 2 Nov 2026 for the same 60.
../trader-egx/scan-egypt.json  TradingView's EGX screener (298 rows).
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")

SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-11-03.json.gz")
DAILY = os.path.join(SDIR, "daily-2026-11-02.json.gz")
SCREENER = os.path.join(FIX, "trader-egx", "scan-egypt.json")
DAY = datetime.date(2026, 11, 3)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def orb_at(hhmm, **kw):
    data, _ = scan.load_offline(WALL)
    day, cutoff = scan.orb_window(scan.parse_now("2026-11-03 " + hhmm), None, 15, datetime.time(14, 0))
    return scan.scan_orb(data, sorted(data), day, cutoff, 15, **kw)


class Universe(unittest.TestCase):
    def test_build(self):
        import importlib
        E = scan.E
        with open(SCREENER) as fh:
            rows = E.parse_scan(fh.read())
        kept = scan.build_universe(rows)
        self.assertEqual(len(kept), 287)
        self.assertEqual(kept[0][0], "COMI")
        self.assertTrue(all(len(s) <= 8 for s, _ in kept))

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        try:
            out_path = os.path.join(tmp, "universe.txt")
            code, out, err = run(["universe", "--offline", SCREENER, "--out", out_path])
            self.assertEqual(code, 0, err)
            self.assertIn("287 stocks kept", out)
            with open(out_path) as fh:
                self.assertTrue(fh.readline().startswith("COMI|Commercial International Bank"))
        finally:
            shutil.rmtree(tmp)


class Orb(unittest.TestCase):
    def test_comi_at_10_30(self):
        hits, counts = orb_at("10:30")
        self.assertEqual(counts["scanned"], 60)
        self.assertEqual(len(hits), 35)
        comi = [h for h in hits if h["symbol"] == "COMI"][0]
        self.assertEqual((comi["at"], comi["price"], comi["or_high"], comi["or_low"], comi["bar_index"]),
                         ("10:25", 122.41, 122.0, 121.51, 4))

    def test_the_bar_in_progress_does_not_count(self):
        hits, _ = orb_at("10:24")
        self.assertNotIn("COMI", [h["symbol"] for h in hits])

    def test_window_messages(self):
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-03 10:15"), None, 15, None)
        self.assertIn("runs 10:00 to 10:15", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-06 11:00"), None, 15, None)
        self.assertIn("No EGX session", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-03 14:05"), None, 15, datetime.time(14, 0))
        self.assertIn("after 14:00", str(cm.exception))

    def test_thin_charts_are_unusable(self):
        def series(n_bars):
            t0 = scan.E.ts_of(scan.E.cairo_to_utc(datetime.datetime(2026, 11, 3, 10, 0)))
            ts = [t0 + 300 * k for k in range(0, 2 * n_bars, 2)]       # every other bar is missing
            return {"t": ts, "c": [10.0] * len(ts), "h": [10.0] * len(ts), "l": [10.0] * len(ts),
                    "v": [1] * len(ts), "meta": {}}
        cutoff = datetime.datetime(2026, 11, 3, 11, 0)
        bars = scan.session_bars(series(6), DAY, cutoff)
        self.assertFalse(scan.usable(bars, DAY, cutoff, 3))

    def test_cli_replay(self):
        tmp = tempfile.mkdtemp()
        try:
            out_path = os.path.join(tmp, "watchlist.json")
            code, out, err = run(["orb", "--offline", WALL, "--now", "2026-11-03 10:30", "--out", out_path])
            self.assertEqual(code, 0, err)
            self.assertIn("TradingView (15 min late)", out)
            self.assertIn("COMI", out)
            self.assertNotIn("$", out)
            with open(out_path) as fh:
                doc = json.load(fh)
            self.assertEqual((doc["mode"], doc["market"], doc["currency"]), ("orb", "EGX", "EGP"))
        finally:
            shutil.rmtree(tmp)


class Daily(unittest.TestCase):
    def test_pre_market_scan(self):
        data, _ = scan.load_offline(DAILY)
        hits, counts, last = scan.scan_daily(data, sorted(data), scan.parse_now("2026-11-03 09:30"), 1.0, 100000)
        self.assertEqual(last, datetime.date(2026, 11, 2))
        self.assertEqual(counts["scanned"], 60)
        self.assertEqual(len(hits), 9)
        tags = {h["symbol"]: h["setups"] for h in hits}
        self.assertEqual(tags["BQDC"], ["breakout_20d"])
        self.assertIn("oversold", tags["FWRY"])

    def test_today_is_dropped_until_the_close(self):
        data, _ = scan.load_offline(DAILY)
        s = data["COMI"]
        before = scan.daily_bars(s, scan.parse_now("2026-11-02 14:00"))
        after = scan.daily_bars(s, scan.parse_now("2026-11-02 14:31"))
        self.assertEqual(len(after), len(before) + 1)

    def test_rsi(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 30)]), 100.0)

    def test_failed_tickers_are_listed(self):
        lines = scan._failed_lines({"NOPE": "TradingView: invalid symbol"})
        self.assertIn("1 ticker failed (listed, not dropped):", lines[0])
        self.assertIn("NOPE", lines[1])


if __name__ == "__main__":
    unittest.main()
