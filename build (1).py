#!/usr/bin/env python3
"""
Sharp Sports Pick Brief — daily builder (model version).

  python build.py          live run, needs ODDS_API_KEY
  python build.py --demo   preview with synthetic odds, no key needed
"""

import os
import sys
import json
import html
from datetime import datetime, timezone
import urllib.request
import urllib.error

import model

API_KEY = os.environ.get("ODDS_API_KEY", "")
DEMO = "--demo" in sys.argv
ODDS_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/{sport}/events/{event_id}/odds"

SPORTS = [
    ("americanfootball_nfl", "NFL"),
    ("basketball_nba", "NBA"),
    ("icehockey_nhl", "NHL"),
    ("baseball_mlb", "MLB"),
    ("soccer_epl", "Premier League"),
    ("soccer_uefa_champs_league", "Champions League"),
    ("basketball_wnba", "WNBA"),
    ("americanfootball_ncaaf", "College Football"),
    ("mma_mixed_martial_arts", "MMA"),
]

_NFL = ["player_pass_yds", "player_pass_tds", "player_rush_yds",
        "player_reception_yds", "player_receptions", "player_anytime_td"]
_HOOPS = ["player_points", "player_rebounds", "player_assists", "player_threes",
          "player_points_rebounds_assists"]
_SOCCER = ["player_shots_on_target", "player_shots", "player_goal_scorer_anytime", "player_assists"]
PROP_MARKETS = {
    "americanfootball_nfl": _NFL,
    "americanfootball_ncaaf": _NFL,
    "basketball_nba": _HOOPS,
    "basketball_wnba": _HOOPS,
    "icehockey_nhl": ["player_points", "player_assists", "player_goals",
                      "player_shots_on_goal", "player_total_saves"],
    "baseball_mlb": ["batter_hits", "batter_total_bases", "batter_home_runs",
                     "batter_rbis", "pitcher_strikeouts"],
    "soccer_epl": _SOCCER,
    "soccer_uefa_champs_league": _SOCCER,
}
# If the API rejects the long market list for a sport (HTTP 422), the script retries
# with this shorter, safer list so you still get props.
PROP_FALLBACK = {
    "americanfootball_nfl": ["player_anytime_td", "player_pass_yds"],
    "americanfootball_ncaaf": ["player_anytime_td", "player_pass_yds"],
    "basketball_nba": ["player_points", "player_rebounds"],
    "basketball_wnba": ["player_points", "player_rebounds"],
    "icehockey_nhl": ["player_points", "player_shots_on_goal"],
    "baseball_mlb": ["batter_home_runs", "pitcher_strikeouts"],
    "soccer_epl": ["player_shots_on_target", "player_goal_scorer_anytime"],
    "soccer_uefa_champs_league": ["player_shots_on_target", "player_goal_scorer_anytime"],
}

# PLAN controls how much data is pulled (and how many API credits it costs).
#   "paid" = Pinnacle (eu) in moneylines AND spreads/totals, more books for props,
#            8 games with props, up to 6-8 prop types per sport. Roughly 100-150
#            credits per run, so it needs a paid Odds API plan (about $30/month).
#   "free" = lean settings meant to fit the free 500 credits/month (Pinnacle on
#            moneylines only, fewer props). Change this one word to switch.
PLAN = "paid"
if PLAN == "paid":
    ML_REGIONS = "us,eu"            # "eu" is where Pinnacle comes from
    LINE_REGIONS = "us,eu"
    PROP_REGIONS = "us,us2"         # more US books = more prop lines per game
    TOP_GAMES_FOR_PROPS = 8
    PROP_GAMES_PER_SPORT = 3
else:
    ML_REGIONS = "us,eu"
    LINE_REGIONS = "us"
    PROP_REGIONS = "us"
    TOP_GAMES_FOR_PROPS = 3
    PROP_GAMES_PER_SPORT = 2
    PROP_MARKETS = {k: v[:2] for k, v in PROP_FALLBACK.items()}
