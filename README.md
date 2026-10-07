# Sharp Sports Pick Brief (model version)

A webpage that rebuilds itself every day, runs a market-consensus model on
the day's odds, and shows four tabs: **Model Value Bets**, **Best Bets**,
**Full Ranked List**, and **Player Props**. Free hosting via GitHub Pages,
free scheduling via GitHub Actions.

## Preview it first (no API key needed)

    python build.py --demo

Opens-ready `index.html` with synthetic odds so you can see the layout.

## How the model works

1. **De-vig.** Each sportsbook's prices include a built-in margin. The model
   strips it out per book, so each book gives a no-margin probability.
2. **Consensus.** It averages those probabilities across books, with sharp
   books (Pinnacle, Circa, Betfair, etc.) weighted 2-4x more than
   recreational books. Weights live in `SHARP_WEIGHTS` in `model.py`.
3. **Edge.** For each outcome it finds the best price available anywhere and
   computes `edge = fair probability x decimal odds - 1`.
4. **Stake.** Quarter-Kelly, capped at 2% of bankroll.
5. **Confidence.** High / Medium / Low from the number of books and how much
   they disagree. An edge above 15% is labeled **Verify**, because that is
   usually a stale or wrong line, not a real opportunity.

### What it is not

It does not predict games from team stats, injuries, or form. It trusts the
sharp-weighted market and looks for prices that disagree with it. If the
whole market is wrong, or a line is stale, the model is wrong too. Edges on
big underdogs and long shots are especially sensitive to small errors in the
fair probability. Most days the honest result is "no edge found," and the
page says so.

Anytime props (anytime goalscorer, anytime TD) have no opposing side to
de-vig, so they are listed with implied probability but no edge calculation.

## Setup

1. **API key:** sign up free at https://the-odds-api.com and copy the key.
2. **GitHub repo:** create a public repo and upload every file, keeping the
   folder structure (`.github/workflows/daily.yml` must stay at that path).
3. **Secret:** repo Settings > Secrets and variables > Actions > New
   repository secret. Name `ODDS_API_KEY`, value = your key.
4. **Pages:** Settings > Pages > Source: **GitHub Actions**.
5. **Run:** Actions tab > Daily Sports Brief > Run workflow. Your live URL
   appears under Settings > Pages. It then rebuilds daily (13:00 UTC).

## API quota (read this)

The free tier is 500 credits/month. Costs here:

- Moneylines: 1 credit per sport per region. The model wants Pinnacle, which
  needs the `eu` region, so `ML_REGIONS = "us,eu"` costs 2 per sport per day.
  With 7 sports that is up to 14/day (~420/month) if all are in season.
- Props: about 2 credits per game, for the top `TOP_GAMES_FOR_PROPS` (3).

That can exceed 500/month. The script prints your remaining credits on every
call (see the Actions log). Ways to stay inside the free tier: trim `SPORTS`
to what's in season, drop `eu` from `ML_REGIONS` (cheaper, but the model
loses Pinnacle and gets noticeably less sharp), lower
`TOP_GAMES_FOR_PROPS`, or move to a paid Odds API plan. `HTTP 401/429` errors
in the log mean you've run out for the month.

## Customizing

- Sports covered: `SPORTS` in `build.py`
- Prop markets: `PROP_MARKETS` in `build.py`
- Sharp-book weights, minimum edge, Kelly fraction, stake cap: top of `model.py`
- Time zone shown: `TIMEZONE` in `build.py`
- Run time: the `cron` line in `.github/workflows/daily.yml` (UTC)

## Notes

- Out-of-season sports simply return nothing.
- Sportsbooks are not licensed in Texas. Prices come from licensed US/EU
  books and may not be available to you. Verify legality where you are.
- Informational only. Nothing here is a guarantee, and +EV bets still lose
  often. Never stake money you can't afford to lose.
