---
name: trader-news
description: >-
  News Monitor for EGX. Tracks breaking news and company disclosures on every
  Egyptian stock you watch or hold: TradingView's headlines per stock
  (Reuters, including EGX disclosures, Zawya, results write-ups and London
  Stock Exchange regulatory filings) and Daily News Egypt's business feed,
  matched by company name. Shows only what is new since the last check,
  newest first, in Cairo time, with each stock's move today (15 minutes late),
  tags (earnings, disclosure, stake, deal, capital, meeting, regulator,
  macro) and a "!" on what matters most. Use when the user says "any news on
  COMI", "why is it moving", "check the news", "what's new on my stocks", or
  after /trader-scanner and before /trader-entry.
argument-hint: "[--tickers COMI ETEL] [--hours 48] [--all]"
---

# trader-news

Before a trade: is there a reason this chart is moving? After it: did
anything change? Headlines are leads, not facts.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                               # watchlist + plan + portfolio tickers
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers COMI ETEL HRHO
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers COMI --all --hours 72
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-local --no-moves
```

## Sources

| source | what |
| --- | --- |
| `news-headlines.tradingview.com` | headlines for EGX:<TICKER>: Reuters (EGX disclosures end in "- EGX"), Zawya, results write-ups, LSE "REG -" filings |
| `www.dailynewsegypt.com` | business RSS, matched to your stocks by company name |
| TradingView quotes | today's move against yesterday's close, 15 minutes late |

State in `trader/state/news.json`: each item shows once.

## Steps

1. Run `news.py` (or `--tickers ...`). Lead with anything marked "!": a
   disclosure, results, a stake change, a tender offer, a halt.
2. Say each stock's move today with its time and "15 minutes late".
3. If results are due soon (`/trader-analyst` shows the next report date),
   say that a day trade into a report carries that risk.

## Example

```
COMI   EGP 125.01 -1.95% vs yesterday's close 127.50 at 14:28 Cairo (15 min late)
! 2026-11-10 09:00 Cairo  COMI   lse   REG - Commercial Int Bank - CIB - Condensed Balance Sheet Consolidated(9M26)
! 2026-11-10 06:28 Cairo  COMI   reuters   Commercial International Bank Egypt Qtrly Consol Profit EGP 23.76 Bln
```

## What it never does

It never trades on a headline, never summarises a story it did not open, and
never calls a headline a reason to buy.

## Safety

- Not financial advice. Headlines are leads, not facts: open the story before acting.
- Prices are TradingView's free EGX feed, DELAYED 15 MINUTES. Say so with every price.
- If a website is blocked, name the exact host and stop. Do not retry in a loop.
- Paper trading by default; nothing in this pack places an order or asks for any login.
- This skill reads news only: the 5-minute warnings and every order ticket come from the trading skills.
- Daily News Egypt and Reuters write about the market: their wording is theirs, quote it as written.
- A stock's name can match a story about something else: check the story is about the company.
- Disclosures marked "- EGX" are public filings, read quickly: the trade or event came first.
