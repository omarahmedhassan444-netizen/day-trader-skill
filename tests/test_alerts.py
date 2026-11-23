"""trader-alerts tests: the EGX clock, TradingView quotes and the 5-minute warnings.

tradingview-COMI.json / -ETEL.json  quote fields from TradingView's quote session on
                                    22 Nov 2026 (COMI last 125.01, previous close 127.50;
                                    delayed_streaming_900)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(FIX, "trader-budget", "plan-comi.json")


def status(text):
    return alerts.market_status(alerts.parse_now(text))


def quotes(**prices):
    q, _ = alerts.get_quotes(["COMI"], QDIR)
    for sym, p in prices.items():
        q[sym]["price"] = p
    return q


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


class Clock(unittest.TestCase):
    def test_open_and_closed(self):
        self.assertTrue(status("2026-11-03 10:00")["open"])
        self.assertFalse(status("2026-11-03 14:30")["open"])
        st = status("2026-11-06 11:00")                     # Friday
        self.assertFalse(st["open"])
        self.assertEqual(st["next_open"], "2026-11-08 10:00")
        self.assertEqual(status("2026-11-03 09:00")["next_open"], "2026-11-03 10:00")

    def test_ramadan_short_session(self):
        st = status("2026-03-01 13:00")
        self.assertEqual((st["opens"], st["closes"]), ("10:00", "13:30"))
        self.assertIn("short session", st["short_session"])


class Quotes(unittest.TestCase):
    def test_tradingview_quote(self):
        q, errors = alerts.get_quotes(["COMI", "ETEL", "NOPE"], QDIR)
        self.assertEqual(q["COMI"]["price"], 125.01)
        self.assertEqual(q["COMI"]["prev_close"], 127.5)
        self.assertEqual(q["COMI"]["delay_seconds"], 900)
        self.assertEqual(q["COMI"]["source"], "tradingview")
        self.assertEqual(len(errors), 1)
        self.assertIn("NOPE", errors[0])                   # reported, not dropped

    def test_write_prices(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "prices.json")
            q, _ = alerts.get_quotes(["COMI"], QDIR)
            doc = alerts.write_prices(p, q, alerts.parse_now("2026-11-03 10:30"))
            self.assertEqual(doc["prices"]["COMI"], 125.01)
            self.assertEqual(doc["currency"], "EGP")
            self.assertEqual(doc["detail"]["COMI"]["delay_seconds"], 900)
        finally:
            shutil.rmtree(tmp)


class PlanRules(unittest.TestCase):
    def test_rules_from_the_plan(self):
        rules = {r["id"]: r for r in plan_rules()}
        self.assertEqual(rules["comi-plan-warn-stop"]["price"], 122.08)      # 122.41 - 0.5 x 0.66
        self.assertEqual(rules["comi-plan-warn-target"]["price"], 122.74)    # 122.41 + 0.5 x 0.66
        self.assertEqual(rules["comi-plan-stop"]["price"], 121.75)
        self.assertEqual(rules["comi-plan-target"]["price"], 123.07)
        self.assertEqual(rules["comi-flat-by-warning"]["at"], "14:15")       # 5 minutes before 14:20
        self.assertEqual(rules["comi-flat-by"]["at"], "14:20")
        self.assertTrue(all(r["date"] == "2026-11-03" for r in rules.values()))

    def test_get_ready_rings_before_the_stop(self):
        rules = plan_rules()
        now = alerts.parse_now("2026-11-03 11:00")
        rings, st = alerts.evaluate(rules, quotes(COMI=122.05), status("2026-11-03 11:00"), {}, now)
        self.assertEqual([r["id"] for r in rings], ["comi-plan-warn-stop"])
        self.assertTrue(rings[0]["message"].startswith("GET READY: COMI is EGP 122.05 (15 min late)"))
        rings, st = alerts.evaluate(rules, quotes(COMI=121.70), status("2026-11-03 11:05"), st, now)
        self.assertEqual([r["id"] for r in rings], ["comi-plan-stop"])        # the warning rang once only

    def test_five_minute_warning_then_flat_by(self):
        rules = plan_rules()
        q = quotes(COMI=122.50)
        _, st = alerts.evaluate(rules, q, status("2026-11-03 14:14"), {}, alerts.parse_now("2026-11-03 14:14"))
        rings, st = alerts.evaluate(rules, q, status("2026-11-03 14:15"), st, alerts.parse_now("2026-11-03 14:15"))
        self.assertEqual([r["id"] for r in rings], ["comi-flat-by-warning"])
        self.assertIn("Open your broker app now and get the COMI sell ready", rings[0]["message"])
        rings, st = alerts.evaluate(rules, q, status("2026-11-03 14:20"), st, alerts.parse_now("2026-11-03 14:20"))
        self.assertEqual([r["id"] for r in rings], ["comi-flat-by"])
        self.assertIn("sell it now in your broker app", rings[0]["message"])

    def test_before_close_moves_up_in_ramadan(self):
        rule = {"id": "w", "type": "time", "at": "14:15", "before_close": True}
        self.assertEqual(alerts.ring_time(rule, status("2026-03-01 12:00")).strftime("%H:%M"), "13:15")
        self.assertEqual(alerts.ring_time(rule, status("2026-11-03 12:00")).strftime("%H:%M"), "14:15")

    def test_pct_move_from_previous_close(self):
        rule = [{"id": "m", "symbol": "COMI", "type": "pct_move", "pct": 1.5, "direction": "down"}]
        rings, _ = alerts.evaluate(rule, quotes(), status("2026-11-03 11:00"), {}, alerts.parse_now("2026-11-03 11:00"))
        self.assertEqual(len(rings), 1)
        self.assertIn("COMI moved -1.95% to EGP 125.01", rings[0]["message"])


class Cli(unittest.TestCase):
    def test_add_from_plan_and_check(self):
        tmp = tempfile.mkdtemp()
        try:
            rules, state = os.path.join(tmp, "alerts.json"), os.path.join(tmp, "state.json")
            code, out, err = run(["add", "--from-plan", "--plan", PLAN, "--rules", rules, "--state", state,
                                  "--config", os.path.join(tmp, "none.json")])
            self.assertEqual(code, 0, err)
            code, out, err = run(["check", "--rules", rules, "--state", state, "--offline", QDIR,
                                  "--prices-out", os.path.join(tmp, "p.json"), "--now", "2026-11-03 14:15"])
            self.assertEqual(code, 0, err)
            self.assertIn("EGX OPEN (2026-11-03 14:15 Cairo", out)
            self.assertIn("5-minute warning: sell by 14:20", out)
            self.assertNotIn("$", out)
        finally:
            shutil.rmtree(tmp)

    def test_clock_cli(self):
        code, out, _ = run(["clock", "--now", "2026-11-06 11:00"])
        self.assertEqual(code, 1)
        self.assertIn("EGX closed", out)
        self.assertIn("Next open: 2026-11-08 10:00 Cairo", out)


if __name__ == "__main__":
    unittest.main()
