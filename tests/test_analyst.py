"""trader-analyst tests on EGX fundamentals (TradingView quote fields, 22 Nov 2026):
quote-COMI.json (a bank: no gross profit) and quote-ETEL.json (Telecom Egypt)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

facts = load("trader-analyst", "facts")
ADIR = os.path.join(FIX, "trader-analyst")


def q(sym):
    return facts.load_offline(ADIR, sym)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(argv)
    return code, out.getvalue(), err.getvalue()


class Quarters(unittest.TestCase):
    def test_labels_come_from_the_end_dates(self):
        qs = facts.quarters(q("COMI"))
        self.assertEqual(len(qs), 32)
        self.assertEqual((qs[0]["end"], qs[0]["period"]), ("2026-09-30", "2026-Q3"))
        self.assertEqual((qs[3]["end"], qs[3]["period"]), ("2025-12-31", "2025-Q4"))

    def test_comi_numbers(self):
        r = facts.analyse(q("COMI"), 4)
        top = r["quarters"][0]
        self.assertEqual(top["revenue"], 71495064000.0)
        self.assertEqual(top["net_income"], 23763601000.0)
        self.assertEqual(top["revenue_yoy"], 24.3)           # vs 2025-Q3 57.54bn
        self.assertEqual(top["net_income_yoy"], -12.0)
        self.assertIsNone(top["gross_margin"])               # banks have no gross profit
        self.assertEqual((r["last_report"], r["next_report"]), ("2026-11-10", "2027-02-24"))
        self.assertEqual(r["eps_reported_vs_estimate"]["surprise_pct"], 16.5)

    def test_etel_margins(self):
        top = facts.analyse(q("ETEL"), 2)["quarters"][0]
        self.assertEqual(top["gross_margin"], 44.5)
        self.assertEqual(top["net_margin"], 26.3)

    def test_growth_math(self):
        self.assertEqual(facts.pct(110, 100), 10.0)
        self.assertEqual(facts.pct(-50, -100), 50.0)          # a smaller loss is growth
        self.assertIsNone(facts.pct(1, 0))


class Cli(unittest.TestCase):
    def test_report(self):
        code, out, err = run(["COMI", "ETEL", "--offline", ADIR])
        self.assertEqual(code, 0, err)
        self.assertIn("COMI  Commercial International Bank - Egypt (CIB) S.A.E.", out)
        self.assertIn("amounts in EGP", out)
        self.assertIn("Revenue EGP 71.50 bn in 2026-Q3, +24.3% on a year earlier.", out)
        self.assertIn("Next report expected around 2027-02-24", out)
        self.assertNotIn("$", out)

    def test_metrics_file(self):
        tmp = tempfile.mkdtemp()
        try:
            m = os.path.join(tmp, "metrics.json")
            code, out, err = run(["COMI", "ETEL", "--offline", ADIR, "--metrics", m, "--now", "2026-11-23"])
            self.assertEqual(code, 0, err)
            with open(m) as fh:
                doc = json.load(fh)
            self.assertEqual([c["ticker"] for c in doc["companies"]], ["COMI", "ETEL"])
            self.assertEqual(doc["companies"][0]["currency"], "EGP")
        finally:
            shutil.rmtree(tmp)

    def test_missing_ticker_is_reported(self):
        code, out, _ = run(["COMI", "NOPE", "--offline", ADIR])
        self.assertEqual(code, 1)
        self.assertIn("NOPE: no quote-NOPE.json", out)


if __name__ == "__main__":
    unittest.main()