CREDIT_RESERVE = 25                 # stop optional pulls (props, lines) when credits run low
MIN_PROB = 0.55                     # favorites below this fair probability are skipped in the lists
# How many days of games to show, counting today. 1 = today only. NFL plays Thu/Sun/Mon,
# so on other days set this to 3 (or more) to see the upcoming NFL slate.
DAYS_AHEAD = 3
# Spreads and totals (over/under on points or goals), only pulled for sports with games
# in the window. Set False to turn off.
LINES_ENABLED = True
LINE_MARKETS = "spreads,totals"
MIN_BOOKS_PROPS = 3
TIMEZONE = "America/Chicago"

TX_LEGAL_NOTE = (
    "Traditional sportsbooks are not licensed in Texas. Platforms like Kalshi, "
    "Polymarket, Underdog, PrizePicks, Novig, and BettorEdge operate differently "
    "and may be available. Verify current legality in your state before using any "
    "betting product. Prices shown come from licensed US/EU books and may not be "
    "available to you."
)
MODEL_NOTE = (
    "How the model works: each book's margin is stripped out, then the books are "
    "averaged with sharp books (Pinnacle, Circa, exchanges) weighted more heavily. "
    "That gives a 'fair' probability. Edge = fair probability x best available price "
    "minus 1. Stake is quarter-Kelly, capped at 2% of bankroll. It does not predict "
    "games from stats or injuries; it finds prices that disagree with the market. "
    "Stale lines and market-wide errors will fool it. Nothing here is guaranteed."
)


# ---------------------------------------------------------------- API ---
CREDITS = None   # remaining API credits, learned from response headers
EMPTY_SPORTS = []  # sports with no games in the window (shown on the page)


def can_afford(cost):
    return CREDITS is None or CREDITS >= cost + CREDIT_RESERVE


def api_get(url):
    global CREDITS
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            remaining = resp.headers.get("x-requests-remaining")
            if remaining is not None:
                print(f"    (API credits remaining this month: {remaining})")
                try:
                    CREDITS = int(float(remaining))
                except ValueError:
                    pass
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.reason} ({url.split('?')[0]})", file=sys.stderr)
    except Exception as e:
        print(f"  error: {e} ({url.split('?')[0]})", file=sys.stderr)
    return None


def fetch_events(sport_key):
    if DEMO:
        import demo_data
        return demo_data.demo_moneylines(sport_key)
    if not API_KEY:
        return []
    url = ODDS_URL.format(sport=sport_key) + (
        f"?apiKey={API_KEY}&regions={ML_REGIONS}&markets=h2h&oddsFormat=american")
    return api_get(url) or []


def demo_lines(sport_key):
    import demo_data
    import random
    rng = random.Random("lines" + sport_key)
    out = []
    for e in demo_data.demo_moneylines(sport_key):
        bks = []
        for key, title in demo_data.BOOKS:
            sharp = key in ("pinnacle", "circasports")
            vig = 0.02 if sharp else 0.05
            tot = demo_data._quote([0.5, 0.5], vig, 0.01, rng, (0, 0.07) if key == "bovada" else None)
            spr = demo_data._quote([0.52, 0.48], vig, 0.01, rng)
            bks.append({"key": key, "title": title, "markets": [
                {"key": "totals", "outcomes": [
                    {"name": "Over", "point": 5.5, "price": tot[0]},
                    {"name": "Under", "point": 5.5, "price": tot[1]}]},
                {"key": "spreads", "outcomes": [
                    {"name": e["home_team"], "point": -1.5, "price": spr[0]},
                    {"name": e["away_team"], "point": 1.5, "price": spr[1]}]}]})
        out.append(dict(e, bookmakers=bks))
    return out


