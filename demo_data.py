"""Synthetic odds so you can preview the page without an API key:  python build.py --demo"""
import random

BOOKS = [("pinnacle", "Pinnacle"), ("circasports", "Circa Sports"), ("draftkings", "DraftKings"),
         ("fanduel", "FanDuel"), ("betmgm", "BetMGM"), ("caesars", "Caesars"),
         ("betonlineag", "BetOnline.ag"), ("bovada", "Bovada")]


def _american(p):
    p = min(max(p, 0.02), 0.97)
    return round(-p / (1 - p) * 100) if p >= 0.5 else round((1 - p) / p * 100)


def _quote(fair, vig, noise, rng, shade=None):
    raw = [max(0.01, p + rng.uniform(-noise, noise)) for p in fair]
    if shade:
        raw[shade[0]] = max(0.01, raw[shade[0]] - shade[1])
    s = sum(raw)
    return [_american(r / s * (1 + vig)) for r in raw]


def _event(rng, sport_key, home, away, fair, names, commence, soft_shade=None):
    bks = []
    for key, title in BOOKS:
        sharp = key in ("pinnacle", "circasports")
        prices = _quote(fair, 0.02 if sharp else 0.05, 0.004 if sharp else 0.012, rng,
                        soft_shade if key == "bovada" else None)
        bks.append({"key": key, "title": title, "markets": [{"key": "h2h", "outcomes": [
            {"name": n, "price": p} for n, p in zip(names, prices)]}]})
    return {"id": f"{home}-{away}".replace(" ", ""), "sport_key": sport_key, "home_team": home,
            "away_team": away, "commence_time": commence, "bookmakers": bks}


def demo_moneylines(sport_key):
    rng = random.Random(sport_key)
    if sport_key == "icehockey_nhl":
        return [
            _event(rng, sport_key, "Winnipeg Jets", "Colorado Avalanche", [0.34, 0.66],
                   ["Winnipeg Jets", "Colorado Avalanche"], "2026-10-08T23:30:00Z", soft_shade=(0, 0.05)),
            _event(rng, sport_key, "Washington Capitals", "Pittsburgh Penguins", [0.62, 0.38],
                   ["Washington Capitals", "Pittsburgh Penguins"], "2026-10-08T23:30:00Z"),
        ]
    if sport_key == "baseball_mlb":
        return [_event(rng, sport_key, "New York Yankees", "Tampa Bay Rays", [0.60, 0.40],
                       ["New York Yankees", "Tampa Bay Rays"], "2026-10-09T00:00:00Z", soft_shade=(1, 0.04))]
    if sport_key == "soccer_epl":
        return [_event(rng, sport_key, "Arsenal", "Brentford", [0.68, 0.18, 0.14],
                       ["Arsenal", "Brentford", "Draw"], "2026-10-10T14:00:00Z")]
    return []


def demo_props(game):
    rng = random.Random(game["event_id"])
    bks = []
    for key, title in BOOKS:
        sharp = key in ("pinnacle", "circasports")
        o, u = _quote([0.45, 0.55], 0.02 if sharp else 0.05, 0.006, rng,
                      (0, 0.10) if key == "bovada" else None)
        bks.append({"key": key, "title": title, "markets": [
            {"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Nathan MacKinnon", "point": 1.5, "price": o},
                {"name": "Under", "description": "Nathan MacKinnon", "point": 1.5, "price": u}]},
            {"key": "player_goal_scorer_anytime", "outcomes": [
                {"name": "Nathan MacKinnon", "price": 140 + rng.randint(-10, 10)}]}]})
    return {"bookmakers": bks}
