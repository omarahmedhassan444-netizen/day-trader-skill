"""trader-exit tests. Bars for the ATR stop reuse tests/fixtures/trader-entry/
(COMI's real 3 Nov 2026 five-minute bars on EGX, see test_entry.py).

The reference trade: COMI triggered at 10:25 Cairo at EGP 122.41 over a 15-minute
range of 121.51 to 122.00. The or-mid stop is floor(121.755) = 121.75, the risk
0.66 a share, the 1R target 123.07 and the 2R target 123.73. Flat by 14:20."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX

exitp = load("trader-exit", "exit")

COMI = os.path.join(FIX, "trader-entry", "COMI-2026-11-03-5m.json")

REF_ENTRY = {"created_by": "trader-entry", "symbol": "COMI", "setup": "orb", "side": "buy", "date": "2026-11-03",
             "status": "triggered", "time": "10:25", "bar_start": "10:20", "bar_index": 4, "price": 122.41,
             "level": 122.0, "interval": "5m", "opening_range": {"high": 122.0, "low": 121.51, "minutes": 15},
             "source": "tradingview", "delayed_seconds": 900, "checked_at": "2026-11-03T08:26:00Z"}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = exitp.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=122.00, ol=121.51, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Math(unittest.TestCase):
    def test_reference_or_mid_1r(self):
        p = c(122.41, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("121.75"))          # floor(121.755)
        self.assertEqual(p["risk"], Decimal("0.66"))
        self.assertEqual(p["target"], Decimal("123.07"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertIn("rounded down", p["stop_detail"])

    def test_default_2r(self):
        self.assertEqual(c(122.41, "or-mid")["target"], Decimal("123.73"))

    def test_or_low_and_pct(self):
        self.assertEqual(c(122.41, "or-low")["stop"], Decimal("121.51"))
        p = c(122.41, "pct:1")
        self.assertEqual(p["stop"], Decimal("121.18"))           # floor(121.1859)

    def test_penny_stock_uses_thousandths(self):
        p = c(0.401, "pct:3", "2R", None, None)
        self.assertEqual(p["stop"], Decimal("0.388"))
        self.assertEqual(p["target"], Decimal("0.427"))

    def test_refusals(self):
        with self.assertRaises(exitp.PlanError):
            c(122.41, "price:123")                               # stop above the entry
        with self.assertRaises(exitp.PlanError):
            c(122.41, "or-mid", "price:122")                     # target under the entry
        with self.assertRaises(exitp.PlanError) as cm:
            c(122.41, "price:122.40")                            # 0.01 risk: inside the spread
        self.assertIn("too tight", str(cm.exception))

    def test_money_is_egp(self):
        self.assertEqual(exitp.money(122.41), "EGP 122.41")
        self.assertEqual(exitp.money(0.401), "EGP 0.401")


class FlatBy(unittest.TestCase):
    def test_default_is_14_20(self):
        import datetime
        t, note = exitp.resolve_flat_by(datetime.date(2026, 11, 3), None, dict(exitp.DEFAULTS), None)
        self.assertEqual((t, note), ("14:20", ""))

    def test_ramadan_moves_it_up(self):
        import datetime
        t, note = exitp.resolve_flat_by(datetime.date(2026, 3, 1), None, dict(exitp.DEFAULTS), None)
        self.assertEqual(t, "13:20")
        self.assertIn("short session", note)

    def test_after_the_close_is_refused(self):
        import datetime
        with self.assertRaises(exitp.PlanError):
            exitp.resolve_flat_by(datetime.date(2026, 11, 3), "14:45", dict(exitp.DEFAULTS), None)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.entry = os.path.join(self.tmp, "entry.json")
        with open(self.entry, "w") as fh:
            json.dump(REF_ENTRY, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_plan_from_entry_json(self):
        plan_path = os.path.join(self.tmp, "plan.json")
        code, out, err = run(["plan", "--from", self.entry, "--target", "1R", "--now", "2026-11-03 10:26",
                              "--write", plan_path, "--config", os.path.join(self.tmp, "none.json")])
        self.assertEqual(code, 0, err)
        with open(plan_path) as fh:
            plan = json.load(fh)
        for k, v in {"symbol": "COMI", "entry": 122.41, "entry_time": "10:25", "stop": 121.75, "target": 123.07,
                     "risk_per_share": 0.66, "flat_by": "14:20"}.items():
            self.assertEqual(plan[k], v, k)
        self.assertIn("EGP 121.75", out)
        self.assertIn("14:20 Cairo", out)
        self.assertIn("broker app", out)
        self.assertNotIn("$", out)

    def test_atr_stop_reads_the_bars(self):
        code, out, err = run(["plan", "--from", self.entry, "--stop", "atr:1.5", "--offline", COMI,
                              "--now", "2026-11-03 10:26", "--config", os.path.join(self.tmp, "none.json"), "--json"])
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["atr_bars"], 5)
        self.assertLess(plan["stop"], 122.41)

    def test_untriggered_entry_is_refused(self):
        with open(self.entry, "w") as fh:
            json.dump(dict(REF_ENTRY, status="waiting"), fh)
        code, _, err = run(["plan", "--from", self.entry])
        self.assertEqual(code, 1)
        self.assertIn("not triggered", err)

    def test_short_is_refused(self):
        code, _, err = run(["plan", "--symbol", "COMI", "--entry", "122", "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)


if __name__ == "__main__":
    unittest.main()
