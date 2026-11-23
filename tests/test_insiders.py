"""trader-insiders tests: EGX insider and major-shareholder disclosures.

tv-EG.json                   TradingView's Egyptian market headlines (EGX-tagged items, 23 Nov 2026)
tv-COMI/EAST/FWRY/SWDY.json  each ticker's own headlines
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

ins = load("trader-insiders", "insiders")
IDIR = os.path.join(FIX, "trader-insiders")
NOW = "2026-11-23 02:30"
ALL = ["--tickers", "COMI", "EAST", "FWRY", "SWDY", "--offline", IDIR, "--now", NOW]


def row(title, sym):
    return ins.classify({"title": title, "symbols": ["EGX:" + sym], "published": 1, "provider": "reuters"})


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ins.main(argv)
    return code, out.getvalue(), err.getvalue()


class Classify(unittest.TestCase):
    def test_stake_up(self):
        r = row("Fidelity Investments FZC Acquires 5.20% Stake In Egypt's Eastern Company - EGX", "EAST")
        self.assertEqual((r["kind"], r["holder"], r["pct"], r["direction"]), ("stake", "Fidelity Investments FZC",
                                                                               5.2, "up"))

    def test_stake_down(self):
        r = row("Egyptian American Enterprisse Fund Reduces Stake In Fawry To 4.427% - EGX", "FWRY")
        self.assertEqual((r["kind"], r["pct"], r["direction"]), ("stake", 4.427, "down"))

    def test_sale_with_value(self):
        r = row("Alpha Oryx Ltd Sells 73.5 Mln Shares In Commercial International Bank Egypt For EGP 7.09 Bln- EGX",
                "COMI")
        self.assertEqual((r["kind"], r["holder"], r["shares"], r["value_egp"]),
                         ("sale", "Alpha Oryx Ltd", 73500000.0, 7090000000.0))

    def test_block_trade(self):
        r = row("Eastern Company's 156.1 Mln Shares Block Traded For EGP 6.24 Bln - EGX", "EAST")
        self.assertEqual((r["kind"], r["shares"], r["value_egp"]), ("block", 156100000.0, 6240000000.0))

    def test_tender_status(self):
        r = row("Sagasse Investment Says Egypt's FRA Rejects Mandatory Tender Offer For Elsewedy Electric", "SWDY")
        self.assertEqual((r["kind"], r["status"]), ("tender", "rejected"))
        r = row("Sagasse Investment Plans Mandatory Tender Offer For Elsewedy Electric", "SWDY")
        self.assertEqual(r["status"], "proposed")

    def test_treasury(self):
        r = row("Ajwa Food Industries Board Approves Orouba Food Industry Decision To Buy Co's Treasury Shares",
                "AJWA")
        self.assertEqual(r["kind"], "treasury")

    def test_not_insider_trades(self):
        self.assertIsNone(row("El Sewedy Electric Acquires 60% Stake In Thomassen Service", "SWDY"))   # a deal abroad
        self.assertIsNone(row("Fawry Q3 Consol Profit EGP 1.07 Billion", "FWRY"))
        self.assertIsNone(ins.classify({"title": "X Reduces Stake In Y To 4% - EGX", "symbols": ["ADX:Y"]}))


class Run(unittest.TestCase):
    def test_last_30_days(self):
        code, out, err = run(ALL + ["--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertIn("AJWA   TREASURY", out)
        self.assertIn("MOIL   TENDER   tender offer for MOIL (proposed)", out)
        self.assertNotIn("FWRY", out)                      # January: outside 30 days

    def test_a_year_and_more(self):
        code, out, _ = run(ALL + ["--days", "500", "--dry-run", "--json"])
        rows = json.loads(out)["rows"]
        kinds = {r["kind"] for r in rows}
        self.assertTrue({"stake", "sale", "block", "tender", "treasury"} <= kinds)
        fid = [r for r in rows if (r["holder"] or "").startswith("Fidelity")]
        self.assertEqual(len(fid), 1)

    def test_holder_filter_and_yours_flag(self):
        code, out, _ = run(ALL + ["--days", "500", "--dry-run", "--holder", "fidelity"])
        self.assertEqual(code, 0)
        self.assertIn("YOURS ", out)                       # EAST is one of --tickers
        self.assertIn("Fidelity Investments FZC raised its stake in EAST to 5.2%", out)
        self.assertEqual(out.count("STAKE"), 1)

    def test_seen_once(self):
        tmp = tempfile.mkdtemp()
        try:
            st = os.path.join(tmp, "state.json")
            self.assertEqual(run(ALL + ["--state", st])[0], 0)
            code, out, _ = run(ALL + ["--state", st])
            self.assertEqual(code, 1)
            self.assertIn("Nothing new.", out)
        finally:
            shutil.rmtree(tmp)

    def test_bad_kind(self):
        code, _, err = run(ALL + ["--kind", "options"])
        self.assertEqual(code, 1)
        self.assertIn("unknown kind", err)


if __name__ == "__main__":
    unittest.main()
