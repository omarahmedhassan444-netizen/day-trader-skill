"""trader-broker tests: tickets you enter by hand in your broker app (Telda has no API).

Uses tests/fixtures/trader-risk/orders-comi.json: buy 306 COMI, stop 121.75, target 123.07."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-comi.json")
NOW = "2026-11-03 10:26"
TODAY = "2026-11-03"
CASH_BUY = {"symbol": "ETEL", "side": "buy", "notional": "10000", "type": "market", "time_in_force": "day"}
BATCH = ["ETEL", "COMI", "TMGH"]


def comi(**over):
    with open(ORDERS_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=CASH_BUY, batch=BATCH):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes buy it", "yes ETEL", "yes 10000", "yes EGP 10,000 of ETEL"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "go", "yeah", "yes to all", "yes both", "yes COMI", "yes 5000",
                  "yes but make it 5000", "no", "yes, wait", "yes if it is under 130"):
            self.assertFalse(self.yes(r), r)

    def test_a_bracket_yes(self):
        o = comi()
        for r in ("yes", "yes 306 COMI", "yes 121.75", "yes target 123.07"):
            self.assertTrue(self.yes(r, o, ["COMI"]), r)
        for r in ("yes 300", "yes but stop at 121", "yes ETEL"):
            self.assertFalse(self.yes(r, o, ["COMI", "ETEL"]), r)


class Ticket(unittest.TestCase):
    def test_plain_words_in_egp(self):
        self.assertEqual(ticket.plain(comi()),
                         "BUY 306 COMI, market, good for today / stop-loss 121.75 / take-profit 123.07 / max loss "
                         "about EGP 201.96 if the stop fills at 121.75 (it can fill worse in a fast market)")

    def test_steps_for_telda(self):
        steps = ticket.entry_steps(comi(), {"broker": "telda", "flat_by": "14:20"})
        text = "\n".join(steps)
        self.assertTrue(steps[0].startswith("In Telda: BUY 306 shares of COMI, a MARKET order"))
        self.assertIn("If Telda has no stop order for EGX, trader-alerts rings at 121.75", text)
        self.assertIn("Flat by 14:20 Cairo", text)
        self.assertIn("15 min late", text)

    def test_json_ticket_is_manual(self):
        j = ticket.ticket_json(comi(), 1, {"broker": "telda", "paper": True}, None, {}, TODAY)
        self.assertEqual((j["broker"], j["manual"], j["paper"]), ("telda", True, True))
        self.assertNotIn("tool", j)
        self.assertEqual(j["ticket"]["stop_loss_stop_price"], "121.75")

    def test_penny_prices(self):
        o = {"symbol": "INEG", "side": "buy", "qty": "10000", "type": "limit", "limit_price": "0.401",
             "time_in_force": "day"}
        self.assertIn("LIMIT order at 0.401", ticket.entry_steps(o, {})[0])


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orders = os.path.join(self.tmp, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.risk = os.path.join(self.tmp, "risk.json")
        self.state = os.path.join(self.tmp, "broker.json")
        self.pf = os.path.join(self.tmp, "portfolio.json")
        self.common = ["--orders", self.orders, "--config", CFG_PATH, "--risk", self.risk, "--state", self.state,
                       "--now", NOW]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def risk_check(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = check.main(["--orders", self.orders, "--config", CFG_PATH, "--now", NOW, "--state", self.state,
                               "--out", self.risk, "--portfolio", self.pf, "--prices", os.path.join(self.tmp, "p")])
        return code

    def test_not_ready_before_the_risk_check(self):
        code, out, _ = run(["show"] + self.common)
        self.assertEqual(code, 1)
        self.assertIn("NOT READY: trader-risk has not checked these orders", out)

    def test_paper_flow(self):
        self.assertEqual(self.risk_check(), 0)
        code, out, _ = run(["show"] + self.common)
        self.assertEqual(code, 0)
        self.assertIn("PAPER: practice only, do NOT enter it in Telda", out)
        self.assertIn("READY", out)
        code, out, _ = run(["approve", "--index", "1", "--reply", "sure"] + self.common)
        self.assertEqual(code, 1)
        code, out, _ = run(["approve", "--index", "1", "--reply", "yes"] + self.common)
        self.assertEqual(code, 0)
        self.assertIn("Paper trade: do NOT enter it in Telda", out)
        code, out, err = run(["result", "--index", "1", "--status", "entered", "--filled-qty", "306",
                              "--filled-price", "122.45", "--portfolio", self.pf] + self.common)
        self.assertEqual(code, 0, err)
        with open(self.state) as fh:
            st = json.load(fh)
        placed = [r for r in st["orders"] if r["status"] == "placed"]
        self.assertEqual(len(placed), 1)
        self.assertTrue(placed[0]["at"].endswith("+02:00"))
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual([p["shares"] for p in pf["positions"] if p.get("ticker", p.get("symbol")) == "COMI"],
                         [306])

    def test_real_money_prints_the_steps(self):
        self.assertEqual(self.risk_check(), 0)
        live = os.path.join(self.tmp, "live.json")
        with open(CFG_PATH) as fh:
            c = json.load(fh)
        c["paper"] = False
        with open(live, "w") as fh:
            json.dump(c, fh)
        args = ["--orders", self.orders, "--config", live, "--risk", self.risk, "--state", self.state, "--now", NOW]
        code, out, _ = run(["approve", "--index", "1", "--reply", "yes 306 COMI"] + args)
        self.assertEqual(code, 0)
        self.assertIn("Now enter it yourself in Telda:", out)
        self.assertIn("1. In Telda: BUY 306 shares of COMI", out)

    def test_skipped_counts_as_declined(self):
        self.assertEqual(self.risk_check(), 0)
        run(["approve", "--index", "1", "--reply", "yes"] + self.common)
        code, out, _ = run(["result", "--index", "1", "--status", "skipped"] + self.common)
        self.assertEqual(code, 0)
        self.assertIn("as declined", out)

    def test_flatten(self):
        code, out, err = run(["flatten", "--symbol", "COMI", "--qty", "306", "--orders", self.orders,
                              "--config", CFG_PATH, "--now", "2026-11-03 14:15",
                              "--plan", os.path.join(self.tmp, "none.json")])
        self.assertEqual(code, 0, err)
        self.assertIn("flat by 14:20 Cairo", out)
        self.assertIn("cancel any open stop-loss or take-profit", out)
        self.assertNotIn("Alpaca", out)


if __name__ == "__main__":
    unittest.main()
