"""trader-journal tests. Offline: the TSLA plan from the reel
(tests/fixtures/trader-budget/plan-tsla.json) and trades written to a temp dir."""

import csv
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX

journal = load("trader-journal", "journal")

PLAN = os.path.join(FIX, "trader-budget", "plan-tsla.json")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = journal.main(argv)
    return code, out.getvalue(), err.getvalue()


class Math(unittest.TestCase):
    def test_the_reel_at_the_target_is_plus_1r(self):
        t = journal.compute(364.18, 358.32, 370.04, 8)
        self.assertEqual(t["pnl_usd"], Decimal("46.88"))
        self.assertEqual(t["pnl_r"], Decimal("1.00"))

    def test_a_clean_stop_out_is_minus_1r(self):
        t = journal.compute(364.18, 358.32, 358.32, 8)
        self.assertEqual(t["pnl_usd"], Decimal("-46.88"))
        self.assertEqual(t["pnl_r"], Decimal("-1.00"))

    def test_a_gap_past_the_stop_is_worse_than_minus_1r(self):
        t = journal.compute(364.18, 358.32, 355.00, 8)
        self.assertEqual(t["pnl_usd"], Decimal("-73.44"))
        self.assertEqual(t["pnl_r"], Decimal("-1.57"))

    def test_raw_floats_give_the_same_cents(self):
        t = journal.compute(364.1759948730469, "358.32", "$370.04", "8")
        self.assertEqual((t["entry"], t["pnl_usd"]), (Decimal("364.18"), Decimal("46.88")))

    def test_refusals(self):
        for args in ((100, 100, 101, 1), (100, 101, 101, 1), (100, 99, 101, 0), (100, 99, 101, 2.5),
                     (100, 99, 0, 1), ("abc", 99, 101, 1)):
            with self.assertRaises(journal.JournalError, msg=args):
                journal.compute(*args)


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.csv = os.path.join(self.dir, "trader", "journal.csv")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def log(self, *extra):
        return run(["log", "--journal", self.csv] + list(extra))

    def rows(self):
        with open(self.csv, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def test_log_from_the_plan_writes_one_row_with_every_column(self):
        code, out, err = self.log("--from-plan", "--plan", PLAN, "--shares", "8", "--exit", "370.04",
                                  "--note", "ORB,\n took the 1R target")
        self.assertEqual(code, 0, err)
        self.assertIn("P&L $46.88 (+1.00R)", out)
        with open(self.csv, encoding="utf-8") as fh:
            self.assertEqual(fh.readline().strip(), ",".join(journal.FIELDS))
        self.assertEqual(self.rows(), [{
            "date": "2026-10-19", "ticker": "TSLA", "entry": "364.18", "stop": "358.32", "target": "370.04",
            "shares": "8", "exit_price": "370.04", "pnl_usd": "46.88", "pnl_r": "1.00",
            "note": "ORB, took the 1R target"}])

    def test_flags_win_over_the_plan_and_rows_append(self):
        self.log("--from-plan", "--plan", PLAN, "--shares", "8", "--exit", "370.04")
        code, out, _ = self.log("--from-plan", "--plan", PLAN, "--shares", "8", "--exit", "355",
                                "--date", "2026-10-20")
        self.assertEqual(code, 0)
        self.assertIn("worse than -1R", out)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[1]["date"], rows[1]["pnl_usd"], rows[1]["pnl_r"]), ("2026-10-20", "-73.44", "-1.57"))

    def test_missing_fields_and_bad_stop_are_refused_and_nothing_is_written(self):
        code, _, err = self.log("--shares", "8", "--exit", "370")
        self.assertEqual(code, 1)
        self.assertIn("missing --ticker", err)
        code, _, err = self.log("--ticker", "TSLA", "--entry", "100", "--stop", "101", "--shares", "1", "--exit", "99")
        self.assertEqual(code, 1)
        self.assertIn("no R without risk", err)
        self.assertFalse(os.path.exists(self.csv))

    def test_weekly_summary(self):
        trades = [("2026-10-19", "TSLA", "370.04", "first"),       # +1R, +46.88
                  ("2026-10-20", "TSLA", "358.32", "stopped"),     # -1R, -46.88
                  ("2026-10-21", "TSLA", "355.00", "gapped"),      # -1.57R, -73.44: the largest loss
                  ("2026-10-23", "TSLA", "375.90", "2R"),          # +2R, +93.76
                  ("2026-10-26", "TSLA", "358.32", "next week")]   # outside the week
        for day, sym, px, note in trades:
            code, _, err = self.log("--ticker", sym, "--entry", "364.18", "--stop", "358.32", "--target", "370.04",
                                    "--shares", "8", "--exit", px, "--date", day, "--note", note)
            self.assertEqual(code, 0, err)
        s = journal.summarize(journal.read_rows(self.csv), journal.parse_date("2026-10-22"))
        self.assertEqual((s["week_start"], s["week_end"]), ("2026-10-19", "2026-10-25"))
        self.assertEqual((s["trades"], s["wins"], s["win_rate_pct"]), (4, 2, 50.0))
        self.assertEqual(s["average_r"], 0.11)                       # (1 - 1 - 1.57 + 2) / 4 = 0.1075
        self.assertEqual(s["total_usd"], "20.32")
        self.assertEqual(s["largest_loss"], {"pnl_usd": "-73.44", "pnl_r": "-1.57", "ticker": "TSLA",
                                             "date": "2026-10-21"})
        code, out, _ = run(["week", "--journal", self.csv, "--date", "2026-10-25"])
        self.assertEqual(code, 0)
        self.assertIn("Trades        4", out)
        self.assertIn("Win rate      50.0% (2 of 4)", out)
        self.assertIn("Average R     +0.11R", out)
        self.assertIn("Largest loss  -$73.44 (-1.57R) TSLA on 2026-10-21", out)
        code, out, _ = run(["week", "--journal", self.csv, "--date", "2026-10-26", "--json"])
        s = json.loads(out)
        self.assertEqual((s["trades"], s["wins"], s["win_rate_pct"], s["average_r"]), (1, 0, 0.0, -1.0))

    def test_an_empty_week_and_a_week_with_no_losses(self):
        code, out, _ = run(["week", "--journal", self.csv, "--date", "2026-10-19"])
        self.assertEqual(code, 0)
        self.assertIn("No trades logged this week.", out)
        self.log("--from-plan", "--plan", PLAN, "--shares", "8", "--exit", "370.04")
        code, out, _ = run(["week", "--journal", self.csv, "--date", "2026-10-19"])
        self.assertIn("Largest loss  none: no losing trades", out)

    def test_the_skill_example_matches(self):
        self.log("--from-plan", "--plan", PLAN, "--shares", "8", "--exit", "370.04")
        self.log("--from-plan", "--plan", PLAN, "--shares", "8", "--exit", "358.32", "--date", "2026-10-20")
        _, out, _ = run(["week", "--journal", self.csv, "--date", "2026-10-19"])
        self.assertIn("Average R     +0.00R", out)
        self.assertIn("Total         $0.00", out)
        self.assertIn("Largest loss  -$46.88 (-1.00R) TSLA on 2026-10-20", out)


if __name__ == "__main__":
    unittest.main()
