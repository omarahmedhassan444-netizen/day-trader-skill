"""trader-news tests on EGX sources (fixtures fetched 23 Nov 2026):

tv-COMI.json, tv-EAST.json, tv-FWRY.json  TradingView headlines per ticker (first 20)
dne-business.xml                          Daily News Egypt business RSS
tradingview-COMI.json                     COMI's quote (last 125.01, previous close 127.50)
"""

import datetime as _dt
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

news = load("trader-news", "news")

NDIR = os.path.join(FIX, "trader-news")
NOW = "2026-11-23T00:30:00Z"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(argv)
    return code, out.getvalue(), err.getvalue()


def fetcher():
    return news.Fetcher(offline=NDIR)


class Parse(unittest.TestCase):
    def test_tradingview_items(self):
        items = news.items_from_tv(fetcher().headlines("COMI"), "COMI")
        self.assertEqual(len(items), 20)
        self.assertTrue(all(i["time"] for i in items))
        self.assertTrue(any("REG - Commercial Int Bank" in i["title"] for i in items))

    def test_dne_rss(self):
        items = news.parse_dne(fetcher().dne())
        self.assertGreater(len(items), 5)
        self.assertTrue(all(i["source"] == "dailynewsegypt" and i["time"] for i in items))

    def test_cairo_times(self):
        t = news.parse_time("2026-11-03 10:25")            # no offset: Cairo
        self.assertEqual(t, _dt.datetime(2026, 11, 3, 8, 25, tzinfo=_dt.timezone.utc))
        self.assertEqual(news.to_local(t), _dt.datetime(2026, 11, 3, 10, 25))


class Tags(unittest.TestCase):
    def test_disclosures_and_results_are_important(self):
        items = news.tag(news.items_from_tv(fetcher().headlines("COMI"), "COMI"), {"COMI": None})
        reg = [i for i in items if i["title"].startswith("REG -")][0]
        self.assertTrue(reg["important"])
        self.assertIn("disclosure", reg["tags"])
        profit = [i for i in items if "Consol Profit" in i["title"]][0]
        self.assertTrue(profit["important"])
        self.assertIn("earnings", profit["tags"])
        self.assertNotIn("macro", profit["tags"])           # "EGP 23.76 Bln" is not macro news

    def test_stake_disclosure(self):
        it = {"id": "x", "ticker": "EAST", "kind": "headline", "source": "reuters", "link": "", "time": None,
              "title": "Fidelity Investments FZC Acquires 5.20% Stake In Egypt's Eastern Company - EGX",
              "summary": "", "symbols": ["EAST"]}
        news.tag([it], {"EAST": None})
        self.assertTrue(it["important"])
        self.assertIn("stake", it["tags"])
        self.assertIn("disclosure", it["tags"])

    def test_name_mentions(self):
        pats = news.mention_patterns({"COMI": "Commercial International Bank - Egypt (CIB) S.A.E."})
        self.assertTrue(pats["COMI"].search("CIB raises its deposit rates"))
        self.assertTrue(pats["COMI"].search("Commercial International Bank posts profit"))
        self.assertEqual(news.short_name("Commercial International Bank - Egypt (CIB) S.A.E."),
                         "Commercial International Bank")


class Cli(unittest.TestCase):
    def test_offline_run(self):
        code, out, err = run(["--tickers", "COMI", "EAST", "FWRY", "--offline", NDIR, "--now", NOW,
                              "--hours", "720", "--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertIn("COMI   EGP 125.01 -1.95% vs yesterday's close 127.50", out)
        self.assertIn("(15 min late)", out)
        self.assertIn("Cairo", out)
        self.assertIn("! 2026-11-10 09:00 Cairo  COMI", out)
        self.assertNotIn("$", out)

    def test_seen_items_show_once(self):
        tmp = tempfile.mkdtemp()
        try:
            st = os.path.join(tmp, "news.json")
            args = ["--tickers", "COMI", "--offline", NDIR, "--now", NOW, "--hours", "720", "--state", st,
                    "--no-moves"]
            self.assertEqual(run(args)[0], 0)
            code, out, _ = run(args)
            self.assertEqual(code, 1)
            self.assertIn("Nothing new.", out)
        finally:
            shutil.rmtree(tmp)

    def test_no_tickers(self):
        tmp = tempfile.mkdtemp()
        try:
            code, _, err = run(["--portfolio", os.path.join(tmp, "p"), "--watchlist", os.path.join(tmp, "w"),
                                "--plan", os.path.join(tmp, "x")])
            self.assertEqual(code, 2)
            self.assertIn("no tickers", err)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
