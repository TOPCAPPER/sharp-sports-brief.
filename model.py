"""
Market-consensus model.

What it does
------------
1. De-vigs each sportsbook's prices (strips the built-in margin) so every
   book gives a "no-margin" probability for each outcome.
2. Averages those probabilities across books, weighting sharp books
   (Pinnacle, Circa, exchanges) more heavily than recreational ones.
   The result is the model's "fair" probability.
3. For each outcome, finds the best price available anywhere and
   computes expected value (EV) = fair_prob * decimal_odds - 1.
4. Suggests a stake using fractional Kelly, capped.

What it is NOT
--------------
It does not predict games from team stats, injuries, or form. It assumes
the sharp-weighted market is the best available estimate of the truth and
looks for books whose prices disagree with it. If the whole market is
wrong, or a line is stale, the model is wrong too.
"""

from collections import Counter
import statistics

# Higher weight = treated as a better estimate of the true probability.
# Keys are The Odds API bookmaker keys. Anything not listed gets 1.0.
SHARP_WEIGHTS = {
    "pinnacle": 4.0,
    "circasports": 4.0,
    "betfair_ex_eu": 3.0,
    "betfair_ex_uk": 3.0,
    "matchbook": 2.5,
    "bookmaker": 2.5,
    "lowvig": 2.0,
    "smarkets": 2.0,
    "betonlineag": 1.5,
}
DEFAULT_WEIGHT = 1.0

MIN_EV = 0.02            # need at least +2% expected value to flag
MIN_BOOKS = 4            # need at least this many books quoting the market
SUSPECT_EV = 0.15        # EV above this is more likely a stale/bad line than a real edge
KELLY_FRACTION = 0.25    # quarter Kelly
MAX_STAKE_PCT = 2.0      # never suggest more than this % of bankroll


def american_to_decimal(a):
    return 1 + (a / 100 if a > 0 else 100 / -a)


def devig(probs):
    s = sum(probs)
    return [p / s for p in probs]


def confidence(spread, n_books):
    if n_books >= 8 and spread < 0.012:
        return "High"
    if n_books >= 5 and spread < 0.025:
        return "Medium"
    return "Low"


def analyze_market(book_prices, titles=None, min_books=MIN_BOOKS):
    """
    book_prices: {book_key: {outcome_name: american_price}}
    Returns one result dict per outcome, or None if there isn't enough data.
    """
    if not book_prices:
        return None
    titles = titles or {}

    # Only compare books that quote the same set of outcomes (e.g. a
    # soccer book with a Draw alongside one without would corrupt the de-vig).
    shapes = Counter(frozenset(p.keys()) for p in book_prices.values())
    modal, _ = shapes.most_common(1)[0]
    if len(modal) < 2:
        return None
    books = {b: p for b, p in book_prices.items() if frozenset(p.keys()) == modal}
    if len(books) < min_books:
        return None

    outcomes = sorted(modal)
    per_book = {}
    for b, p in books.items():
        raw = [1 / american_to_decimal(p[o]) for o in outcomes]
        per_book[b] = dict(zip(outcomes, devig(raw)))

    weights = {b: SHARP_WEIGHTS.get(b, DEFAULT_WEIGHT) for b in books}
    wsum = sum(weights.values())
    has_sharp = any(b in SHARP_WEIGHTS for b in books)

    results = []
    for o in outcomes:
        fair = sum(weights[b] * per_book[b][o] for b in books) / wsum
        spread = statistics.pstdev([per_book[b][o] for b in books])
        best_b = max(books, key=lambda b: american_to_decimal(books[b][o]))
        best_price = books[best_b][o]
        dec = american_to_decimal(best_price)
        ev = fair * dec - 1
        kelly = max(0.0, ev / (dec - 1)) * KELLY_FRACTION
        conf = confidence(spread, len(books))
        if ev > SUSPECT_EV:
            conf = "Verify"   # too good to be true — check the line is live
        results.append({
            "outcome": o,
            "fair": fair,
            "spread": spread,
            "best_book": titles.get(best_b, best_b),
            "best_price": best_price,
            "ev": ev,
            "stake_pct": min(kelly * 100, MAX_STAKE_PCT),
            "n_books": len(books),
            "has_sharp": has_sharp,
            "confidence": conf,
        })
    return results


def is_value(r):
    return (
        r["ev"] >= MIN_EV
        and r["n_books"] >= MIN_BOOKS
        and r["confidence"] in ("High", "Medium", "Verify")
    )