def fetch_lines(sport_key):
    if DEMO:
        return demo_lines(sport_key)
    if not API_KEY:
        return []
    cost = len(LINE_MARKETS.split(",")) * len(LINE_REGIONS.split(","))
    if not can_afford(cost):
        print(f"  skipping spreads/totals: only {CREDITS} credits left")
        return []
    url = ODDS_URL.format(sport=sport_key) + (
        f"?apiKey={API_KEY}&regions={LINE_REGIONS}&markets={LINE_MARKETS}&oddsFormat=american")
    return api_get(url) or []


def fetch_prop_data(game):
    if DEMO:
        import demo_data
        return demo_data.demo_props(game)
    markets = PROP_MARKETS.get(game["sport_key"])
    if not markets or not API_KEY:
        return None
    def call(mkts):
        return api_get(EVENT_ODDS_URL.format(sport=game["sport_key"], event_id=game["event_id"]) + (
            f"?apiKey={API_KEY}&regions={PROP_REGIONS}&markets={','.join(mkts)}&oddsFormat=american"))

    cost = len(markets) * len(PROP_REGIONS.split(","))
    if not can_afford(cost):
        print(f"  skipping props: only {CREDITS} credits left")
        return None
    data = call(markets)
    fallback = PROP_FALLBACK.get(game["sport_key"])
    if data is None and fallback and fallback != markets and can_afford(len(fallback) * len(PROP_REGIONS.split(","))):
        print("  retrying with the shorter prop list")
        data = call(fallback)
    return data


# ------------------------------------------------------------- MODEL ---
def pin_prob(book_prices, outcome):
    """Pinnacle's own no-margin probability for an outcome, or None if it isn't quoting."""
    p = book_prices.get("pinnacle")
    if not p or outcome not in p:
        return None
    names = sorted(p)
    raw = [1 / model.american_to_decimal(p[n]) for n in names]
    return dict(zip(names, model.devig(raw)))[outcome]


def analyze_game(event, label, sport_key):
    prices, titles = {}, {}
    for bk in event.get("bookmakers", []):
        titles[bk["key"]] = bk.get("title", bk["key"])
        for m in bk.get("markets", []):
            if m.get("key") == "h2h":
                prices[bk["key"]] = {o["name"]: o["price"] for o in m.get("outcomes", [])
                                     if o.get("price") is not None}
    res = model.analyze_market(prices, titles)
    if not res:
        return None, []
    game_base = {
        "sport": label, "sport_key": sport_key, "event_id": event.get("id"),
        "home": event.get("home_team"), "away": event.get("away_team"),
        "commence": event.get("commence_time"),
    }
    fav = max(res, key=lambda r: r["fair"])
    game = dict(game_base, favorite=fav["outcome"], fair=fav["fair"], conf=fav["confidence"],
                n_books=fav["n_books"], has_sharp=fav["has_sharp"],
                best_price=fav["best_price"], best_book=fav["best_book"], ev=fav["ev"],
                pin=pin_prob(prices, fav["outcome"]))
    values = []
    for r in res:
        if model.is_value(r):
            values.append(dict(game_base, kind="Moneyline",
                               pick=f"{r['outcome']} ML",
                               matchup=f"{game_base['away']} @ {game_base['home']}", **r))
    return game, values


def analyze_lines(event, label):
    """Spreads and totals. Returns one dict per side of the most common line."""
    titles = {}
    books = {"spreads": {}, "totals": {}}
    for bk in event.get("bookmakers", []):
        titles[bk["key"]] = bk.get("title", bk["key"])
        for m in bk.get("markets", []):
            if m.get("key") not in books:
                continue
            prices = {}
            for o in m.get("outcomes", []):
                if o.get("price") is None or o.get("point") is None:
                    continue
                pt = f"{o['point']:+g}" if m["key"] == "spreads" else f"{o['point']:g}"
                prices[f"{o['name']} {pt}"] = o["price"]
            if len(prices) == 2:
                books[m["key"]][bk["key"]] = prices
    base = {"sport": label, "matchup": f"{event.get('away_team')} @ {event.get('home_team')}",
            "commence": event.get("commence_time")}
    rows = []
    for key, kind in (("spreads", "Spread"), ("totals", "Total")):
        res = model.analyze_market(books[key], titles)
        for r in res or []:
            rows.append(dict(base, kind=kind, pick=r["outcome"], pin=pin_prob(books[key], r["outcome"]), **r))
    return rows


