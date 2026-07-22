import requests
from typing import Any
import time

TOURNAMENT_ID = 11621
SEASON_ID = 96191  # Liga MX Apertura 2026
HEADERS = {"User-Agent": "Mozilla/5.0"}

def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def get_events(season_id=SEASON_ID):
    events = []
    for round_num in range(1, 30):
        url = f"https://www.sofascore.com/api/v1/unique-tournament/{TOURNAMENT_ID}/season/{season_id}/events/round/{round_num}"
        try:
            data = _get(url)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                break
            raise
        if not data.get("events"):
            break
        events.extend(data["events"])
        time.sleep(0.1)
    return events

def get_match_details(event_id):
    url = f"https://www.sofascore.com/api/v1/event/{event_id}"
    return _get(url)

def get_match_incidents(event_id):
    url = f"https://www.sofascore.com/api/v1/event/{event_id}/incidents"
    return _get(url)

def get_lineups(event_id):
    url = f"https://www.sofascore.com/api/v1/event/{event_id}/lineups"
    return _get(url)

def get_player_stats(season_id=SEASON_ID, limit=100):
    events = get_events(season_id)
    stats: dict[str, Any] = {}
    seen: dict[str, Any] = {}
    for ev in events:
        eid = ev.get("id")
        if not eid or ev.get("status", {}).get("type") != "finished":
            continue
        home = ev.get("homeTeam", {}).get("name")
        away = ev.get("awayTeam", {}).get("name")
        try:
            data = _get(f"https://www.sofascore.com/api/v1/event/{eid}/incidents")
        except Exception as e:
            print(f"⚠️ {eid}: {e}")
            continue
        for inc in data.get("incidents", []):
            itype = inc.get("incidentType")
            team = home if inc.get("isHome") else away
            if itype == "goal":
                p = inc.get("player", {})
                a = inc.get("assist1")
                for athlete, field in [(p, "goals"), (a, "assists")]:
                    if not athlete: continue
                    key = athlete.get("id")
                    if not key: continue
                    seen.setdefault(key, set()).add(eid)
                    s = stats.setdefault(key, {"player_id": key, "player_name": athlete.get("name"), "team_name": team, "goals": 0, "assists": 0, "yellow_cards": 0, "red_cards": 0})
                    s[field] += 1
            elif itype == "card":
                p = inc.get("player", {})
                key = p.get("id")
                if not key: continue
                seen.setdefault(key, set()).add(eid)
                s = stats.setdefault(key, {"player_id": key, "player_name": p.get("name"), "team_name": team, "goals": 0, "assists": 0, "yellow_cards": 0, "red_cards": 0})
                color = inc.get("incidentClass")
                if color == "yellow": s["yellow_cards"] += 1
                elif color == "red": s["red_cards"] += 1
        time.sleep(0.15)
    for key in stats: stats[key]["matches_played"] = len(seen[key])
    return sorted(stats.values(), key=lambda x: x["goals"], reverse=True)[:limit]
