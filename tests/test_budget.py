"""trader-budget tests: sizing the COMI reference trade in EGP.

plan-comi.json  trader/plan.json from trader-exit for 3 Nov 2026: buy COMI at 122.41,
                stop 121.75, target 123.07 (1R), flat by 14:20.

With the example config (budget EGP 50,000, max loss EGP 500 a trade, 75 % position
cap, EGP 40,000 max order, 10 % cash buffer) the position cap binds: 306 shares.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-comi.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
NOW = "2026-11-03 10:26"
TODAY = "2026-11-03"


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


class Config(unittest.TestCase):
    def test_example_config_is_egp_and_manual(self):
        c = cfg()
        for k in ("max_loss_per_trade_egp", "max_daily_loss_egp", "max_order_egp", "min_order_egp"):
            self.assertIn(k, c)
        self.assertEqual((c["broker"], c["manual"], c["paper"]), ("telda", True, True))
        self.assertEqual((c["no_entries_before"], c["no_entries_after"], c["flat_by"]), ("10:15", "14:00", "14:20"))
        self.assertFalse(any(k.endswith("_usd") for k in c))

    def test_old_usd_keys_are_read_as_egp(self):
        c = size.egp_keys({"max_loss_per_trade_usd": 300})
        self.assertEqual(c["max_loss_per_trade_egp"], 300)


class Sizing(unittest.TestCase):
    def test_reference_trade(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (306, "position cap"))
        self.assertEqual(s["risk_per_share"], 0.66)
        self.assertEqual(s["cost"], 37457.46)
        self.assertEqual(s["max_loss"], 201.96)
        limits = {l["name"]: l["shares"] for l in s["limits"]}
        self.assertEqual(limits, {"max loss": 757, "position cap": 306, "max order": 326, "cash": 367})

    def test_max_loss_binds_on_a_small_budget_limit(self):
        s = size.size_trade(plan(), cfg(max_loss_per_trade_egp=100), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (151, "max loss"))      # floor(100 / 0.66)

    def test_fees_are_part_of_the_loss(self):
        s = size.size_trade(plan(), cfg(max_loss_per_trade_egp=100, fee_pct=0.1), today=TODAY)
        # 0.66 risk + 0.1 % of (122.41 + 121.75) = 0.66 + 0.24416 a share
        self.assertEqual(s["qty"], 110)
        self.assertAlmostEqual(s["max_loss"], round(110 * (0.66 + 0.2442), 2), places=1)

    def test_penny_stock_steps(self):
        p = plan(symbol="INEG", entry=0.401, stop=0.388, target=0.427)
        s = size.size_trade(p, cfg(max_loss_per_trade_egp=130, max_position_pct=100, max_order_egp=1000000),
                            today=TODAY)
        self.assertEqual(s["risk_per_share"], 0.013)
        self.assertEqual(s["qty"], 10000)                                      # 130 / 0.013

    def test_refusals(self):
        s = size.size_trade(plan(), cfg(max_loss_per_trade_egp=0.5), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("more than your EGP 0.50 max loss", s["refusal"])
        with self.assertRaises(size.SizeError):
            size.size_trade(plan(), cfg(), today="2026-11-04")                 # another day's plan
        with self.assertRaises(size.SizeError):
            size.size_trade(plan(side="sell"), cfg(), today=TODAY)
        with self.assertRaises(size.SizeError):
            size.size_trade(plan(), cfg(blocked=["COMI"]), today=TODAY)

    def test_order_ticket(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        o = size.bracket_order(s)
        self.assertEqual((o["symbol"], o["qty"], o["take_profit_limit_price"], o["stop_loss_stop_price"]),
                         ("COMI", "306", "123.07", "121.75"))
        self.assertIn("EGP 201.96", o["why"])


class Cli(unittest.TestCase):
    def test_write_orders(self):
        tmp = tempfile.mkdtemp()
        out_path = os.path.join(tmp, "orders.json")
        code, out, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--write", out_path])
        self.assertEqual(code, 0, err)
        self.assertIn("306 shares of COMI = EGP 37,457.46", out)
        self.assertIn("enter it yourself in your broker app", out)
        self.assertIn("14:20 Cairo", out)
        self.assertNotIn("$", out)
        with open(out_path) as fh:
            doc = json.load(fh)
        self.assertTrue(doc["paper"])
        self.assertEqual(doc["orders"][0]["qty"], "306")
        self.assertIn("broker app", doc["note"])

    def test_weights_mode_in_egp(self):
        tmp = tempfile.mkdtemp()
        prices = os.path.join(tmp, "prices.json")
        with open(prices, "w") as fh:     # 3 Nov 2026 closes, EGX
            json.dump({"prices": {"COMI": 123.48, "ETEL": 137.0, "TMGH": 76.5, "HRHO": 25.5}}, fh)
        code, out, err = run(["--targets", TARGETS, "--budget", "50000", "--config", CFG, "--prices", prices,
                              "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        total = sum(p["egp"] for p in doc["result"]["positions"])
        self.assertEqual(doc["result"]["positions"][0]["shares"], 146)
        self.assertLessEqual(total, 45000.0 + 0.01)


if __name__ == "__main__":
    unittest.main()