def analyze_props(data, game):
    """Returns (value_props, all_props, anytime_props)."""
    if not data:
        return [], [], []
    titles, groups, anytime = {}, {}, {}
    for bk in data.get("bookmakers", []):
        titles[bk["key"]] = bk.get("title", bk["key"])
        for m in bk.get("markets", []):
            for o in m.get("outcomes", []):
                if o.get("price") is None:
                    continue
                if o.get("description"):
                    key = (m["key"], o["description"], o.get("point"))
                    groups.setdefault(key, {}).setdefault(bk["key"], {})[o["name"]] = o["price"]
                else:
                    key = (m["key"], o.get("name", ""))
                    cur = anytime.get(key)
                    if cur is None or model.american_to_decimal(o["price"]) > model.american_to_decimal(cur[0]):
                        anytime[key] = (o["price"], titles[bk["key"]])

    matchup = f"{game['away']} @ {game['home']}"
    base = {"sport": game["sport"], "commence": game["commence"], "matchup": matchup}
    value, allp = [], []
    for (market, player, point), book_prices in groups.items():
        res = model.analyze_market(book_prices, titles, min_books=MIN_BOOKS_PROPS)
        if not res:
            continue
        for r in res:
            row = dict(base, kind="Prop", market=market, player=player,
                       pick=f"{player} {r['outcome']} {point} {fmt_market(market)}".replace(" None", ""),
                       **r)
            allp.append(row)
            if model.is_value(r):
                value.append(row)
    any_rows = []
    for (market, player), (price, book) in anytime.items():
        any_rows.append(dict(base, market=market, player=player, price=price, book=book,
                             prob=1 / model.american_to_decimal(price)))
    return value, allp, any_rows


# ----------------------------------------------------------- RENDER ---
def fmt_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        try:
            from zoneinfo import ZoneInfo
            return dt.astimezone(ZoneInfo(TIMEZONE)).strftime("%a %b %-d, %-I:%M %p CT")
        except Exception:
            return dt.strftime("%a %b %-d, %-I:%M %p UTC")
    except Exception:
        return iso_str or ""


def fmt_market(key):
    for pre in ("player_", "batter_", "pitcher_"):
        key = key.replace(pre, "")
    return key.replace("_", " ").title()


def fmt_price(p):
    return f"+{p}" if p > 0 else str(p)


def pct(x):
    return f"{x * 100:.1f}%"


def esc(s):
    return html.escape(str(s))


def table(headers, rows, empty):
    if not rows:
        return f'<div class="empty">{esc(empty)}</div>'
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(rows)
    return f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def pin_cell(p):
    return "&mdash;" if p is None else pct(p)


def conf_cell(c):
    return f'<td class="conf {c.lower()}">{c}</td>'


