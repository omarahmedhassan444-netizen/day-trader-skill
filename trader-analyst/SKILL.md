---
name: trader-analyst
description: >-
  Earnings Analyst for EGX. Reads an Egyptian company's reported numbers and
  sums up what changed, quarter by quarter: revenue (total income for banks),
  gross, operating and net margins, net income, diluted EPS, growth against
  the same quarter a year earlier, the trailing twelve months, P/E, dividend
  yield, the last and the next report date, and how the last report compared
  with the analysts' estimate. Amounts in EGP, from TradingView's
  fundamentals. Use when the user says "how did COMI's earnings look", "read
  the results", "is revenue growing", "when do they report", "earnings
  analyst", "compare COMI and ETEL", or before trading into a report.
argument-hint: "[TICKER ...] [--quarters 8] [--metrics FILE]"
---

# trader-analyst

Numbers, not opinions. What the company reported, how it changed, and when
the next report lands (a day trade into a report is a different trade).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" COMI
python3 "${CLAUDE_SKILL_DIR}/facts.py" COMI ETEL --quarters 8
python3 "${CLAUDE_SKILL_DIR}/facts.py" COMI ETEL TMGH --metrics trader/metrics.json
python3 "${CLAUDE_SKILL_DIR}/facts.py" COMI --json
```

## Source

TradingView's quote session for EGX:<TICKER> (one connection for all the
tickers), through `trader-egx`: quarterly `*_fq_h` series (32 quarters),
TTM figures, estimates and report dates. Each quarter is named from its own
end date. Banks have no gross profit, so their gross margin is blank.
The numbers come from the companies' filings as TradingView's data provider
collects them: check the company's disclosure on egx.com.eg before relying
on one.

## Steps

1. Run `facts.py TICKER`. Read back the newest quarter in two or three
   sentences: revenue and its growth, net income and margin, EPS, and the
   estimate comparison when there is one.
2. Always say the next report date.
3. For a table across stocks, use `--metrics trader/metrics.json`.

## Example

```
COMI  Commercial International Bank - Egypt (CIB) S.A.E.  (Finance, Regional Banks; amounts in EGP)
  2026-Q3  2026-09-30      71.50bn   +24.3%      23.76bn   -12.0%    33.2%    45.5%     6.92    -13.2%
  Revenue EGP 71.50 bn in 2026-Q3, +24.3% on a year earlier.
  Against the analysts' estimate: EPS 6.23 vs 5.35 expected (+16.5%).
  Next report expected around 2027-02-24: a day trade held into it carries that risk.
```

## What it never does

It never says whether a stock is a buy, never rounds a loss into a profit,
and never fills a blank with a guess.

## Safety

- Not financial advice. Numbers only.
- Paper trading by default; nothing in this pack places an order or asks for any login.
- This skill reads reports only: the 5-minute warnings and every order ticket come from the trading skills.
- If a website is blocked, name the exact host and stop. Do not retry in a loop.
- A figure that looks wrong usually is: check it against the company's own disclosure.
- One quarter is not a trend: read at least four.
- Growth from a loss to a profit is shown, but read it with care.
