---
name: trader-insiders
description: >-
  Insider and Major-Shareholder Tracker for EGX (the EGX version of the
  Pelosi tracker). Reads the trades big holders and insiders must disclose to
  the Egyptian Exchange: stake changes ("raises its stake to 5.2 %"), holders
  selling or buying blocks of shares, block trades, mandatory and voluntary
  tender offers (with the FRA's decision) and treasury-share buybacks. Turns
  each headline into a row (who, what, how much, which stock, when), shows
  every new one once, and flags the ones in stocks you hold or watch. Use
  when the user says "who is buying COMI", "insider trades", "big holders",
  "stake changes", "tender offers", "track Fidelity", "what are the big funds
  doing on EGX", or "run the Pelosi skill".
argument-hint: "[--tickers COMI EAST] [--holder NAME] [--kind stake,sale] [--days 30]"
---

# trader-insiders

Egypt has no Congress-style trade filings, but EGX makes big holders and
insiders disclose their trades, and Reuters publishes them as short
headlines ending in "- EGX". This skill reads those. It is fast, not early:
the trade came first. These are public filings, not inside information.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py"                            # market feed + your tickers, last 30 days
python3 "${CLAUDE_SKILL_DIR}/insiders.py" --tickers COMI EAST FWRY
python3 "${CLAUDE_SKILL_DIR}/insiders.py" --holder fidelity --days 365 --all
python3 "${CLAUDE_SKILL_DIR}/insiders.py" --kind stake,sale --json
```

## Kinds

| kind | example headline |
| --- | --- |
| stake | Fidelity Investments FZC Acquires 5.20% Stake In Egypt's Eastern Company - EGX |
| sale | Alpha Oryx Ltd Sells 73.5 Mln Shares In Commercial International Bank Egypt For EGP 7.09 Bln- EGX |
| block | Eastern Company's 156.1 Mln Shares Block Traded For EGP 6.24 Bln - EGX |
| tender | Sagasse Investment Says Egypt's FRA Rejects Mandatory Tender Offer For Elsewedy Electric |
| treasury | Ajwa Food Industries Board Approves ... Decision To Buy Co's Treasury Shares |

Counted only when the headline says it was disclosed to EGX or the FRA, or
when it is a tender offer or a treasury buy. A company buying another
company abroad is a deal, left to `/trader-news`.

## Steps

1. Run `insiders.py`. Lead with rows marked YOURS.
2. Quote each row's headline as written, with its date: say how old it is.
3. A stake change or a block sale is information about who holds the stock,
   not a signal to copy. Say that.

## Example

```
YOURS 2026-01-22 14:57 Cairo  FWRY   STAKE    Egyptian American Enterprisse Fund cut its stake in FWRY to 4.43%
2026-11-17 11:05 Cairo  MOIL   TENDER   tender offer for MOIL (proposed)
```

## What it never does

It never calls a disclosure a reason to buy or sell, never guesses a missing
number, and never presents a filing as early news.

## Safety

- Not financial advice. Public disclosures, read quickly: the trade came first.
- Paper trading by default (`"paper": true`); nothing in this pack places an order, and a "yes" to a
  ticket is only ever asked by /trader-broker, one ticket at a time.
- Never ask for logins, PINs, card or account details.
- If a website is blocked, name the exact host and stop. Do not retry in a loop.
- Copying a big holder is not a strategy: they may hold for years and sell for reasons of their own.
- Quote the headline as written; open the disclosure before relying on a number.
