"""trader-entry tests. Fixture in tests/fixtures/trader-entry/:

COMI-2026-11-03-5m.json  COMI's five-minute bars for Tuesday 3 November 2026 on EGX
                         (TradingView, through egx_data.py, chart JSON layout),
                         52 bars from 10:00 to 14:25 Cairo, delay 900 seconds,
                         previous close 121.40 from the daily bars.

The day: the 15-minute opening range (10:00, 10:05, 10:10 bars) is 121.51 to 122.00.
The 10:10 bar closes 121.92 (0.07 % under the high: GET READY), the 10:15 bar 121.99,
and the 10:20 bar closes 122.41 above the high: TRIGGERED at 10:25.
"""

import copy
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
COMI = os.path.join(EDIR, "COMI-2026-11-03-5m.json")
DT = datetime.datetime


def cairo(text):
    return entry.parse_now(text)


def doc(path=COMI):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="COMI"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), cairo(text), **kw)


def no_breakout(d=None):
    """Every bar after the range stays under 121.50, far from the 122.00 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        for k in ("open", "high", "low", "close"):
            q[k][i] = min(q[k][i], 121.50)
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class Time(unittest.TestCase):
    def test_cairo_winter_and_summer(self):
        self.assertEqual(entry.parse_now("2026-11-03 10:25"), DT(2026, 11, 3, 8, 25))   # UTC+2
        self.assertEqual(entry.parse_now("2026-07-07 10:00"), DT(2026, 7, 7, 7, 0))     # summer time, UTC+3
        self.assertEqual(entry.parse_now("2026-11-03T08:25:00Z"), DT(2026, 11, 3, 8, 25))

    def test_bad_now_names_cairo(self):
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_now("tomorrow")
        self.assertIn("Cairo", str(cm.exception))

    def test_egx_trades_sunday_to_thursday(self):
        self.assertTrue(entry.trading_day(datetime.date(2026, 11, 1))[0])     # Sunday
        self.assertTrue(entry.trading_day(datetime.date(2026, 11, 5))[0])     # Thursday
        self.assertFalse(entry.trading_day(datetime.date(2026, 11, 6))[0])    # Friday
        self.assertFalse(entry.trading_day(datetime.date(2026, 11, 7))[0])    # Saturday
        self.assertFalse(entry.trading_day(datetime.date(2026, 10, 8))[0])    # EGX holiday

    def test_ramadan_short_session_moves_the_cutoff(self):
        day = datetime.date(2026, 3, 1)
        self.assertEqual(entry.session(day), (datetime.time(10, 0), datetime.time(13, 30)))
        cut = entry.early_cutoff(day, datetime.time(14, 0), DT.combine(day, datetime.time(13, 30)))
        self.assertEqual(cut.time(), datetime.time(13, 0))


class Evaluate(unittest.TestCase):
    def test_pre_open(self):
        r = at("2026-11-03 09:50")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("EGX opens at 10:00", r["reason"])

    def test_range_forming(self):
        r = at("2026-11-03 10:12")
        self.assertEqual(r["phase"], "forming")
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertEqual(r["forming"]["bar_start"], "10:10")

    def test_get_ready_once_the_range_is_set(self):
        r = at("2026-11-03 10:16")
        self.assertEqual(r["status"], "get-ready")
        self.assertEqual(r["opening_range"], {"high": 122.0, "low": 121.51, "minutes": 15, "bars": 3, "of": 3,
                                              "complete": True, "avg_volume": r["opening_range"]["avg_volume"]})
        self.assertEqual(r["get_ready"]["level"], 122.0)
        self.assertEqual(r["get_ready"]["last"], 121.92)
        self.assertEqual(r["get_ready"]["gap_pct"], 0.07)

    def test_near_pct_zero_turns_get_ready_off(self):
        self.assertEqual(at("2026-11-03 10:16", near_pct=0)["status"], "waiting")

    def test_far_from_the_level_is_just_waiting(self):
        r = at("2026-11-03 11:00", d=no_breakout(), near_pct=0.1)
        self.assertEqual(r["status"], "waiting")

    def test_trigger_at_the_close_of_the_10_20_bar(self):
        r = at("2026-11-03 10:26")
        self.assertEqual(r["status"], "triggered")
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("10:25", "10:20", 4, 122.41))
        self.assertEqual(t["pct_over"], 0.34)
        self.assertFalse(r["late"])

    def test_bar_not_closed_yet_does_not_trigger(self):
        r = at("2026-11-03 10:24")
        self.assertNotEqual(r["status"], "triggered")
        self.assertEqual(r["forming"]["bar_start"], "10:20")

    def test_an_old_trigger_is_late(self):
        r = at("2026-11-03 11:00")
        self.assertTrue(r["late"])
        self.assertEqual(r["minutes_since"], 35)
        text = "\n".join(entry.describe(r))
        self.assertIn("LATE: this trigger is 35 minutes old", text)

    def test_expired_without_a_breakout(self):
        r = at("2026-11-03 14:05", d=no_breakout())
        self.assertEqual(r["status"], "expired")
        self.assertIn("14:00", r["reason"])

    def test_level_setup(self):
        r = at("2026-11-03 12:00", setup="level", level=123.0)
        self.assertEqual(r["status"], "triggered")
        self.assertGreater(r["trigger"]["price"], 123.0)

    def test_closed_on_friday(self):
        d = doc()
        r = entry.evaluate(chart(d), cairo("2026-11-06 11:00"))
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("EGX is closed", r["reason"])
        self.assertTrue(r["next_open"].startswith("2026-11-08 10:00"))

    def test_volume_filter_blocks_a_thin_trigger(self):
        r = at("2026-11-03 10:26", min_volume_x=50)
        self.assertNotEqual(r["status"], "triggered")


class Words(unittest.TestCase):
    def test_every_result_names_the_delay_and_egp(self):
        lines = entry.describe(at("2026-11-03 10:26"))
        text = "\n".join(lines)
        self.assertIn("DELAYED 15 min", lines[0])
        self.assertIn("Cairo", lines[0])
        self.assertIn("EGP 122.41", text)
        self.assertIn("Prices are 15 minutes late", text)
        self.assertIn("Nothing was placed", text)
        self.assertNotIn("$", text)

    def test_get_ready_line_has_a_draft_ticket(self):
        text = "\n".join(entry.describe(at("2026-11-03 10:16")))
        self.assertIn("GET READY: within 0.07% of EGP 122.00", text)
        self.assertIn("Open your broker app", text)


class Watch(unittest.TestCase):
    def test_replay_warns_before_the_trigger(self):
        tmp = tempfile.mkdtemp()
        try:
            out_path = os.path.join(tmp, "entry.json")
            code, out, err = run(["watch", "COMI", "--offline", COMI, "--now", "2026-11-03 09:55",
                                  "--out", out_path, "--no-notify"])
            self.assertEqual(code, 0, err)
            lines = out.splitlines()
            ready = next(i for i, l in enumerate(lines) if "GET READY" in l)
            trig = next(i for i, l in enumerate(lines) if "TRIGGERED" in l)
            self.assertLess(ready, trig)
            self.assertTrue(lines[ready].startswith("10:15 Cairo"))
            self.assertNotIn("LATE", out)
            with open(out_path) as fh:
                d = json.load(fh)
            self.assertEqual((d["status"], d["time"], d["price"], d["source"], d["delayed_seconds"]),
                             ("triggered", "10:25", 122.41, "tradingview", 900))
        finally:
            shutil.rmtree(tmp)

    def test_replay_without_warning_is_late(self):
        code, out, _ = run(["watch", "COMI", "--offline", COMI, "--now", "2026-11-03 09:55",
                            "--near-pct", "0", "--out", os.path.join(tempfile.mkdtemp(), "e.json"), "--no-notify"])
        self.assertEqual(code, 0)
        self.assertIn("LATE: no GET READY 5+ minutes before this trigger", out)


class Cli(unittest.TestCase):
    def test_check_writes_entry_json(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "entry.json")
            code, out, _ = run(["check", "COMI", "--offline", COMI, "--now", "2026-11-03 10:26", "--write", p])
            self.assertEqual(code, 0)
            with open(p) as fh:
                d = json.load(fh)
            self.assertEqual((d["symbol"], d["price"], d["level"]), ("COMI", 122.41, 122.0))
        finally:
            shutil.rmtree(tmp)

    def test_short_side_is_refused(self):
        code, _, err = run(["check", "COMI", "--offline", COMI, "--side", "sell"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)

    def test_wrong_symbol_in_file(self):
        code, _, err = run(["check", "ETEL", "--offline", COMI])
        self.assertEqual(code, 1)
        self.assertIn("holds COMI", err)


if __name__ == "__main__":
    unittest.main()
