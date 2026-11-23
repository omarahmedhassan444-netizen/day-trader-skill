"""trader-risk tests: the EGX pre-trade check.

orders-comi.json  trader/orders.json from trader-budget for the reference trade:
                  buy 306 COMI at market, stop 121.75, target 123.07 (3 Nov 2026).
"""

import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
COMI_PATH = os.path.join(FIX, "trader-risk", "orders-comi.json")
NOW = "2026-11-03 10:26"
TODAY = "2026-11-03"


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def comi(**over):
    with open(COMI_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def bracket(sym, qty, entry, stop, target):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "%g" % target,
            "stop_loss_stop_price": "%g" % stop, "plan": {"entry": entry, "stop": stop, "target": target}}


def placed(sym, side="buy", day=TODAY, max_loss=201.96, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T10:30:00+02:00"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def one(order, now=NOW, state=None, **over):
    return check.check([order], cfg(**over), state=state, now=at(now))


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class Reference(unittest.TestCase):
    def test_the_comi_order_passes(self):
        res = one(comi())
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))
        info = " ".join(res["results"][0]["info"])
        self.assertIn("max loss EGP 201.96 (limit EGP 500)", info)
        self.assertIn("risk 0.66 a share", info)

    def test_over_the_max_loss(self):
        res = one(comi(qty="800"), max_order_egp=1000000, max_position_pct=100, budget=200000)
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("loss: 800 shares x 0.66 risk a share = EGP 528 at the stop, over your EGP 500", reasons(res))

    def test_old_usd_keys_still_work(self):
        c = cfg()
        c.pop("max_loss_per_trade_egp")
        c["max_loss_per_trade_usd"] = 100
        res = check.check([comi()], c, now=at(NOW))
        self.assertIn("over your EGP 100 max loss", reasons(res))


class Window(unittest.TestCase):
    def test_cairo_window(self):
        cases = [("2026-11-03 10:10", "no new entries before 10:15 Cairo (it is 10:10)"),
                 ("2026-11-03 14:01", "no new entries after 14:00 Cairo (it is 14:01)"),
                 ("2026-11-03 09:30", "EGX is closed (Tue 2026-11-03 09:30"),
                 ("2026-11-03 14:35", "EGX is closed"),
                 ("2026-11-06 11:00", "EGX is closed today (weekend"),
                 ("2026-10-08 11:00", "EGX is closed today (EGX holiday"),
                 ("2026-03-01 13:01", "no new entries after 13:00 Cairo")]       # Ramadan: close 13:30
        for now, want in cases:
            self.assertIn("window: " + want, reasons(one(comi(), now=now)), now)

    def test_sunday_is_a_trading_day(self):
        self.assertEqual(verdicts(one(comi(), now="2026-11-01 11:00")), ["PASS"])

    def test_ramadan_warns(self):
        res = one(comi(), now="2026-03-01 12:00")
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("short session today (EGX closes 13:30)", " ".join(res["warnings"]))

    def test_window_is_cairo_time_not_the_machine(self):
        self.assertEqual(at("2026-11-03 10:26"), datetime.datetime(2026, 11, 3, 8, 26))
        self.assertEqual(at("2026-07-07 10:26"), datetime.datetime(2026, 7, 7, 7, 26))    # summer time


class SameDay(unittest.TestCase):
    def test_empty_list_warns(self):
        res = one(comi())
        self.assertIn("same-day: check that COMI is on EGX's intraday (T+0) trading list", warnings(res))

    def test_on_the_list_passes_quietly(self):
        res = one(comi(), same_day_list=["COMI", "ETEL"])
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertNotIn("same-day", warnings(res))

    def test_off_the_list_blocks(self):
        res = one(comi(), same_day_list=["ETEL"])
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("same-day: COMI is not on your same_day_list", reasons(res))


class Limits(unittest.TestCase):
    def test_stop_required(self):
        o = comi()
        o.pop("stop_loss_stop_price")
        o["plan"].pop("stop")
        self.assertIn("stop: no stop", reasons(one(o)))

    def test_reward_risk(self):
        res = one(bracket("COMI", 100, 122.41, 121.75, 122.80))
        self.assertIn("reward:risk: 0.59", reasons(res))

    def test_daily_loss_and_trades(self):
        st = {"orders": [placed("ETEL", max_loss=700), placed("TMGH", max_loss=700)]}
        res = one(comi(), state=st)
        self.assertIn("daily loss: EGP 1,400 already at risk today plus EGP 201.96", reasons(res))
        st = {"orders": [placed("ETEL", max_loss=1), placed("TMGH", max_loss=1), placed("HRHO", max_loss=1)]}
        self.assertIn("trades: 3 entries placed today", reasons(one(comi(), state=st)))

    def test_penny_stock_steps(self):
        res = one(bracket("INEG", 10000, 0.401, 0.388, 0.427), max_order_egp=100000, max_position_pct=100)
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))
        info = " ".join(res["results"][0]["info"])
        self.assertIn("stop 0.388", info)
        self.assertIn("risk 0.013 a share", info)

    def test_short_is_blocked(self):
        res = one({"symbol": "COMI", "side": "sell", "qty": "10", "type": "market", "time_in_force": "day"})
        self.assertIn("short:", reasons(res))

    def test_blocked_list(self):
        self.assertIn("blocked", reasons(one(comi(), blocked=["COMI"])))


class Cli(unittest.TestCase):
    def test_cli_writes_verdicts(self):
        tmp = tempfile.mkdtemp()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = check.main(["--orders", COMI_PATH, "--config", CFG_PATH, "--now", NOW,
                               "--state", os.path.join(tmp, "broker.json"), "--out", os.path.join(tmp, "risk.json"),
                               "--portfolio", os.path.join(tmp, "none.json"), "--prices", os.path.join(tmp, "p.json")])
        self.assertEqual(code, 0, err.getvalue())
        text = out.getvalue()
        self.assertIn("1 passed, 0 blocked at 2026-11-03 10:26 Cairo", text)
        self.assertIn("enter it yourself in your broker app", text)
        self.assertNotIn("$", text)
        with open(os.path.join(tmp, "risk.json")) as fh:
            self.assertTrue(json.load(fh))


if __name__ == "__main__":
    unittest.main()
