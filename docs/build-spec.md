# Momentum Dashboard — build spec (handoff to Claude Code)

Decided in chat on 15 Nov 2026. This doc is the brief for building it in Claude Code. It extends the older `momentum-watch-daily-gainers.md`, which only looked at the top 3 daily gainers, into a scored tracker for the whole universe.

## Goal

Each week, score a fixed universe of Indian stocks for momentum size and for how well fundamentals back that momentum. Surface stocks that show early signs of a sustained 1–3 month move driven by things like re-rating, policy or regulatory tailwinds, a business plan starting to deliver, new markets or products, a loss-to-profit turnaround, partnerships, major client or order wins, a new segment, or debt reduction.

## Decisions

* Universe: Nifty Next 50 + Nifty Midcap 150, about 200 stocks. The two indices don't overlap: Next 50 is ranks 51–100, Midcap 150 is ranks 101–250. Pull the constituent lists from niftyindices.com so rebalances are picked up automatically.
* Price data: Yahoo Finance (`yfinance`, tickers with the `.NS` suffix). Fundamentals: screener.in.
* Cadence: a weekly deep run, Saturday morning IST, after the week's close.
* Output: a dashboard that sorts every stock into one of four groups, with both scores shown and the catalysts listed.

## Architecture: a three-layer funnel

1. **Price momentum, all stocks, scripted.** Gives a Momentum score (0–100). Inputs:
   * 1-month, 3-month and 6-month returns
   * relative strength against the Nifty 50 and its trend
   * price against the 50-day and 200-day moving averages, and the slope of each
   * distance from the 52-week high
   * 20-day average volume against the 90-day average
   * NSE delivery %, if it can be obtained
   * a higher-highs / higher-lows structure
   * Early-turn flags: a recent cross above the 50-day average on above-average volume, relative strength turning up from a base, a fresh 52-week high after a consolidation.
2. **Fundamental support, refreshed each results season.** Gives a Support score (0–100). Inputs:
   * revenue and profit growth year on year, and whether it's accelerating across 2–3 quarters
   * widening operating margin
   * a loss turning into a profit
   * falling debt-to-equity or net debt
   * ROCE trend
   * order book or book-to-bill, where relevant
   * changes in promoter, FII and DII holdings; changes in promoter pledges
   * upgrades to analyst estimates or price targets
3. **News and catalysts, only for stocks flagged in layers 1 and 2.** Use web search for the last 2–4 weeks. Tag each catalyst with its type (policy/regulation, new market/product, order/client win, partnership, turnaround, fund raise, debt cut, brokerage upgrade/re-rating) and its date. Always check the article's real publish date, because stale articles often resurface as if they were new.

## The four groups (momentum against support)

* 🟢 **Early signs**: early-turn momentum plus strong support plus a fresh catalyst. This is the main target.
* 🔵 **Supported trend**: strong momentum and strong support.
* 🟠 **Hype risk**: strong momentum, weak support. Watch for a reversal.
* ⚪ **Not moving yet**: strong support, flat price.

## Dashboard

* a summary row with the count of stocks in each group
* a sortable table of all stocks: ticker, index, both scores, group, 1-month and 3-month returns, early-turn flags
* a card for each flagged stock, with its catalysts (type, date, source) and the fundamental changes behind its score
* the change in each stock's score since last week

## Notes

* The cloud chat sandbox blocked Yahoo Finance and NSE. Running Claude Code locally avoids that block.
* Show this disclaimer on the dashboard: these scores are screening signals, not buy recommendations.
