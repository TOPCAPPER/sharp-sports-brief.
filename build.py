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

SPORTS = [("basketball_wnba", "WNBA"),
    ("americanfootball_ncaaf", "College Football"),
    ("americanfootball_nfl", "NFL"),
    ("basketball_nba", "NBA"),
    ("icehockey_nhl", "NHL"),
    ("baseball_mlb", "MLB"),
    ("soccer_epl", "Premier League"),
    ("soccer_uefa_champs_league", "Champions League"),
    ("mma_mixed_martial_arts", "MMA"),
]

PROP_MARKETS = {"basketball_wnba": ["player_points", "player_rebounds"],
    "americanfootball_nfl": ["player_anytime_td", "player_pass_yds"],
    "basketball_nba": ["player_points", "player_rebounds"],
    "icehockey_nhl": ["player_points", "player_shots_on_goal"],
    "baseball_mlb": ["batter_home_runs", "pitcher_strikeouts"],
    "soccer_epl": ["player_shots_on_target", "player_goal_scorer_anytime"],
    "soccer_uefa_champs_league": ["player_shots_on_target", "player_goal_scorer_anytime"],
}

# "eu" brings Pinnacle into the data, which makes the model much sharper,
# but every extra region costs extra API credits. Props stay on "us" only.
ML_REGIONS = "us,eu"
PROP_REGIONS = "us"
MIN_PROB = 0.55            # favorites below this fair probability are skipped in the lists
TOP_GAMES_FOR_PROPS = 3
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
def api_get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            remaining = resp.headers.get("x-requests-remaining")
            if remaining is not None:
                print(f"    (API credits remaining this month: {remaining})")
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


def fetch_prop_data(game):
    if DEMO:
        import demo_data
        return demo_data.demo_props(game)
    markets = PROP_MARKETS.get(game["sport_key"])
    if not markets or not API_KEY:
        return None
    url = EVENT_ODDS_URL.format(sport=game["sport_key"], event_id=game["event_id"]) + (
        f"?apiKey={API_KEY}&regions={PROP_REGIONS}&markets={','.join(markets)}&oddsFormat=american")
    return api_get(url)


# ------------------------------------------------------------- MODEL ---
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
                best_price=fav["best_price"], best_book=fav["best_book"], ev=fav["ev"])
    values = []
    for r in res:
        if model.is_value(r):
            values.append(dict(game_base, kind="Moneyline",
                               pick=f"{r['outcome']} ML",
                               matchup=f"{game_base['away']} @ {game_base['home']}", **r))
    return game, values


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


def conf_cell(c):
    return f'<td class="conf {c.lower()}">{c}</td>'


