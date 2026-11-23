"""trader-timemachine tests on EGX data.

COMI-60d5m.json.gz  COMI's real five-minute bars on EGX for the 62 sessions from
                    6 Aug to 3 Nov 2026 (TradingView through egx_data.py, chart
                    JSON layout, delay 900 seconds).

The plan is the reference trade (see test_exit.py): buy COMI at 122.41 at 10:25 on
3 Nov 2026, stop 121.75 (opening-range midpoint), target 123.07 (1R)."""

import copy
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
COMI = os.path.join(DIR, "COMI-60d5m.json.gz")
with gzip.open(COMI, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-11-03 10:26"
NOW_UTC = tm.parse_now(NOW)

PLAN = {"created_by": "trader-exit", "symbol": "COMI", "side": "buy", "date": "2026-11-03", "setup": "orb",
        "entry": 122.41, "entry_time": "10:25", "entry_bar": 4, "interval": "5m",
        "opening_range": {"high": 122.0, "low": 121.51, "minutes": 15},
        "stop": 121.75, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 123.07, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 0.66, "reward_risk": 1.0, "flat_by": "14:20"}


def run_tm(plan=None, **kw):
    return tm.time_machine(TEXT, tm.normalize_plan(plan or PLAN), NOW_UTC, **kw)


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Sessions(unittest.TestCase):
    def test_bars_are_cairo_regular_session(self):
        _, bars, dropped = tm.parse_bars(tm.chart_result(TEXT, "COMI"), NOW_UTC)
        self.assertEqual(bars[0]["start"], "10:00")
        self.assertTrue(all("10:00" <= b["start"] <= "14:25" for b in bars))
        self.assertEqual(bars[-1]["date"], "2026-11-03")

    def test_gaps_are_filled_flat_and_thin_days_left_out(self):
        _, bars, _ = tm.parse_bars(tm.chart_result(TEXT, "COMI"), NOW_UTC)
        full, short = tm.full_sessions(tm.split_sessions(bars), "2026-11-03")
        self.assertEqual(len(full), 60)
        self.assertEqual([s["date"] for s in short], ["2026-10-25"])
        self.assertTrue(all(len(D) == tm.SESSION_BARS for _, D in full))
        self.assertEqual(tm.filled_count(full), 88)
        filled = [b for _, D in full for b in D if b.get("filled")]
        self.assertTrue(all(b["o"] == b["h"] == b["l"] == b["c"] for b in filled))

    def test_fill_rule(self):
        def day(n_missing):
            bars = {i: {"date": "2026-11-02", "slot": i, "start": "x", "o": 1, "h": 1, "l": 1, "c": 1.0 + i / 100.0,
                        "v": 1} for i in range(tm.SESSION_BARS)}
            for i in range(1, n_missing + 1):
                del bars[i * 2]
            return {"2026-11-02": bars}
        full, short = tm.full_sessions(day(5), "2026-11-03")
        self.assertEqual(len(full), 1)
        self.assertEqual(full[0][1][2]["c"], full[0][1][1]["c"])      # flat at the previous close
        full, short = tm.full_sessions(day(6), "2026-11-03")           # 48 of 54 < 90 %
        self.assertEqual((len(full), short[0]["reason"]), (0, "thin day"))


class Replays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = run_tm()

    def test_numbers(self):
        mc = self.doc["mc"]
        self.assertEqual(mc["pool_sessions"], 60)
        self.assertEqual((mc["pool_from"], mc["pool_to"]), ("2026-08-06", "2026-11-02"))
        self.assertEqual(mc["until"], "14:30")
        self.assertEqual((round(100 * mc["p_target"], 1), round(100 * mc["p_stop"], 1),
                          round(100 * mc["p_neither"], 1)), (44.1, 55.1, 0.8))

    def test_history(self):
        h = self.doc["history"]
        self.assertEqual(h["sessions"], 60)
        self.assertEqual(h["triggered"], 30)
        outcomes = [t["outcome"] for t in h["trades"]]
        self.assertEqual((outcomes.count("target"), outcomes.count("stop"), outcomes.count("close")), (9, 16, 5))

    def test_verdict(self):
        self.assertEqual(self.doc["verdict"], "Leans toward the stop: target first 44.1 % vs stop first 55.1 %.")

    def test_same_seed_same_numbers(self):
        again = run_tm()
        self.assertEqual(again["mc"]["p_target"], self.doc["mc"]["p_target"])
        other = run_tm(seed=7)
        self.assertNotEqual(other["mc"]["paths"] if "paths" in other["mc"] else other["mc"]["p_target"],
                            self.doc["mc"]["paths"] if "paths" in self.doc["mc"] else self.doc["mc"]["p_target"])

    def test_source_and_delay(self):
        self.assertEqual(self.doc["source"], "tradingview")
        self.assertEqual(self.doc["data"]["delay_seconds"], 900)
        self.assertTrue(self.doc["data"]["last_bar"].endswith("Cairo"))

    def test_until_flat_by(self):
        doc = run_tm(until="14:20")
        self.assertEqual(doc["mc"]["until"], "14:20")
        with self.assertRaises(tm.TMError):
            run_tm(until="15:00")


class Plans(unittest.TestCase):
    def test_entry_time_to_bar(self):
        p = tm.normalize_plan(dict(PLAN, entry_bar=None))
        self.assertEqual((p["entry_bar"], p["entry_time"]), (4, "10:25"))

    def test_entry_outside_the_session(self):
        with self.assertRaises(tm.TMError) as cm:
            tm.normalize_plan(dict(PLAN, entry_bar=None, entry_time="14:30"))
        self.assertIn("between 10:05 and 14:25", str(cm.exception))

    def test_bad_order(self):
        with self.assertRaises(tm.TMError):
            tm.normalize_plan(dict(PLAN, stop=123.0))

    def test_1m_is_refused(self):
        with self.assertRaises(tm.TMError):
            tm.normalize_plan(dict(PLAN, interval="1m"))

    def test_wrong_symbol(self):
        with self.assertRaises(tm.TMError):
            tm.time_machine(TEXT, tm.normalize_plan(dict(PLAN, symbol="ETEL")), NOW_UTC)

    def test_too_few_sessions(self):
        with self.assertRaises(tm.TMError) as cm:
            tm.time_machine(TEXT, tm.normalize_plan(dict(PLAN, date="2026-08-20", entry=124.0, stop=123.0,
                                                         target=125.0)), tm.parse_now("2026-08-20 10:26"))
        self.assertIn("at least 20", str(cm.exception))


class Cli(unittest.TestCase):
    def test_run_and_chart(self):
        tmp = tempfile.mkdtemp()
        try:
            plan = os.path.join(tmp, "plan.json")
            with open(plan, "w") as fh:
                json.dump(PLAN, fh)
            out_path = os.path.join(tmp, "tm.json")
            code, out, err = cli(["run", "--plan", plan, "--offline", COMI, "--now", NOW, "--write", out_path])
            self.assertEqual(code, 0, err)
            self.assertIn("target first   44.1 %", out)
            self.assertIn("88 of the pool's 3240 bars had no trades", out)
            self.assertNotIn("$", out)
            code, out, err = cli(["chart", "--from", out_path])
            self.assertEqual(code, 0, err)
            self.assertIn("14:30", out)
            self.assertIn("entry", out)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
