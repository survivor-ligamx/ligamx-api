import time
from typing import Any
from app import models


def sync_all_stats(db, matches, scraper, tmap, season=None):
    from app.season import current_season_year

    season = season or current_season_year()
    print("Sincronizando stats combinados...")
    db.query(models.TopScorer).filter(models.TopScorer.season == season).delete()
    db.query(models.MatchStat).filter(models.MatchStat.season == season).delete()
    db.query(models.PlayerStat).filter(models.PlayerStat.season == season).delete()
    pids = {p.id for p in db.query(models.Player).all()}
    scorers: dict[str, Any] = {}
    ms: list[Any] = []
    ps: dict[str, Any] = {}
    seen: dict[str, Any] = {}

    def num(v):
        if v is None:
            return None
        s = str(v).replace("%", "").replace(",", "").strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return None

    def add_player(ath, tname, tid, field, eid):
        if not ath:
            return
        name = ath.get("displayName")
        pid = ath.get("id")
        if not name or not pid:
            return
        ip = int(pid)
        if ip not in pids:
            db.add(models.Player(id=ip, name=name, team_id=tid))
            pids.add(ip)
        key = (name, ip)
        seen.setdefault(key, set()).add(eid)  # type: ignore[call-overload]
        ps.setdefault(key, {"player_id": ip, "player_name": name, "team_id": tid, "team_name": tname, "season": season, "goals": 0, "assists": 0, "yellow_cards": 0, "red_cards": 0})[field] += 1  # type: ignore[call-overload]

    for m in matches:
        if m.get("status") != "finished":
            continue
        eid = m.get("event_id")
        if not eid:
            continue
        try:
            data = scraper._get_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/summary?event={eid}")
        except Exception as e:
            print(f"⚠️ {eid}: {e}")
            continue
        for team in data.get("boxscore", {}).get("teams", []):
            tname = team.get("team", {}).get("displayName")
            ts = {s.get("name"): s.get("displayValue") for s in team.get("statistics", [])}
            ms.append(
                models.MatchStat(
                    team_id=tmap.get(tname),
                    team_name=tname,
                    event_id=str(eid),
                    season=season,
                    possession=num(ts.get("possessionPct")),
                    shots=num(ts.get("totalShots")),
                    shots_on_target=num(ts.get("shotsOnTarget")),
                    corners=num(ts.get("wonCorners")),
                    fouls=num(ts.get("foulsCommitted")),
                    yellow_cards=num(ts.get("yellowCards")),
                    red_cards=num(ts.get("redCards")),
                    offsides=num(ts.get("offsides")),
                    saves=num(ts.get("saves")),
                    passes=num(ts.get("accuratePasses")),
                    total_passes=num(ts.get("totalPasses")),
                    tackles=num(ts.get("effectiveTackles")),
                    interceptions=num(ts.get("interceptions")),
                    blocked_shots=num(ts.get("blockedShots")),
                    crosses=num(ts.get("accurateCrosses")),
                    long_balls=num(ts.get("accurateLongBalls")),
                )
            )
        for ev in data.get("keyEvents", []):
            etype = ev.get("type", {}).get("text", "")
            tname = ev.get("team", {}).get("displayName")
            tid = tmap.get(tname)
            parts = ev.get("participants", [])
            if ev.get("scoringPlay"):
                if parts:
                    scorer = parts[0].get("athlete", {})
                    sname = scorer.get("displayName")
                    if sname:
                        scorers[(sname, tname)] = scorers.get((sname, tname), {"player": sname, "team": tname, "goals": 0, "season": season})  # type: ignore[call-overload, index]
                        scorers[(sname, tname)]["goals"] += 1  # type: ignore[index]
                for i, part in enumerate(parts):
                    if not part:
                        continue
                    ath = part.get("athlete", {})
                    add_player(ath, tname, tid, "goals" if i == 0 else "assists", eid)
            elif "Yellow Card" in etype:
                if parts:
                    add_player(parts[0].get("athlete", {}), tname, tid, "yellow_cards", eid)
            elif "Red Card" in etype:
                if parts:
                    add_player(parts[0].get("athlete", {}), tname, tid, "red_cards", eid)
        time.sleep(0.15)
    for s in scorers.values():
        db.add(models.TopScorer(**s))
    for m in ms:
        db.add(m)
    for k, s in ps.items():
        s["matches_played"] = len(seen[k])
        db.add(models.PlayerStat(**s))
    db.commit()
    print("OK goleadores", len(db.query(models.TopScorer).all()))
    print("OK stats por partido", len(db.query(models.MatchStat).all()))
    print("OK stats por jugador", len(db.query(models.PlayerStat).all()))