def build_html(games, values, props_all, anytime, lines):
    games.sort(key=lambda g: g["fair"], reverse=True)
    values.sort(key=lambda v: v["fair"], reverse=True)
    props_all.sort(key=lambda p: p["fair"], reverse=True)
    anytime.sort(key=lambda p: p["prob"], reverse=True)
    listed = [g for g in games if g["fair"] >= MIN_PROB]
    best = [g for g in listed if g["conf"] in ("High", "Medium")][:6]

    def game_row(g, i):
        return (f'<tr data-sport="{esc(g["sport"])}"><td class="rank">{i}</td><td class="sport">{esc(g["sport"])}</td>'
                f'<td>{esc(g["away"])} @ {esc(g["home"])}</td><td class="pick">{esc(g["favorite"])}</td>'
                f'<td class="num">{pct(g["fair"])}</td><td class="num">{pin_cell(g.get("pin"))}</td><td class="num">{fmt_price(g["best_price"])}</td>'
                f'{conf_cell(g["conf"])}<td class="time">{fmt_time(g["commence"])}</td></tr>')

    def value_row(v, i):
        return (f'<tr data-sport="{esc(v["sport"])}"><td class="rank">{i}</td><td class="sport">{esc(v["sport"])}</td>'
                f'<td class="pick">{esc(v["pick"])}<div class="sub">{esc(v["matchup"])}</div></td>'
                f'<td class="num">{fmt_price(v["best_price"])}<div class="sub">{esc(v["best_book"])}</div></td>'
                f'<td class="num">{pct(v["fair"])}</td><td class="num edge">+{v["ev"] * 100:.1f}%</td>'
                f'<td class="num">{v["stake_pct"]:.2f}%</td>{conf_cell(v["confidence"])}'
                f'<td class="time">{fmt_time(v["commence"])}</td></tr>')

    def prop_row(p, i):
        return (f'<tr data-sport="{esc(p["sport"])}"><td class="rank">{i}</td><td class="sport">{esc(p["sport"])}</td>'
                f'<td>{esc(p["matchup"])}</td><td class="pick">{esc(p["pick"])}</td>'
                f'<td class="num">{pct(p["fair"])}</td><td class="num">{fmt_price(p["best_price"])}'
                f'<div class="sub">{esc(p["best_book"])}</div></td>'
                f'<td class="num">{p["ev"] * 100:+.1f}%</td>{conf_cell(p["confidence"])}'
                f'<td class="time">{fmt_time(p.get("commence"))}</td></tr>')

    def line_row(p, i):
        return (f'<tr data-sport="{esc(p["sport"])}"><td class="rank">{i}</td><td class="sport">{esc(p["sport"])}</td>'
                f'<td>{esc(p["matchup"])}</td><td class="pick">{esc(p["pick"])}</td>'
                f'<td class="num">{pct(p["fair"])}</td><td class="num">{pin_cell(p.get("pin"))}</td>'
                f'<td class="num">{fmt_price(p["best_price"])}<div class="sub">{esc(p["best_book"])}</div></td>'
                f'<td class="num">{p["ev"] * 100:+.1f}%</td>{conf_cell(p["confidence"])}'
                f'<td class="time">{fmt_time(p.get("commence"))}</td></tr>')

    def line_rows(kind):
        rows = sorted([x for x in lines if x["kind"] == kind], key=lambda x: x["fair"], reverse=True)
        return [line_row(x, i + 1) for i, x in enumerate(rows)], len(rows)

    def any_row(p, i):
        return (f'<tr data-sport="{esc(p["sport"])}"><td class="rank">{i}</td><td class="sport">{esc(p["sport"])}</td>'
                f'<td>{esc(p["matchup"])}</td><td class="pick">{esc(p["player"])} {esc(fmt_market(p["market"]))}</td>'
                f'<td class="num">{pct(p["prob"])}*</td><td class="num">{fmt_price(p["price"])}'
                f'<div class="sub">{esc(p["book"])}</div></td></tr>')

    g_head = ["#", "Sport", "Matchup", "Favorite", "Fair %", "Pinnacle %", "Best odds", "Confidence", "Start"]
    v_head = ["#", "Sport", "Pick", "Best price", "Fair %", "Edge", "Stake", "Confidence", "Start"]
    p_head = ["#", "Sport", "Matchup", "Prop", "Fair %", "Best price", "Edge", "Confidence", "Start"]
    l_head = ["#", "Sport", "Matchup", "Pick", "Fair %", "Pinnacle %", "Best price", "Edge", "Confidence", "Start"]
    spread_rows, n_spreads = line_rows("Spread")
    total_rows, n_totals = line_rows("Total")
    a_head = ["#", "Sport", "Matchup", "Prop", "Implied %", "Best price"]

    tabs = {
        "value": table(v_head, [value_row(v, i + 1) for i, v in enumerate(values)],
                       "No positive-edge bets found right now. That is normal: most days the market is efficient."),
        "best": table(g_head, [game_row(g, i + 1) for i, g in enumerate(best)],
                      "No high-confidence favorites found right now."),
        "full": table(g_head, [game_row(g, i + 1) for i, g in enumerate(listed)],
                      "No qualifying games found right now."),
        "spreads": table(l_head, spread_rows, "No spread lines found for today's games."),
        "totals": table(l_head, total_rows, "No over/under totals found for today's games."),
        "props": (table(p_head, [prop_row(p, i + 1) for i, p in enumerate(props_all)],
                        "No over/under props were pulled yet.")
                  + ('<div class="count" style="margin-top:18px">Anytime props (no opposing side to de-vig, so '
                     'no edge is calculated; *implied % still includes the book margin)</div>'
                     + table(a_head, [any_row(p, i + 1) for i, p in enumerate(anytime[:25])], "")
                     if anytime else "")),
    }
    counts = {
        "value": f"{len(values)} positive-edge bets vs. the sharp-weighted fair price, most likely to hit at the top",
        "best": "Strongest favorites with tight agreement between books, most likely to win at the top",
        "full": f"{len(listed)} games ranked by fair (de-vigged) win probability",
        "spreads": f"{n_spreads} spreads: the favored side of each game's main line, most likely to cover at the top",
        "totals": f"{n_totals} totals: the likelier side (over or under) of each game's main line, most likely at the top",
        "props": f"{len(props_all)} over/under props from today's top games, most likely to hit at the top",
    }
    panels = "".join(
        f'<div id="{k}" class="panel{" active" if k == "value" else ""}">'
        f'<div class="count">{counts[k]}</div>{tabs[k]}</div>' for k in tabs)

    sports_present = sorted({x["sport"] for x in games + values + props_all + anytime + lines})
    chips = '<button class="chip active" onclick="filterSport(\'All\', event)">All sports</button>' + "".join(
        f'<button class="chip" onclick="filterSport(\'{esc(sp)}\', event)">{esc(sp)}</button>' for sp in sports_present)
    updated = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y, %-I:%M %p UTC")
    updated += " | Showing games: " + ("today" if DAYS_AHEAD == 1 else f"today + next {DAYS_AHEAD - 1} days")
    empty = (f'<div class="count">No games in this window for: {esc(", ".join(EMPTY_SPORTS))}</div>'
             if EMPTY_SPORTS else "")
    demo_banner = '<div class="demo">DEMO DATA: synthetic odds for previewing the layout only.</div>' if DEMO else ""
    return (TEMPLATE.replace("%%UPDATED%%", updated).replace("%%PANELS%%", panels)
            .replace("%%TX%%", esc(TX_LEGAL_NOTE)).replace("%%MODEL%%", esc(MODEL_NOTE))
            .replace("%%DEMO%%", demo_banner).replace("%%CHIPS%%", chips).replace("%%EMPTY%%", empty))


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Sharp Sports Pick Brief</title>
<style>
  :root { --bg:#11151a; --panel:#171c22; --line:#262d35; --text:#e7ebee; --dim:#8b96a1;
          --accent:#e0a44b; --accent-dim:#8a6a33; --good:#5cc08a; --warn:#d9a441; --bad:#d9644f;
          --mono:'IBM Plex Mono','Courier New',monospace;
          --sans:'IBM Plex Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          box-sizing:border-box; padding-top:env(safe-area-inset-top,0px); padding-bottom:env(safe-area-inset-bottom,0px); }
  @media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) {
    --bg:#f4f2ee; --panel:#fff; --line:#ddd7cc; --text:#1b1d1f; --dim:#6b6f73;
    --accent:#a9681c; --accent-dim:#d8b073; --good:#2f8a5b; --warn:#a9781c; --bad:#b4442f; } }
  :root[data-theme="light"] { --bg:#f4f2ee; --panel:#fff; --line:#ddd7cc; --text:#1b1d1f; --dim:#6b6f73;
    --accent:#a9681c; --accent-dim:#d8b073; --good:#2f8a5b; --warn:#a9781c; --bad:#b4442f; }
  * { box-sizing:border-box; }
  html, body { margin:0; background:var(--bg); color:var(--text); font-family:var(--sans); }
  .wrap { max-width:1060px; margin:0 auto; padding:28px 18px 60px; }
  header { border-bottom:1px solid var(--line); padding-bottom:20px; margin-bottom:24px; }
  h1 { font-size:1.5rem; margin:0 0 6px; letter-spacing:-0.01em; }
  .updated { color:var(--dim); font-family:var(--mono); font-size:.82rem; }
  .demo { background:var(--accent-dim); color:var(--text); padding:8px 12px; border-radius:6px; font-size:.8rem; margin-bottom:16px; }
  .tabs { display:flex; gap:2px; margin-bottom:18px; background:var(--line); border-radius:8px; padding:3px; width:fit-content; flex-wrap:wrap; }
  .tab-btn { border:none; background:transparent; color:var(--dim); font-family:var(--sans); font-size:.9rem; padding:8px 16px; border-radius:6px; cursor:pointer; }
  .tab-btn.active { background:var(--panel); color:var(--text); }
  .panel { display:none; } .panel.active { display:block; }
  .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px; }
  .chip { border:1px solid var(--line); background:transparent; color:var(--dim); font-family:var(--sans); font-size:.8rem; padding:6px 12px; border-radius:999px; cursor:pointer; }
  .chip.active { background:var(--accent); border-color:var(--accent); color:#11151a; font-weight:600; }
  .count { color:var(--dim); font-size:.78rem; margin-bottom:10px; }
  .tablewrap { overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--panel); }
  table { width:100%; border-collapse:collapse; min-width:720px; }
  thead th { text-align:left; font-size:.72rem; color:var(--dim); font-weight:500; padding:10px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
  tbody td { padding:11px 12px; border-bottom:1px solid var(--line); font-size:.88rem; vertical-align:top; }
  tbody tr:last-child td { border-bottom:none; }
  td.rank { color:var(--dim); font-family:var(--mono); width:28px; }
  td.sport { color:var(--dim); font-size:.78rem; white-space:nowrap; }
  td.pick { color:var(--accent); font-weight:600; }
  td.num { font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }
  td.edge { color:var(--good); font-weight:600; }
  td.time { color:var(--dim); font-size:.78rem; white-space:nowrap; font-family:var(--mono); }
  td.conf { font-size:.8rem; white-space:nowrap; }
  td.conf.high { color:var(--good); } td.conf.medium { color:var(--warn); }
  td.conf.low { color:var(--dim); } td.conf.verify { color:var(--bad); }
  .sub { color:var(--dim); font-size:.74rem; font-weight:400; font-family:var(--sans); }
  .empty { color:var(--dim); padding:30px 12px; font-size:.85rem; border:1px dashed var(--line); border-radius:10px; text-align:center; }
  .note { margin-top:18px; font-size:.8rem; color:var(--dim); line-height:1.5; border-left:2px solid var(--accent-dim); padding-left:12px; }
</style>
</head>
<body>
  <div class="wrap">
    <header><h1>Sharp Sports Pick Brief</h1><div class="updated">Last updated %%UPDATED%%</div></header>
    %%DEMO%%
    <div class="tabs">
      <button class="tab-btn active" onclick="showTab('value', event)">Model Value Bets</button>
      <button class="tab-btn" onclick="showTab('best', event)">Best Bets</button>
      <button class="tab-btn" onclick="showTab('full', event)">Full Ranked List</button>
      <button class="tab-btn" onclick="showTab('spreads', event)">Spreads</button>
      <button class="tab-btn" onclick="showTab('totals', event)">Totals</button>
      <button class="tab-btn" onclick="showTab('props', event)">Player Props</button>
    </div>
    <div class="chips">%%CHIPS%%</div>
    %%EMPTY%%
    %%PANELS%%
    <div class="note">%%MODEL%%</div>
    <div class="note">%%TX%%</div>
  </div>
  <script>
    function showTab(id, evt) {
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      evt.target.classList.add('active');
    }
    function filterSport(sport, evt) {
      document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      evt.target.classList.add('active');
      document.querySelectorAll('tbody tr[data-sport]').forEach(r => {
        r.style.display = (sport === 'All' || r.dataset.sport === sport) ? '' : 'none';
      });
      document.querySelectorAll('tbody').forEach(tb => {
        let n = 0;
        tb.querySelectorAll('tr').forEach(r => {
          if (r.style.display !== 'none') { n += 1; const c = r.querySelector('td.rank'); if (c) c.textContent = n; }
        });
      });
    }
  </script>
</body>
</html>
"""


# ------------------------------------------------------------- MAIN ---
TODAY_ONLY = True  # limit games to the DAYS_AHEAD window (Central time)


def is_today(iso_str):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(TIMEZONE)
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(tz)
        delta = (dt.date() - datetime.now(tz).date()).days
        return 0 <= delta < DAYS_AHEAD
    except Exception:
        return True


def main():
    if not API_KEY and not DEMO:
        print("WARNING: ODDS_API_KEY is not set. Writing an empty page. "
              "(Try: python build.py --demo)", file=sys.stderr)

    games, values, lines = [], [], []
    for sport_key, label in SPORTS:
        print(f"Fetching {label} moneylines...")
        n = 0
        for ev in fetch_events(sport_key):
            if TODAY_ONLY and not DEMO and not is_today(ev.get("commence_time")):
                continue
            g, v = analyze_game(ev, label, sport_key)
            if g:
                games.append(g)
                values.extend(v)
                n += 1
        print(f"  -> {n} games modeled")
        if not n:
            EMPTY_SPORTS.append(label)
        if LINES_ENABLED and n:   # only spend credits on sports with games today
            kept = 0
            for ev in fetch_lines(sport_key):
                if TODAY_ONLY and not DEMO and not is_today(ev.get("commence_time")):
                    continue
                rows = analyze_lines(ev, label)
                for kind in ("Spread", "Total"):
                    side = [r for r in rows if r["kind"] == kind]
                    if side:
                        lines.append(max(side, key=lambda r: r["fair"]))
                        kept += 1
                    for r in side:
                        if model.is_value(r):
                            values.append(dict(r, pick=f"{r['pick']} {kind}"))
            print(f"  -> {kept} spread/total lines modeled")

    games.sort(key=lambda g: g["fair"], reverse=True)
    props_all, anytime = [], []
    chosen, per_sport = [], {}
    for g in games:   # already sorted most likely first
        if g["sport_key"] not in PROP_MARKETS and not DEMO:
            continue
        if per_sport.get(g["sport_key"], 0) >= PROP_GAMES_PER_SPORT:
            continue
        chosen.append(g)
        per_sport[g["sport_key"]] = per_sport.get(g["sport_key"], 0) + 1
        if len(chosen) >= TOP_GAMES_FOR_PROPS:
            break
    for g in chosen:
        print(f"Fetching props for {g['away']} @ {g['home']}...")
        v, a, any_rows = analyze_props(fetch_prop_data(g), g)
        values.extend(v)
        props_all.extend(a)
        anytime.extend(any_rows)
        print(f"  -> {len(a)} over/under props, {len(v)} flagged")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(games, values, props_all, anytime, lines))
    print(f"Wrote index.html: {len(games)} games, {len(lines)} lines, {len(values)} value bets, {len(props_all)} props.")


if __name__ == "__main__":
    main()