def build_html(games, values, props_all, anytime):
    games.sort(key=lambda g: g["fair"], reverse=True)
    values.sort(key=lambda v: v["ev"], reverse=True)
    props_all.sort(key=lambda p: p["fair"], reverse=True)
    anytime.sort(key=lambda p: p["prob"], reverse=True)
    listed = [g for g in games if g["fair"] >= MIN_PROB]
    best = [g for g in listed if g["conf"] in ("High", "Medium")][:6]

    def game_row(g, i):
        return (f'<tr><td class="rank">{i}</td><td class="sport">{esc(g["sport"])}</td>'
                f'<td>{esc(g["away"])} @ {esc(g["home"])}</td><td class="pick">{esc(g["favorite"])}</td>'
                f'<td class="num">{pct(g["fair"])}</td><td class="num">{fmt_price(g["best_price"])}</td>'
                f'{conf_cell(g["conf"])}<td class="time">{fmt_time(g["commence"])}</td></tr>')

    def value_row(v, i):
        return (f'<tr><td class="rank">{i}</td><td class="sport">{esc(v["sport"])}</td>'
                f'<td class="pick">{esc(v["pick"])}<div class="sub">{esc(v["matchup"])}</div></td>'
                f'<td class="num">{fmt_price(v["best_price"])}<div class="sub">{esc(v["best_book"])}</div></td>'
                f'<td class="num">{pct(v["fair"])}</td><td class="num edge">+{v["ev"] * 100:.1f}%</td>'
                f'<td class="num">{v["stake_pct"]:.2f}%</td>{conf_cell(v["confidence"])}'
                f'<td class="time">{fmt_time(v["commence"])}</td></tr>')

    def prop_row(p, i):
        return (f'<tr><td class="rank">{i}</td><td class="sport">{esc(p["sport"])}</td>'
                f'<td>{esc(p["matchup"])}</td><td class="pick">{esc(p["pick"])}</td>'
                f'<td class="num">{pct(p["fair"])}</td><td class="num">{fmt_price(p["best_price"])}'
                f'<div class="sub">{esc(p["best_book"])}</div></td>'
                f'<td class="num">{p["ev"] * 100:+.1f}%</td>{conf_cell(p["confidence"])}</tr>')

    def any_row(p, i):
        return (f'<tr><td class="rank">{i}</td><td class="sport">{esc(p["sport"])}</td>'
                f'<td>{esc(p["matchup"])}</td><td class="pick">{esc(p["player"])} {esc(fmt_market(p["market"]))}</td>'
                f'<td class="num">{pct(p["prob"])}*</td><td class="num">{fmt_price(p["price"])}'
                f'<div class="sub">{esc(p["book"])}</div></td></tr>')

    g_head = ["#", "Sport", "Matchup", "Favorite", "Fair %", "Best odds", "Confidence", "Start"]
    v_head = ["#", "Sport", "Pick", "Best price", "Fair %", "Edge", "Stake", "Confidence", "Start"]
    p_head = ["#", "Sport", "Matchup", "Prop", "Fair %", "Best price", "Edge", "Confidence"]
    a_head = ["#", "Sport", "Matchup", "Prop", "Implied %", "Best price"]

    tabs = {
        "value": table(v_head, [value_row(v, i + 1) for i, v in enumerate(values)],
                       "No positive-edge bets found right now. That is normal: most days the market is efficient."),
        "best": table(g_head, [game_row(g, i + 1) for i, g in enumerate(best)],
                      "No high-confidence favorites found right now."),
        "full": table(g_head, [game_row(g, i + 1) for i, g in enumerate(listed)],
                      "No qualifying games found right now."),
        "props": (table(p_head, [prop_row(p, i + 1) for i, p in enumerate(props_all)],
                        "No over/under props were pulled yet.")
                  + ('<div class="count" style="margin-top:18px">Anytime props (no opposing side to de-vig, so '
                     'no edge is calculated; *implied % still includes the book margin)</div>'
                     + table(a_head, [any_row(p, i + 1) for i, p in enumerate(anytime[:25])], "")
                     if anytime else "")),
    }
    counts = {
        "value": f"{len(values)} positive-edge bets vs. the sharp-weighted fair price, sorted by edge",
        "best": "Favorites with the strongest fair probability and the tightest agreement between books",
        "full": f"{len(listed)} games ranked by fair (de-vigged) win probability",
        "props": f"{len(props_all)} over/under props from today's top games, ranked by fair probability",
    }
    panels = "".join(
        f'<div id="{k}" class="panel{" active" if k == "value" else ""}">'
        f'<div class="count">{counts[k]}</div>{tabs[k]}</div>' for k in tabs)

    updated = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y, %-I:%M %p UTC")
    demo_banner = '<div class="demo">DEMO DATA: synthetic odds for previewing the layout only.</div>' if DEMO else ""
    return (TEMPLATE.replace("%%UPDATED%%", updated).replace("%%PANELS%%", panels)
            .replace("%%TX%%", esc(TX_LEGAL_NOTE)).replace("%%MODEL%%", esc(MODEL_NOTE))
            .replace("%%DEMO%%", demo_banner))


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
      <button class="tab-btn" onclick="showTab('props', event)">Player Props</button>
    </div>
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
  </script>
</body>
</html>
"""


# ------------------------------------------------------------- MAIN ---
def main():
    if not API_KEY and not DEMO:
        print("WARNING: ODDS_API_KEY is not set. Writing an empty page. "
              "(Try: python build.py --demo)", file=sys.stderr)

    games, values = [], []
    for sport_key, label in SPORTS:
        print(f"Fetching {label} moneylines...")
        n = 0
        for ev in fetch_events(sport_key):
            g, v = analyze_game(ev, label, sport_key)
            if g:
                games.append(g)
                values.extend(v)
                n += 1
        print(f"  -> {n} games modeled")

    games.sort(key=lambda g: g["fair"], reverse=True)
    props_all, anytime = [], []
    eligible = [g for g in games if g["sport_key"] in PROP_MARKETS or DEMO]
    for g in eligible[:TOP_GAMES_FOR_PROPS]:
        print(f"Fetching props for {g['away']} @ {g['home']}...")
        v, a, any_rows = analyze_props(fetch_prop_data(g), g)
        values.extend(v)
        props_all.extend(a)
        anytime.extend(any_rows)
        print(f"  -> {len(a)} over/under props, {len(v)} flagged")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(games, values, props_all, anytime))
    print(f"Wrote index.html: {len(games)} games, {len(values)} value bets, {len(props_all)} props.")


if __name__ == "__main__":
    main()
