# Momentum Dashboard

A weekly scored tracker for the Nifty Next 50 + Nifty Midcap 150 universe (~200 Indian
stocks), surfacing early signs of a sustained 1–3 month move backed by real fundamentals
and a fresh catalyst. Full spec: [docs/build-spec.md](docs/build-spec.md).

**These scores are screening signals, not buy recommendations.**

## Architecture (three-layer funnel)

1. **`momentum.price_momentum`** — scripted, runs for the whole universe every week.
   Returns, relative strength vs Nifty 50, moving-average position/slope, 52-week-high
   distance, volume trend, higher-highs/higher-lows structure, and early-turn flags.
2. **`momentum.fundamentals`** — scraped from screener.in, refreshed quarterly (cached;
   `pipeline.py` only re-pulls a stock once its cache is >75 days old). Revenue/profit
   growth trend, margin trend, loss→profit turnarounds, ROCE, debt, holding changes.
3. **Catalysts** — *not automated*. `momentum.catalysts` only builds the search queries
   and the storage format. Actually researching catalysts needs real web search with
   judgment on source credibility and publish dates — that's a job for Claude Code, run
   interactively after the weekly pipeline, not a cron script. See "Weekly run" below.

Momentum + Support scores combine (`momentum.scoring`) into four groups:

| Group | Condition |
|---|---|
| 🟢 Early signs | early-turn momentum + strong support |
| 🔵 Supported trend | strong momentum + strong support |
| 🟠 Hype risk | strong momentum, weak support |
| ⚪ Not moving yet | strong support, flat price |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Weekly run (Saturday morning IST, after close)

```bash
python -m momentum.pipeline
```

This fetches the universe from niftyindices.com, scores price momentum for every
stock, refreshes stale fundamentals from screener.in, classifies into groups, and
writes `data/runs/<date>/run.json` + `catalyst_queries.json` (search queries for every
flagged stock).

Then, in Claude Code, ask it to research catalysts for this run's flagged stocks —
it reads `catalyst_queries.json`, searches, and should call
`momentum.catalysts.save_catalysts(run_date, records)` with what it finds, tagged by
type and with the real publish date verified (stale articles resurface as if new).

Finally:

```bash
streamlit run dashboard/app.py
```

Shows the group summary counts, a sortable table of every scored stock, and a card
per flagged stock with its catalysts and the fundamental story behind its score.

## Smoke-testing without a full run

```bash
python -m momentum.pipeline --quick 20
```

Scores only the first 20 universe stocks — useful for checking the pipeline runs
end-to-end without waiting on ~200 yfinance downloads and ~200 screener.in scrapes.

## Known rough edges / next steps

- `momentum.fundamentals`'s screener.in scraper is a best-effort HTML parse against the
  current page structure — if screener.in changes its layout, `fetch_fundamentals()`
  will start returning `error` instead of silently zeroing scores, which is the point,
  but the parsing selectors will need a fix.
- The momentum-score weights (`price_momentum.py`) and support-score heuristic
  (`fundamentals.py`) are starting points, not calibrated against outcomes. Revisit
  after a few weeks of real runs.
- NSE delivery % (listed as a nice-to-have input in the spec) isn't wired up yet — NSE's
  bhavcopy/delivery data needs a separate download step.
- No scheduler is set up yet; running weekly is currently manual (`python -m
  momentum.pipeline` then the catalyst-research step in Claude Code).
