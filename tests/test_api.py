def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "running"


def test_health(client):
    assert client.get("/health").status_code == 200


def test_teams_incluye_logo_y_estadio(client, seeded):
    r = client.get("/teams")
    assert r.status_code == 200
    ame = [t for t in r.json() if t["id"] == 1][0]
    assert ame["logo_url"] == "http://x/a.png"
    assert ame["founded"] == 1916
    assert ame["stadium"]["capacity"] == 50000


def test_standings(client, seeded):
    r = client.get("/standings")
    assert r.status_code == 200
    assert r.json()[0]["team"]["name"] == "América"


def test_liguilla(client, seeded):
    r = client.get("/liguilla")
    assert r.status_code == 200
    body = r.json()
    assert "liguilla_directa" in body and "play_in" in body and "eliminados" in body


def test_team_form(client, seeded):
    r = client.get("/teams/1/form").json()
    assert r["form"] == "W"
    assert r["summary"]["W"] == 1
    assert r["played"] == 1


def test_h2h_summary(client, seeded):
    r = client.get("/h2h/1/2/summary").json()
    assert r["played"] == 1
    assert r["team1"]["wins"] == 1
    assert r["team2"]["wins"] == 0
    assert r["team1"]["goals"] == 2
    assert r["draws"] == 0


def test_player_search(client, seeded):
    r = client.get("/players/search", params={"q": "henry"}).json()
    assert len(r) == 1 and r[0]["name"] == "Henry Martín"
    # busqueda ignora acentos y filtra por nacionalidad
    r2 = client.get("/players/search", params={"q": "martin"}).json()
    assert len(r2) == 1
    r3 = client.get("/players/search", params={"nationality": "mexico"}).json()
    assert len(r3) == 1


def test_season_endpoint(client, seeded):
    r = client.get("/season").json()
    assert r["loaded_season"] == "Apertura 2026"
    assert r["tournament_type"] == "Apertura"
    assert r["finished_matches"] == 1
    assert r["total_matches"] == 1


def test_sync_requiere_api_key(client):
    # sin header -> 422 (falta X-API-Key)
    assert client.post("/sync", params={"source": "demo"}).status_code == 422
    # key incorrecta -> 403
    assert client.post("/sync", params={"source": "demo"}, headers={"X-API-Key": "wrong"}).status_code == 403


def test_sync_status_sin_datos(client):
    r = client.get("/sync/status").json()
    assert r["has_data"] is False
    assert r["last_sync"] is None
    assert r["last_successful_sync"] is None
    # Resumen de frescura: sin sync exitosa -> marcado como viejo
    assert r["freshness"]["is_stale"] is True
    assert r["freshness"]["last_successful_sync_at"] is None
    # Conteos presentes y en cero (pretemporada / BD vacia)
    assert r["data_counts"]["teams"] == 0
    assert r["data_counts"]["matches"] == 0


def test_match_timeline(client, seeded):
    r = client.get("/matches/1/timeline")
    assert r.status_code == 200
    eventos = r.json()
    assert len(eventos) == 2
    # ordenados por minuto: gol (23') antes que tarjeta (55')
    assert eventos[0]["event_type"] == "goal" and eventos[0]["event_time"] == 23
    tipos = {e["event_type"] for e in eventos}
    assert "yellow_card" in tipos


def test_match_squad(client, seeded):
    r = client.get("/matches/1/squad").json()
    equipos = {t["team_id"]: t for t in r["teams"]}
    assert 1 in equipos
    assert equipos[1]["starters"][0]["player_name"] == "Henry Martín"
    assert equipos[1]["starters"][0]["jersey_number"] == 21


def test_match_full(client, seeded):
    r = client.get("/matches/1/full").json()
    assert r["id"] == 1
    assert r["score"] == {"home": 2, "away": 1}
    assert len(r["timeline"]) == 2
    assert len(r["lineups"]) >= 1
    assert len(r["stats"]) == 1
    assert r["stats"][0]["possession"] == 58.0


def test_match_full_404(client, seeded):
    assert client.get("/matches/999/full").status_code == 404


# ---------- Fase C: stats por jugador (365Scores) y arbitros ----------


def test_match_full_incluye_referee_y_venue(client, seeded, db):
    from app import models

    m = db.get(models.Match, 1)
    m.referee = "César Ramos"
    db.commit()
    r = client.get("/matches/1/full").json()
    assert r["referee"] == "César Ramos"
    assert "venue" in r  # presente aunque sea None


def test_365_player_leaders(client, monkeypatch):
    from app.scrapers import scores365_scraper

    fake = [{"category_id": 1, "category": "Goles", "leaders": [{"rank": 1, "name": "Paulinho", "value": "14", "team_id": 2078}]}]
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_player_season_leaders", lambda self, category_id=None: fake)
    r = client.get("/365scores/leaders")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["category"] == "Goles"
    assert body[0]["leaders"][0]["name"] == "Paulinho"


def test_365_match_player_stats(client, monkeypatch):
    from app.scrapers import scores365_scraper

    fake = {
        "game_id": 123,
        "teams": [
            {
                "team_name": "Pumas",
                "formation": "4-3-3",
                "players": [{"name": "Carrasquilla", "rating": 6.8, "stats": {"Minutes": "90'", "Total Remates": "3"}}],
            }
        ],
    }
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_match_player_stats", lambda self, game_id: fake)
    r = client.get("/365scores/matches/123/player-stats")
    assert r.status_code == 200
    body = r.json()
    assert body["teams"][0]["players"][0]["rating"] == 6.8
    assert body["teams"][0]["players"][0]["stats"]["Minutes"] == "90'"


# ---------- Fase D: stats por jugador persistidas en BD ----------


def test_stat_parsers():
    from app.services.sync_service import _stat_int, _stat_float, _stat_fraction

    assert _stat_int("58'") == 58
    assert _stat_int("0") == 0
    assert _stat_int(None) is None
    assert _stat_float("0.05") == 0.05
    assert _stat_float("90'") == 90.0
    assert _stat_fraction("21/26 (81%)") == (21, 26)
    assert _stat_fraction("5") == (5, None)
    assert _stat_fraction(None) == (None, None)


def _seed_player_match_stats(db):
    from app import models

    db.add(
        models.PlayerMatchStat(
            match_id=1,
            player_id=999,
            player_name="Henry Martín",
            team_id=1,
            team_name="América",
            season="Apertura 2026",
            starter=1,
            minutes=90,
            goals=2,
            assists=1,
            shots=4,
            xg=1.2,
            xa=0.3,
            touches=60,
            interceptions=1,
            rating=8.5,
            stats={"Toques": "60"},
        )
    )
    db.add(models.PlayerMatchStat(match_id=1, player_id=998, player_name="Rival X", team_id=2, team_name="Chivas", season="Apertura 2026", starter=1, minutes=90, goals=0, assists=0, rating=6.1))
    db.commit()


def test_match_player_stats_db(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/matches/1/player-stats").json()
    teams = {t["team_id"]: t for t in r["teams"]}
    assert 1 in teams and 2 in teams
    p = teams[1]["players"][0]
    assert p["player_name"] == "Henry Martín"
    assert p["goals"] == 2 and p["assists"] == 1 and p["rating"] == 8.5


def test_player_season_stats(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/players/10/season-stats").json()
    assert r["appearances"] == 1
    assert r["goals"] == 2 and r["assists"] == 1
    assert r["minutes"] == 90 and r["avg_rating"] == 8.5


def test_player_match_stats_history(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/players/10/match-stats").json()
    assert len(r) == 1 and r[0]["goals"] == 2 and r[0]["match_id"] == 1


def test_players_season_leaders(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/players/season-leaders", params={"stat": "goals"}).json()
    assert r[0]["player"] == "Henry Martín" and r[0]["value"] == 2
    # rating con filtro de apariciones
    r2 = client.get("/players/season-leaders", params={"stat": "rating"}).json()
    assert r2[0]["player"] == "Henry Martín" and r2[0]["value"] == 8.5


# ---------- Histórico multi-temporada ----------


def _season_payload(event_id="E1", match_date=None):
    from datetime import datetime

    return dict(
        stadiums=[{"name": "Azteca", "city": "CDMX"}],
        teams=[{"id": 1, "name": "América"}, {"id": 2, "name": "Chivas"}],
        players=[{"id": 10, "name": "Henry Martín", "team_name": "América"}],
        matches=[
            {
                "home_team": "América",
                "away_team": "Chivas",
                "home_team_id": 1,
                "away_team_id": 2,
                "status": "finished",
                "home_score": 1,
                "away_score": 0,
                "match_date": match_date or datetime(2025, 8, 1),
                "event_id": event_id,
            }
        ],
        standings=[{"team_name": "América", "position": 1, "played": 1, "won": 1, "drawn": 0, "lost": 0, "goals_for": 1, "goals_against": 0, "points": 3}],
    )


def test_write_season_data_no_destructivo(db):
    from app import models
    from app.services.sync_service import _write_season_data

    # Dos torneos del MISMO año deben coexistir (los event IDs de ESPN son globales).
    _write_season_data(
        db,
        tournament="Clausura",
        year=2026,
        **_season_payload(event_id="C2026-E1"),
    )
    db.commit()
    _write_season_data(
        db,
        tournament="Apertura",
        year=2026,
        **_season_payload(event_id="A2026-E1"),
    )
    db.commit()
    assert db.query(models.Season).count() == 2
    assert db.query(models.Match).count() == 2
    assert db.query(models.Team).count() == 2
    assert db.query(models.Player).count() == 1  # upsert por id, no duplica
    # Re-sincronizar un torneo NO duplica ni borra el otro.
    _write_season_data(
        db,
        tournament="Clausura",
        year=2026,
        **_season_payload(event_id="C2026-E1"),
    )
    db.commit()
    assert db.query(models.Season).count() == 2
    assert db.query(models.Match).count() == 2
    assert db.query(models.Team).count() == 2


def test_write_season_data_conserva_identidad_y_relaciones(db):
    from datetime import datetime

    from app import models
    from app.services.sync_service import _write_season_data

    payload = _season_payload(event_id="ESPN-STABLE-1")
    _write_season_data(db, tournament="Apertura", year=2026, **payload)
    db.commit()
    original = db.query(models.Match).filter_by(espn_event_id="ESPN-STABLE-1").one()
    original_id = original.id
    db.add(
        models.MatchEvent(
            match_id=original_id,
            event_type="goal",
            event_time=10,
            player_name="Henry Martín",
        )
    )
    db.commit()

    payload["matches"][0].update(
        home_score=3,
        match_date=datetime(2026, 8, 2, 1, 30),
    )
    _write_season_data(db, tournament="Apertura", year=2026, **payload)
    db.commit()

    refreshed = db.query(models.Match).filter_by(espn_event_id="ESPN-STABLE-1").one()
    assert refreshed.id == original_id
    assert refreshed.home_score == 3
    assert refreshed.match_date == datetime(2026, 8, 2, 1, 30)
    assert db.query(models.MatchEvent).filter_by(match_id=original_id).count() == 1


def test_ids_de_otro_proveedor_no_colisionan_con_espn(db):
    from datetime import datetime

    from app import models
    from app.services.sync_service import _write_season_data

    _write_season_data(
        db,
        tournament="Apertura",
        year=2025,
        source="espn",
        **_season_payload(event_id="123", match_date=datetime(2025, 8, 1)),
    )
    db.commit()
    espn_match = db.query(models.Match).filter_by(espn_event_id="123").one()
    espn_match_id = espn_match.id

    _write_season_data(
        db,
        tournament="Clausura",
        year=2026,
        source="365scores",
        **_season_payload(event_id="123", match_date=datetime(2026, 1, 10)),
    )
    db.commit()

    assert db.query(models.Match).count() == 2
    preserved = db.get(models.Match, espn_match_id)
    assert preserved.espn_event_id == "123"
    assert preserved.season.name == "Apertura 2025"
    other = db.query(models.Match).filter(models.Match.id != espn_match_id).one()
    assert other.espn_event_id is None


def test_fallo_de_detalle_conserva_eventos_y_alineaciones(db, seeded):
    from app import models
    from app.services.sync_service import _sync_events_and_lineups

    class FailingDetailScraper:
        def get_match_events(self, event_id):
            raise RuntimeError("fuente temporalmente no disponible")

        def get_match_lineups(self, event_id):
            raise RuntimeError("fuente temporalmente no disponible")

    _sync_events_and_lineups(
        db,
        FailingDetailScraper(),
        {"ESP1": {"match_id": 1, "home": "América", "away": "Chivas"}},
        {"América": 1, "Chivas": 2},
    )

    assert db.query(models.MatchEvent).filter_by(match_id=1).count() == 2
    assert db.query(models.MatchLineup).filter_by(match_id=1).count() == 1


def test_snapshot_parcial_no_elimina_partidos_ni_hijos(db):
    from datetime import datetime

    from app import models
    from app.services.sync_service import _write_season_data

    complete = _season_payload(event_id="KEEP-1")
    complete["matches"].append(
        {
            "home_team": "Chivas",
            "away_team": "América",
            "home_team_id": 2,
            "away_team_id": 1,
            "status": "scheduled",
            "home_score": None,
            "away_score": None,
            "match_date": datetime(2025, 8, 8),
            "event_id": "KEEP-2",
        }
    )
    _write_season_data(db, tournament="Apertura", year=2025, **complete)
    db.commit()
    missing = db.query(models.Match).filter_by(espn_event_id="KEEP-2").one()
    db.add(models.MatchEvent(match_id=missing.id, event_type="other"))
    db.commit()

    partial = _season_payload(event_id="KEEP-1")
    _write_season_data(db, tournament="Apertura", year=2025, **partial)
    db.commit()

    assert db.query(models.Match).filter_by(season_id=missing.season_id).count() == 2
    assert db.query(models.MatchEvent).filter_by(match_id=missing.id).count() == 1


def test_seasons_y_standings_por_temporada(client, seeded, db):
    from app import models

    db.add(models.Season(id=2, name="Clausura 2026", year=2026, tournament_type="Clausura"))
    db.flush()
    db.add(models.Standing(season_id=2, team_id=1, position=1, played=1, won=0, drawn=1, lost=0, goals_for=0, goals_against=0, goal_difference=0, points=1))
    db.commit()
    names = {s["name"] for s in client.get("/seasons").json()}
    assert {"Apertura 2026", "Clausura 2026"} <= names
    # Por defecto: temporada vigente (Apertura 2026) -> 2 filas sembradas
    assert len(client.get("/standings").json()) == 2
    # Filtrada a Clausura 2026 -> 1 fila
    assert len(client.get("/standings", params={"season": "Clausura 2026"}).json()) == 1


# ---------- Backfill de temporadas pasadas ----------


def test_compute_standings_from_matches():
    from app.services.sync_service import compute_standings_from_matches

    ms = [
        {"status": "finished", "home_team": "A", "away_team": "B", "home_score": 2, "away_score": 0},
        {"status": "finished", "home_team": "B", "away_team": "A", "home_score": 1, "away_score": 1},
        {"status": "scheduled", "home_team": "A", "away_team": "B", "home_score": None, "away_score": None},
    ]
    table = {r["team_name"]: r for r in compute_standings_from_matches(ms)}
    assert table["A"]["points"] == 4 and table["A"]["played"] == 2 and table["A"]["position"] == 1
    assert table["A"]["goal_difference"] == 2
    assert table["B"]["points"] == 1 and table["B"]["position"] == 2


def test_run_backfill_crea_temporada_pasada(db, monkeypatch):
    from datetime import datetime
    from app import models
    from app.services import sync_service

    class _Fake:
        def get_stadiums(self):
            return [{"name": "Azteca", "city": "CDMX"}]

        def get_teams(self):
            return [{"id": 1, "name": "América"}, {"id": 2, "name": "Chivas"}]

        def get_players(self):
            return [{"id": 10, "name": "Henry Martín", "team_name": "América"}]

        def get_matches(self, season_id=None, tournament=None):
            return [
                {
                    "event_id": "E1",
                    "home_team": "América",
                    "away_team": "Chivas",
                    "home_team_id": 1,
                    "away_team_id": 2,
                    "home_score": 3,
                    "away_score": 1,
                    "status": "finished",
                    "match_date": datetime(2025, 8, 1),
                },
                {
                    "event_id": "E2",
                    "home_team": "Chivas",
                    "away_team": "América",
                    "home_team_id": 2,
                    "away_team_id": 1,
                    "home_score": 0,
                    "away_score": 0,
                    "status": "finished",
                    "match_date": datetime(2025, 8, 8),
                },
            ]

    monkeypatch.setattr(sync_service, "get_scraper", lambda source: _Fake())
    res = sync_service.run_backfill(db, 2025, "Apertura", "espn")
    assert res["season"] == "Apertura 2025"
    assert res["finished_matches"] == 2
    assert db.query(models.Season).filter_by(name="Apertura 2025").count() == 1
    st = db.query(models.Standing).join(models.Season).filter(models.Season.name == "Apertura 2025").order_by(models.Standing.position).all()
    assert st[0].team.name == "América" and st[0].points == 4  # victoria + empate
    assert st[1].team.name == "Chivas" and st[1].points == 1


def test_backfill_valida_torneo(client):
    r = client.post("/sync/backfill", params={"year": 2025, "tournament": "Liguilla"}, headers={"X-API-Key": "test-key"})
    assert r.status_code == 422


# ---------- Liguilla: bracket oficial ----------


def test_liguilla_bracket(client, db):
    from app import models

    db.add(models.Season(id=1, name="Apertura 2026", year=2026, tournament_type="Apertura"))
    db.flush()
    for pos in range(1, 11):
        db.add(models.Team(id=pos, name=f"Equipo {pos}"))
        db.add(models.Standing(season_id=1, team_id=pos, position=pos, played=17, won=10, drawn=0, lost=7, goals_for=20, goals_against=10, goal_difference=10, points=40 - pos))
    db.commit()

    b = client.get("/liguilla/bracket").json()
    assert b["season"] == "Apertura 2026"
    assert len(b["qualified_direct"]) == 6
    assert len(b["play_in_teams"]) == 4
    # Play-In: 7º vs 8º y 9º vs 10º
    assert b["play_in"]["game_1"]["home"]["position"] == 7
    assert b["play_in"]["game_1"]["away"]["position"] == 8
    assert b["play_in"]["game_2"]["home"]["position"] == 9
    # Cuartos sembrados correctamente
    qf = {q["series"]: q for q in b["quarterfinals"]}
    assert qf["C1"]["high_seed"]["position"] == 1
    assert qf["C3"]["high_seed"]["position"] == 3 and qf["C3"]["low_seed"]["position"] == 6
    assert qf["C4"]["high_seed"]["position"] == 4 and qf["C4"]["low_seed"]["position"] == 5


# ---------- Búsqueda global ----------


def test_search_global(client, seeded):
    # jugador por nombre (ignora acentos)
    r = client.get("/search", params={"q": "henry"}).json()
    assert r["counts"]["players"] == 1
    assert r["players"][0]["name"] == "Henry Martín"
    assert r["players"][0]["team_name"] == "América"
    # equipo (acentos: 'América' coincide con 'amer')
    r2 = client.get("/search", params={"q": "amer"}).json()
    assert any(t["name"] == "América" for t in r2["teams"])
    # estadio sembrado
    r3 = client.get("/search", params={"q": "test"}).json()
    assert any(s["name"] == "Estadio Test" for s in r3["stadiums"])


def test_search_prefijo_primero(client, seeded, db):
    from app import models

    # 'Martín' contiene pero no empieza; 'Mart' como prefijo debe ir antes
    db.add(models.Player(id=11, team_id=1, name="Martina López"))
    db.commit()
    r = client.get("/search", params={"q": "mart"}).json()
    # ambos coinciden; el que EMPIEZA por 'mart' (Martina) va primero
    assert r["players"][0]["name"] == "Martina López"


def test_search_requiere_q(client):
    assert client.get("/search").status_code == 422


# ---------- Seguridad: cabeceras, API key y rate limiting ----------


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "no-referrer"


def test_sync_503_si_no_hay_api_key_configurada(client, monkeypatch):
    monkeypatch.delenv("SYNC_API_KEY", raising=False)
    r = client.post("/sync", params={"source": "demo"}, headers={"X-API-Key": "loquesea"})
    assert r.status_code == 503


def test_rate_limit_devuelve_429():
    # App minima con limite bajo para verificar la integracion de slowapi
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware

    lim = Limiter(key_func=get_remote_address, default_limits=["2/minute"])
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    def ping(request: Request):
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/ping").status_code == 200
    assert c.get("/ping").status_code == 200
    assert c.get("/ping").status_code == 429  # tercer request supera 2/minute


# ---------- Streaming en vivo (SSE) ----------


def test_live_stream_sse(client, monkeypatch):
    from app.routers import live

    monkeypatch.setattr(
        live,
        "_live_snapshot",
        lambda: [
            {"event_id": "1", "home_team": "América", "away_team": "Chivas", "home_score": 1, "away_score": 0, "status": "live", "clock": "55'"},
        ],
    )
    with client.stream("GET", "/live/stream", params={"interval": 1, "max_seconds": 1}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        got_data = False
        lines = []
        for line in r.iter_lines():
            lines.append(line)
            if "América" in line:
                got_data = True
                break
    blob = "\n".join(lines)
    assert got_data
    assert "event: live" in blob


# ---------- Versionado /v1 ----------


def test_version_endpoint(client):
    r = client.get("/version").json()
    assert r["current"] == "v1"
    assert "v1" in r["available_versions"]


def test_v1_mirrors_root(client, seeded):
    # health y standings disponibles bajo /v1 igual que en la raiz
    assert client.get("/v1/health").status_code == 200
    root = client.get("/standings").json()
    v1 = client.get("/v1/standings").json()
    assert v1 == root
    assert len(v1) == 2
    # un endpoint mas para asegurar el espejo
    assert client.get("/v1/seasons").status_code == 200


# ---------- Observabilidad: métricas ----------


def test_metrics_endpoint(client, seeded):
    # generamos algo de trafico
    client.get("/health")
    client.get("/standings")
    client.get("/matches/1/full")
    m = client.get("/metrics").json()
    assert m["requests"]["total"] >= 3
    assert "2xx" in m["requests"]["by_class"]
    assert "uptime_seconds" in m
    assert "avg" in m["latency_ms"]
    assert "cache" in m
    # las rutas se normalizan a su plantilla, no IDs concretos
    paths = {p["path"] for p in m["top_paths"]}
    assert any("{match_id}" in p for p in paths) or "/standings" in paths


def test_metrics_cuenta_errores(client):
    from app.metrics import metrics

    before = metrics.snapshot()["requests"]["total"]
    client.get("/matches/999999")  # 404
    after = metrics.snapshot()
    assert after["requests"]["total"] > before
    assert after["requests"]["by_class"].get("4xx", 0) >= 1


# ---------- Joyita: estadísticas de equipo por temporada (ESPN) ----------


def test_team_season_stats(client, seeded, monkeypatch):
    from app.scrapers import espn_requests_scraper as espn

    fake = {
        "team_id": 1,
        "season_year": 2025,
        "categories": {
            "defensive": {"interceptions": 154.0, "effectiveTackles": 176.0},
            "goalKeeping": {"cleanSheet": 6.0, "goalsConceded": 23.0},
        },
    }
    monkeypatch.setattr(espn.ESPNRequestsScraper, "get_team_season_stats", lambda self, team_id, year=None: {**fake, "team_id": team_id, "season_year": year})
    r = client.get("/teams/1/season-stats", params={"season": "Apertura 2025"}).json()
    assert r["season_year"] == 2025  # ano extraido de la etiqueta
    assert r["categories"]["goalKeeping"]["cleanSheet"] == 6.0


def test_team_stats_usa_etiqueta_de_temporada(client, seeded):
    # /teams/{id}/stats ahora resuelve la etiqueta vigente (no el viejo "2026")
    r = client.get("/teams/1/stats").json()
    # la MatchStat sembrada tiene season="Apertura 2026" y team_id=1
    assert r["season"] == "Apertura 2026"
    assert r["matches"] == 1
    assert r["totals"]["shots"] == 12


# ---------- Joyita: shotmap/xG y top performers (365Scores) ----------


def test_365_match_shots(client, monkeypatch):
    from app.scrapers import scores365_scraper

    fake = {
        "game_id": 123,
        "teams": {"home": "Pumas", "away": "Cruz Azul"},
        "totals": {"home": {"shots": 8, "xg": 0.53, "xgot": 0.3, "goals": 0}, "away": {"shots": 12, "xg": 0.95, "xgot": 0.7, "goals": 1}},
        "shots": [
            {
                "minute": "6'",
                "team": "Cruz Azul",
                "side": "away",
                "player": "Rotondi",
                "xg": 0.03,
                "xgot": 0.11,
                "body_part": "Pie izquierdo",
                "outcome": "Atajado",
                "is_goal": False,
                "x": 47.9,
                "y": 75.4,
            }
        ],
    }
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_match_shots", lambda self, game_id: fake)
    r = client.get("/365scores/matches/123/shots").json()
    assert r["totals"]["away"]["xg"] == 0.95
    assert r["shots"][0]["player"] == "Rotondi"
    assert r["shots"][0]["is_goal"] is False


def test_365_top_performers(client, monkeypatch):
    from app.scrapers import scores365_scraper

    fake = {
        "game_id": 123,
        "categories": [
            {
                "category": "Delantero",
                "home": {"player_id": 1, "name": "Morales", "position": "Centro Delantero", "stats": {"Total Remates": "2"}},
                "away": {"player_id": 2, "name": "Otro", "position": "Delantero", "stats": {}},
            },
        ],
    }
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_match_top_performers", lambda self, game_id: fake)
    r = client.get("/365scores/matches/123/top-performers").json()
    assert r["categories"][0]["category"] == "Delantero"
    assert r["categories"][0]["home"]["name"] == "Morales"


# ---------- Estadios oficiales 2026 ----------


def test_estadios_oficiales_2026():
    from app.scrapers.espn_requests_scraper import STADIUMS

    # Renombres oficiales del Apertura 2026
    assert STADIUMS[227]["name"] == "Estadio Banorte"  # ex Azteca (América)
    assert STADIUMS[15720]["name"] == "Estadio Libertad Financiera"  # ex Alfonso Lastras (San Luis)
    # No quedaron nombres viejos
    names = {s["name"] for s in STADIUMS.values()}
    assert "Estadio Azteca" not in names
    assert "Estadio Alfonso Lastras" not in names


# ---------- Calendario, noticias 365 y xG de temporada ----------


def test_matches_publican_id_espn_y_kickoff_utc(client, seeded):
    listed = client.get("/matches").json()[0]
    detailed = client.get("/matches/1").json()
    calendar_match = client.get("/calendar").json()["jornadas"][0]["matches"][0]
    full = client.get("/matches/1/full").json()

    for match in (listed, detailed):
        assert match["espn_event_id"] == "ESP1"
        assert match["match_date"].endswith("Z")
    assert calendar_match["espn_event_id"] == "ESP1"
    assert calendar_match["date"].endswith("Z")
    assert full["espn_event_id"] == "ESP1"
    assert full["match_date"].endswith("Z")


def test_filtros_de_temporada_en_partidos(client, seeded, db):
    from datetime import datetime

    from app import models

    db.add(
        models.Season(
            id=2,
            name="Clausura 2026",
            year=2026,
            tournament_type="Clausura",
        )
    )
    db.flush()
    db.add(
        models.Match(
            id=2,
            season_id=2,
            home_team_id=1,
            away_team_id=2,
            match_date=datetime(2099, 1, 20, 2, 0),
            week_number=2,
            status="scheduled",
            external_event_id="ESP2",
            espn_event_id="ESP2",
        )
    )
    db.commit()

    # Sin parametro se conserva el historico completo; por ano se usa el torneo
    # mas reciente de ese ano (Apertura).
    assert [m["id"] for m in client.get("/matches").json()] == [1, 2]
    assert [m["id"] for m in client.get("/matches", params={"season": "2026"}).json()] == [1]
    # La etiqueta exacta selecciona Clausura y una temporada inexistente no filtra mal.
    clausura = client.get("/matches", params={"season": "Clausura 2026"}).json()
    assert [m["id"] for m in clausura] == [2]
    assert client.get("/matches", params={"season": "Apertura 1900"}).json() == []

    calendar = client.get("/calendar", params={"season": "Clausura 2026"}).json()
    assert calendar["season"] == "Clausura 2026"
    assert calendar["jornadas"][0]["matches"][0]["id"] == 2
    assert [m["id"] for m in client.get("/matches/team/1", params={"season": "Clausura 2026"}).json()] == [2]
    assert [m["id"] for m in client.get("/matches/week/2", params={"season": "Clausura 2026"}).json()] == [2]
    assert client.get("/weeks").json() == [1, 2]
    assert client.get("/weeks", params={"season": "Clausura 2026"}).json() == [2]
    assert [m["id"] for m in client.get("/matches/upcoming", params={"season": "Clausura 2026"}).json()] == [2]


def test_calendar(client, seeded):
    r = client.get("/calendar").json()
    assert r["total_matches"] == 1
    j1 = r["jornadas"][0]
    assert j1["jornada"] == 1
    m = j1["matches"][0]
    assert m["home_team"]["name"] == "América"
    assert m["away_team"]["name"] == "Chivas"
    assert m["score"] == {"home": 2, "away": 1}
    assert "venue" in m


def test_365_news(client, monkeypatch):
    from app.scrapers import scores365_scraper

    monkeypatch.setattr(
        scores365_scraper.Scores365Scraper,
        "get_news",
        lambda self, limit=30: [{"id": 1, "title": "Fichaje bomba", "url": "http://x", "image": "http://i", "published_at": "2026-06-30", "is_magazine": False}],
    )
    r = client.get("/365scores/news").json()
    assert r[0]["title"] == "Fichaje bomba"


def test_xg_performance(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/players/xg-performance").json()
    top = r[0]
    assert top["player"] == "Henry Martín"
    assert top["goals"] == 2 and top["xg"] == 1.2
    assert top["diff"] == 0.8  # 2 goles - 1.2 xG (sobre-rendimiento)


# ---------- Noticias con imagen (RSS + 365Scores unificados) ----------


def test_news_incluye_imagen(client, db):
    from datetime import datetime
    from app import models

    db.add(models.News(title="Gol de último minuto", link="http://x/n1", description="...", source="365Scores", image_url="http://img/portada.webp", published_at=datetime(2026, 7, 1)))
    db.commit()
    r = client.get("/news").json()
    assert r[0]["title"] == "Gol de último minuto"
    assert r[0]["image_url"] == "http://img/portada.webp"
    assert r[0]["source"] == "365Scores"


# ---------- xG por equipo, porteros y heatmaps ----------


def test_teams_xg_performance(client, seeded, db):
    _seed_player_match_stats(db)  # Henry (equipo 1): goals=2, xg=1.2
    r = client.get("/teams/xg-performance").json()
    top = r[0]
    assert top["team_id"] == 1
    assert top["goals"] == 2 and top["xg"] == 1.2 and top["diff"] == 0.8
    # no debe colisionar con /teams/{team_id}
    assert client.get("/teams/xg-performance").status_code == 200


def test_365_goalkeepers(client, monkeypatch):
    from app.scrapers import scores365_scraper

    fake = [{"player_id": 1, "name": "Nahuel Guzmán", "team_id": 10, "clean_sheets": "7", "goals_conceded": "8", "saves": "3.1", "penalties_saved": "1/2"}]
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_goalkeepers", lambda self: fake)
    r = client.get("/365scores/goalkeepers").json()
    assert r[0]["name"] == "Nahuel Guzmán" and r[0]["clean_sheets"] == "7"


def test_365_heatmaps(client, monkeypatch):
    from app.scrapers import scores365_scraper

    fake = {"game_id": 123, "teams": [{"team_name": "Pumas", "players": [{"player_id": 1, "name": "Carrasquilla", "position": "Mediocampista", "heatmap_url": "https://heatmap.365scores.com/?x=1"}]}]}
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_match_heatmaps", lambda self, game_id: fake)
    r = client.get("/365scores/matches/123/heatmaps").json()
    assert r["teams"][0]["players"][0]["heatmap_url"].startswith("https://heatmap")


# ---------- Analítica: comparador y predictor ----------


def test_compare_players(client, seeded, db):
    from app import models

    _seed_player_match_stats(db)
    db.add(models.Player(id=11, team_id=2, name="Rival X"))
    db.commit()
    r = client.get("/compare/players", params={"a": 10, "b": 11}).json()
    assert r["a"]["name"] == "Henry Martín" and r["a"]["goals"] == 2
    assert r["a"]["xg"] == 1.2
    assert r["b"]["name"] == "Rival X" and r["b"]["goals"] == 0


def test_compare_teams(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/compare/teams", params={"a": 1, "b": 2}).json()
    assert r["a"]["team_id"] == 1 and r["a"]["standing"]["position"] == 1
    assert r["a"]["xg"] == 1.2
    assert r["b"]["team_id"] == 2


def test_predict_match(client, seeded):
    r = client.get("/predict", params={"home": 1, "away": 2}).json()
    p = r["probabilities"]
    assert abs(p["home_win"] + p["draw"] + p["away_win"] - 1.0) < 0.05
    assert "expected_goals" in r and "most_likely_score" in r
    # equipo 1 (mejor ataque/defensa) y de local debe ser favorito
    assert p["home_win"] > p["away_win"]


def test_predict_sin_datos(client, db):
    from app import models

    db.add(models.Season(id=1, name="Apertura 2026", year=2026, tournament_type="Apertura"))
    db.add(models.Team(id=1, name="A"))
    db.add(models.Team(id=2, name="B"))
    db.commit()
    # sin standings con partidos jugados -> 400
    assert client.get("/predict", params={"home": 1, "away": 2}).status_code == 400


# ---------- Dashboard y readiness ----------


def test_dashboard(client, seeded):
    r = client.get("/dashboard").json()
    assert r["season"] == "Apertura 2026"
    # líder de la tabla = América (posición 1 sembrada)
    assert r["standings_leader"]["team"]["name"] == "América"
    assert r["standings_leader"]["position"] == 1
    # claves presentes (listas, aunque vacías)
    for k in ("top_scorer", "upcoming_matches", "recent_results", "latest_news"):
        assert k in r
    # hay 1 partido finalizado sembrado -> aparece en recent_results
    assert len(r["recent_results"]) == 1


def test_health_ready(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] in ("disabled", "ok")


# ---------- Power ranking y perfiles ----------


def test_power_ranking(client, seeded):
    r = client.get("/power-ranking").json()
    assert r["season"] == "Apertura 2026"
    assert len(r["ranking"]) == 2
    for row in r["ranking"]:
        assert 0 <= row["rating"] <= 100
        assert "team" in row and "ppg" in row and "rank" in row


def test_player_profile(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/players/10/profile").json()
    assert r["player"]["name"] == "Henry Martín"
    assert r["player"]["team"]["name"] == "América"
    assert r["season_stats"]["goals"] == 2
    assert r["season_stats"]["xg"] == 1.2
    assert len(r["recent_matches"]) >= 1


def test_team_profile(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/teams/1/profile").json()
    assert r["team"]["name"] == "América"
    assert r["standing"]["position"] == 1
    assert r["xg"] == 1.2
    assert r["squad_size"] >= 1
    assert "form" in r and "last_result" in r


# ---------- Jugadores a seguir ----------


def test_players_to_watch(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/matches/1/players-to-watch").json()
    assert r["season"] == "Apertura 2026"
    assert r["home_team"]["id"] == 1 and r["away_team"]["id"] == 2
    hp = r["home_team"]["players"]
    assert hp and hp[0]["player"] == "Henry Martín"
    assert hp[0]["goals"] == 2 and "reason" in hp[0] and hp[0]["watch_score"] > 0
    ap = r["away_team"]["players"]
    assert ap and ap[0]["player"] == "Rival X"


def test_players_to_watch_sin_datos(client, seeded):
    # match sembrado pero sin player_match_stats -> note y listas vacías
    r = client.get("/matches/1/players-to-watch").json()
    assert r["home_team"]["players"] == [] and r["away_team"]["players"] == []
    assert "note" in r


# ---------- Disciplina: tarjetas acumuladas y suspensiones ----------


def _seed_cards(db):
    """Agrega tarjetas al jugador 'Tarjetero' (equipo 1): 4 amarillas (en riesgo)."""
    from app import models

    for minute in (10, 20, 30, 40):
        db.add(models.MatchEvent(match_id=1, event_type="yellow_card", event_time=minute, player_name="Tarjetero", team_id=1, team_name="América", description="Yellow Card", is_home=1))
    db.commit()


def test_players_discipline(client, seeded, db):
    _seed_cards(db)
    r = client.get("/players/discipline").json()
    assert r["season"] == "Apertura 2026"
    players = {p["player"]: p for p in r["players"]}
    # Rival X tiene 1 amarilla sembrada en el fixture; Tarjetero 4
    assert players["Tarjetero"]["yellow_cards"] == 4
    assert players["Tarjetero"]["suspension_risk"] is True
    assert players["Tarjetero"]["yellows_to_suspension"] == 1
    assert players["Rival X"]["yellow_cards"] == 1
    assert players["Rival X"]["suspension_risk"] is False
    # ordenado por discipline_points desc -> Tarjetero primero
    assert r["players"][0]["player"] == "Tarjetero"


def test_players_discipline_at_risk(client, seeded, db):
    _seed_cards(db)
    r = client.get("/players/discipline", params={"at_risk": True}).json()
    nombres = {p["player"] for p in r["players"]}
    assert "Tarjetero" in nombres and "Rival X" not in nombres


def test_player_discipline_individual(client, seeded, db):
    from app import models

    db.add(models.Player(id=20, team_id=1, name="Tarjetero"))
    _seed_cards(db)
    r = client.get("/players/20/discipline").json()
    assert r["player"] == "Tarjetero"
    assert r["yellow_cards"] == 4 and r["red_cards"] == 0
    assert r["suspension_risk"] is True


def test_team_discipline(client, seeded, db):
    _seed_cards(db)
    r = client.get("/teams/1/discipline").json()
    assert r["team_id"] == 1
    assert r["totals"]["yellow_cards"] == 4  # Tarjetero (equipo 1)
    assert any(p["player"] == "Tarjetero" for p in r["players"])
    assert len(r["at_risk"]) == 1
    # equipo 2 (Chivas) tiene la amarilla de Rival X
    r2 = client.get("/teams/2/discipline").json()
    assert r2["totals"]["yellow_cards"] == 1


# ---------- Rachas y proyección ----------


def test_player_form(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/players/10/form").json()
    assert r["player"] == "Henry Martín"
    assert r["matches_considered"] == 1
    assert r["goals"] == 2
    assert r["avg_rating"] == 8.5
    assert r["scoring_streak"] == 1  # marcó en su último partido


def test_team_streak(client, seeded):
    # fixture: América (1) ganó 2-1 -> racha de victoria e invicto = 1, anotando = 1
    r = client.get("/teams/1/streak").json()
    assert r["team_id"] == 1
    assert r["matches_played"] == 1
    assert r["recent_form"] == "W"
    s = r["streaks"]
    assert s["wins"] == 1 and s["unbeaten"] == 1 and s["scoring"] == 1
    assert s["winless"] == 0 and s["clean_sheets"] == 0  # recibió 1 gol


def test_standings_projection(client, seeded, db):
    from datetime import datetime
    from app import models

    # sin partidos restantes: la proyección iguala los puntos actuales
    r = client.get("/standings/projection").json()
    assert r["season"] == "Apertura 2026"
    rows = {row["team_id"]: row for row in r["projected_standings"]}
    assert rows[1]["projected_points"] == rows[1]["current_points"]
    assert rows[1]["remaining_matches"] == 0
    # añadimos un partido programado -> ambos equipos suman puntos esperados
    db.add(models.Match(id=2, season_id=1, home_team_id=1, away_team_id=2, status="scheduled", match_date=datetime(2026, 8, 1), week_number=2))
    db.commit()
    r2 = client.get("/standings/projection").json()
    rows2 = {row["team_id"]: row for row in r2["projected_standings"]}
    assert rows2[1]["remaining_matches"] == 1
    assert rows2[1]["projected_points"] > rows2[1]["current_points"]
    # ordenado por puntos proyectados desc
    pts = [row["projected_points"] for row in r2["projected_standings"]]
    assert pts == sorted(pts, reverse=True)


# ---------- Leaderboard unificado ----------


def test_leaderboard_rendimiento(client, seeded, db):
    _seed_player_match_stats(db)
    # goles
    r = client.get("/players/leaderboard", params={"metric": "goals"}).json()
    assert r["metric"] == "goals"
    assert r["players"][0]["player"] == "Henry Martín"
    assert r["players"][0]["value"] == 2 and r["players"][0]["rank"] == 1
    # rating (promedio) y xg redondeado
    r2 = client.get("/players/leaderboard", params={"metric": "rating"}).json()
    assert r2["players"][0]["value"] == 8.5
    r3 = client.get("/players/leaderboard", params={"metric": "xg"}).json()
    assert r3["players"][0]["value"] == 1.2


def test_leaderboard_disciplina(client, seeded, db):
    _seed_cards(db)  # 'Tarjetero' (equipo 1) con 4 amarillas
    r = client.get("/players/leaderboard", params={"metric": "yellow_cards"}).json()
    assert r["metric"] == "yellow_cards"
    top = {p["player"]: p for p in r["players"]}
    assert top["Tarjetero"]["value"] == 4
    assert r["players"][0]["player"] == "Tarjetero"  # más amarillas primero


def test_leaderboard_metrica_invalida_usa_goals(client, seeded, db):
    _seed_player_match_stats(db)
    r = client.get("/players/leaderboard", params={"metric": "inventada"}).json()
    assert r["metric"] == "goals"  # cae a goals por defecto


# ---------- Cruce de identidad ESPN <-> 365Scores ----------


def test_name_match_score():
    from app.services.player_identity import name_match_score

    # igualdad exacta (ignora acentos)
    assert name_match_score("Henry Martín", "Henry Martín") == 1.0
    assert name_match_score("Julian Quinones", "Julián Quiñones") == 1.0
    # nombre corto con inicial + apellido -> match fuerte
    assert name_match_score("J. Quiñones", "Julián Quiñones") >= 0.9
    # apodo sin tokens comunes -> sin relación
    assert name_match_score("Chaco", "Diego Valdés") == 0.0
    # solo apellido -> señal media (no decisiva por sí sola)
    assert 0.5 <= name_match_score("García", "Luis García") < 0.95


def test_build_identity_map(db):
    from app import models
    from app.services.player_identity import build_player_identity_map

    db.add(models.Team(id=1, name="América"))
    db.add(models.Player(id=10, team_id=1, name="Henry Martín"))
    db.add(models.Player(id=11, team_id=1, name="Julián Quiñones"))
    db.flush()
    # stats de 365Scores: ids propios y nombres distintos (uno abreviado)
    db.add(models.PlayerMatchStat(match_id=1, player_id=5001, player_name="Henry Martín", team_id=1, season="Apertura 2026", goals=1))
    db.add(models.PlayerMatchStat(match_id=1, player_id=5002, player_name="J. Quiñones", team_id=1, season="Apertura 2026", goals=2))
    db.commit()
    res = build_player_identity_map(db, "Apertura 2026")
    assert res["mapped"] == 2 and res["unmatched"] == 0
    assert db.get(models.Player, 10).external_365_id == 5001
    assert db.get(models.Player, 11).external_365_id == 5002


def test_identity_map_homonimos(db):
    # Dos apellidos iguales en el MISMO equipo: se desambiguan por el nombre/inicial
    from app import models
    from app.services.player_identity import build_player_identity_map

    db.add(models.Team(id=1, name="América"))
    db.add(models.Player(id=20, team_id=1, name="Luis García"))
    db.add(models.Player(id=21, team_id=1, name="Carlos García"))
    db.flush()
    db.add(models.PlayerMatchStat(match_id=1, player_id=6001, player_name="L. García", team_id=1, season="S"))
    db.add(models.PlayerMatchStat(match_id=1, player_id=6002, player_name="C. García", team_id=1, season="S"))
    db.commit()
    build_player_identity_map(db, "S")
    assert db.get(models.Player, 20).external_365_id == 6001
    assert db.get(models.Player, 21).external_365_id == 6002


def test_identity_map_endpoint(client, seeded, db):
    from app import models

    p = db.get(models.Player, 10)
    p.external_365_id = 999
    db.commit()
    r = client.get("/players/identity-map").json()
    assert r["players_total"] >= 1 and r["players_mapped"] >= 1
    assert any(s["external_365_id"] == 999 for s in r["sample"])


def test_stats_por_id_exacto(client, seeded, db):
    # Jugador 10 mapeado a id 365 = 7777; la fila de stats usa ESE id pero un
    # nombre DISTINTO que NO casaría por nombre -> debe encontrarse por id exacto.
    from app import models

    p = db.get(models.Player, 10)
    p.external_365_id = 7777
    db.add(models.PlayerMatchStat(match_id=1, player_id=7777, player_name="H. Martín (365)", team_id=1, team_name="América", season="Apertura 2026", minutes=90, goals=3, assists=1, rating=9.0))
    db.commit()
    r = client.get("/players/10/season-stats").json()
    assert r["goals"] == 3 and r["assists"] == 1


def test_sync_player_identity_requiere_api_key(client):
    assert client.post("/sync/player-identity").status_code == 422
    assert client.post("/sync/player-identity", headers={"X-API-Key": "wrong"}).status_code == 403


# ---------- Bio enriquecida de jugadores ----------


def test_player_bio_en_response(client, seeded, db):
    from app import models

    p = db.get(models.Player, 10)
    p.nationality = "México"
    p.flag_url = "https://a.espncdn.com/i/teamlogos/countries/500/mex.png"
    p.height = "1.78 m"
    p.weight = "75 kg"
    p.birth_date = "1992-11-22T08:00Z"
    db.commit()
    r = client.get("/players/10").json()
    assert r["nationality"] == "México"
    assert r["flag_url"].endswith("mex.png")
    assert r["height"] == "1.78 m" and r["weight"] == "75 kg"


def test_player_profile_incluye_edad_y_bio(client, seeded, db):
    _seed_player_match_stats(db)
    from app import models

    p = db.get(models.Player, 10)
    p.birth_date = "1992-11-22T08:00Z"
    p.flag_url = "http://flag/mex.png"
    p.height = "1.78 m"
    db.commit()
    r = client.get("/players/10/profile").json()
    assert r["player"]["age"] == 33  # nacido 1992-11-22, a fecha 2026
    assert r["player"]["flag_url"] == "http://flag/mex.png"
    assert r["player"]["height"] == "1.78 m"


def test_age_from_birthdate():
    from app.routers.players import _age_from_birthdate

    assert _age_from_birthdate("1992-11-22T08:00Z") == 33
    assert _age_from_birthdate("2007-09-05") == 18
    assert _age_from_birthdate(None) is None
    assert _age_from_birthdate("texto-invalido") is None


def test_espn_scraper_nacionalidad_desde_citizenship():
    # Regresión del bug: el roster de ESPN usa 'citizenship', no 'country'
    from app.scrapers.espn_requests_scraper import ESPNRequestsScraper

    s = ESPNRequestsScraper()
    s._teams = [{"id": 1, "name": "América"}]

    def fake_get_json(url, params=None, retries=3):
        return {
            "athletes": [
                {
                    "id": "555",
                    "displayName": "Jugador Prueba",
                    "jersey": "9",
                    "citizenship": "México",
                    "dateOfBirth": "2000-01-01T08:00Z",
                    "flag": {"href": "http://flag/mex.png"},
                    "displayHeight": "1.80 m",
                    "displayWeight": "76 kg",
                    "position": {"abbreviation": "DEL"},
                }
            ]
        }

    s._get_json = fake_get_json
    players = s.get_players()
    p = players[0]
    assert p["nationality"] == "México"
    assert p["flag_url"] == "http://flag/mex.png"
    assert p["height"] == "1.80 m" and p["weight"] == "76 kg"


# ---------- Liguilla: resultados reales por serie ----------


def test_classify_phase():
    from app.routers.standings import _classify_phase

    assert _classify_phase("Cuartos de Final", None)[0] == "quarterfinals"
    assert _classify_phase("Semifinal", None)[0] == "semifinals"
    assert _classify_phase("Final", None)[0] == "final"
    assert _classify_phase("Reclasificación", None)[0] == "play_in"
    # temporada regular -> no es fase final
    assert _classify_phase("Fecha 5", None)[0] is None
    assert _classify_phase(None, None)[0] is None
    # 'Semifinal' contiene 'final' pero NO debe clasificarse como Final
    assert _classify_phase("Semifinal", None)[0] == "semifinals"


def test_liguilla_results_sin_fase_final(client, seeded):
    # el fixture solo tiene un partido de temporada regular -> sin datos de Liguilla
    r = client.get("/liguilla/results").json()
    assert r["season"] == "Apertura 2026"
    assert r["has_playoff_data"] is False
    assert r["series_count"] == 0


def test_liguilla_results_serie_real(client, seeded, db):
    from datetime import datetime
    from app import models

    # Cuartos de final, ida y vuelta entre equipo 1 y 2
    db.add(models.Match(id=50, season_id=1, home_team_id=1, away_team_id=2, home_score=3, away_score=1, status="finished", round_name="Cuartos de Final", match_date=datetime(2026, 11, 20)))
    db.add(models.Match(id=51, season_id=1, home_team_id=2, away_team_id=1, home_score=1, away_score=1, status="finished", round_name="Cuartos de Final", match_date=datetime(2026, 11, 23)))
    db.commit()
    r = client.get("/liguilla/results").json()
    assert r["has_playoff_data"] is True
    assert r["series_count"] == 1
    serie = r["phases"]["quarterfinals"][0]
    agg = {t["team_id"]: t["aggregate"] for t in serie["teams"]}
    assert agg[1] == 4 and agg[2] == 2  # 3+1 vs 1+1
    assert serie["winner_team_id"] == 1 and serie["decided"] is True
    assert len(serie["legs"]) == 2


# ---------- Enlace manual del cruce de identidad ----------


def test_build_identity_respeta_enlace_manual(db):
    from app import models
    from app.services.player_identity import build_player_identity_map

    db.add(models.Team(id=1, name="América"))
    # 'Chaco' es un apodo que NO casa por nombre con "Diego Valdés"
    db.add(models.Player(id=10, team_id=1, name="Diego Valdés", external_365_id=8001))
    db.flush()
    db.add(models.PlayerMatchStat(match_id=1, player_id=8001, player_name="Chaco", team_id=1, season="Apertura 2026", goals=1))
    db.commit()
    res = build_player_identity_map(db, "Apertura 2026")
    # el enlace manual se conserva (no lo pisa ni lo cuenta como nuevo)
    assert res["preserved"] == 1
    assert db.get(models.Player, 10).external_365_id == 8001


def test_link_365_endpoint(client, seeded, db):
    from app import models

    # sin API key -> 422; con key -> enlaza
    assert client.post("/players/10/link-365", params={"external_365_id": 777}).status_code == 422
    r = client.post("/players/10/link-365", params={"external_365_id": 777}, headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json()["external_365_id"] == 777
    assert db.get(models.Player, 10).external_365_id == 777
    # jugador inexistente -> 404
    assert client.post("/players/99999/link-365", params={"external_365_id": 1}, headers={"X-API-Key": "test-key"}).status_code == 404


# ---------- Joyita: link a Google Maps de estadios ----------


def test_stadium_maps_url_por_coordenadas(client, db):
    from app import models

    db.add(models.Stadium(id=5, name="Estadio Banorte", city="CDMX", capacity=87000, latitude=19.3029, longitude=-99.1505))
    db.commit()
    r = client.get("/stadiums/5").json()
    assert r["maps_url"] == "https://www.google.com/maps/search/?api=1&query=19.3029,-99.1505"


def test_stadium_maps_url_por_nombre(client, db):
    from app import models

    db.add(models.Stadium(id=6, name="Estadio Akron", city="Zapopan"))
    db.commit()
    r = client.get("/stadiums/6").json()
    assert r["maps_url"].startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "Estadio+Akron" in r["maps_url"] and "Zapopan" in r["maps_url"]


def test_stadium_maps_url_en_teams(client, seeded):
    # el fixture liga el equipo 1 al estadio 1 -> el maps_url aparece en /teams
    ame = [t for t in client.get("/teams").json() if t["id"] == 1][0]
    assert ame["stadium"]["maps_url"].startswith("https://www.google.com/maps/search/")


# ---------- Comparar temporadas ----------


def test_seasons_compare(client, seeded, db):
    from datetime import datetime
    from app import models

    # segunda temporada con datos propios
    db.add(models.Season(id=2, name="Clausura 2026", year=2026, tournament_type="Clausura"))
    db.flush()
    db.add(models.Team(id=99, name="Equipo Z"))
    db.add(models.Standing(season_id=2, team_id=99, position=1, played=2, won=2, drawn=0, lost=0, goals_for=5, goals_against=1, goal_difference=4, points=6))
    db.add(models.Match(id=70, season_id=2, home_team_id=99, away_team_id=1, home_score=3, away_score=1, status="finished", match_date=datetime(2026, 1, 15)))
    db.add(models.TopScorer(player="Goleador Z", team="Equipo Z", goals=7, season="Clausura 2026"))
    db.commit()

    r = client.get("/seasons/compare", params={"a": "Apertura 2026", "b": "Clausura 2026"}).json()
    assert r["a"]["season"] == "Apertura 2026"
    assert r["b"]["season"] == "Clausura 2026"
    # B: líder Equipo Z, goleador y goles del partido sembrado (3+1=4)
    assert r["b"]["standings_leader"]["team"] == "Equipo Z"
    assert r["b"]["top_scorer"]["player"] == "Goleador Z" and r["b"]["top_scorer"]["goals"] == 7
    assert r["b"]["goals_total"] == 4 and r["b"]["matches_played"] == 1
    # A: el fixture tiene 1 partido finalizado (2+1=3 goles) y líder América
    assert r["a"]["goals_total"] == 3
    assert r["a"]["standings_leader"]["team"] == "América"


def test_seasons_compare_404(client, seeded):
    assert client.get("/seasons/compare", params={"a": "Apertura 2026", "b": "Clausura 2099"}).status_code == 404


# ---------- Transferencias / fichajes (365Scores) ----------

# Payload minimo imitando la respuesta real de 365Scores (endpoint transfers/):
# dos equipos de Liga MX (mainCompetitionId=141) y un club extranjero.
_FAKE_TRANSFERS = {
    "competitors": [
        {"id": 100, "name": "Club América", "mainCompetitionId": 141},
        {"id": 200, "name": "Chivas de Guadalajara", "mainCompetitionId": 141},
        {"id": 300, "name": "Celta de Vigo", "mainCompetitionId": 99},
    ],
    "athletes": [
        {"id": 1, "name": "Borja Iglesias"},
        {"id": 2, "name": "Kevin Álvarez"},
        {"id": 3, "name": "Renovado"},
    ],
    "transfers": [
        # Alta a América desde club extranjero (compra)
        {"athleteId": 1, "origin": 300, "target": 100, "type": 2, "price": "-", "statusName": "Confirmado", "time": "2026-07-01T10:00:00"},
        # Prestamo de América a Chivas: baja para América, alta para Chivas
        {"athleteId": 2, "origin": 100, "target": 200, "type": 3, "price": "Préstamo", "statusName": "Rumor", "time": "2026-07-02T10:00:00"},
        # Renovacion (origin == target): ni alta ni baja
        {"athleteId": 3, "origin": 100, "target": 100, "type": 8, "price": "Extensión de contrato", "statusName": "Confirmado", "time": "2026-07-03T10:00:00"},
        # Fichaje de otro anio: debe filtrarse por defecto (year actual)
        {"athleteId": 1, "origin": 200, "target": 300, "type": 2, "price": "-", "statusName": "Confirmado", "time": "2020-01-01T10:00:00"},
    ],
}


def _patch_transfers_http(monkeypatch, payload=_FAKE_TRANSFERS):
    from app.scrapers import scores365_scraper

    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "_get_json", lambda self, path, params=None, retries=3: payload)


def test_365_transfers_agrupado(client, monkeypatch):
    _patch_transfers_http(monkeypatch)
    r = client.get("/365scores/transfers", params={"year": 2026})
    assert r.status_code == 200
    body = r.json()
    assert body["disponible"] is True
    equipos = body["equipos"]
    # Nombres normalizados al estilo ESPN
    assert "América" in equipos and "Guadalajara" in equipos
    ame = equipos["América"]
    # Alta: Borja Iglesias desde Celta de Vigo (compra = transfer)
    assert {"jugador": "Borja Iglesias", "desde": "Celta de Vigo", "tipo": "transfer"} in ame["altas"]
    # Baja: Kevin a Guadalajara en prestamo (loan)
    assert {"jugador": "Kevin Álvarez", "hacia": "Guadalajara", "tipo": "loan"} in ame["bajas"]
    # La renovacion (origin==target) no cuenta como alta ni baja
    assert all(a["jugador"] != "Renovado" for a in ame["altas"])
    assert all(b["jugador"] != "Renovado" for b in ame["bajas"])
    # Guadalajara recibe a Kevin (alta en prestamo)
    gdl = equipos["Guadalajara"]
    assert {"jugador": "Kevin Álvarez", "desde": "América", "tipo": "loan"} in gdl["altas"]


def test_365_transfers_filtra_por_anio(client, monkeypatch):
    _patch_transfers_http(monkeypatch)
    # Sin fichajes de 2020 en el resultado por defecto (year actual = 2026)
    body = client.get("/365scores/transfers", params={"year": 2026}).json()
    # El fichaje de 2020 iba de Chivas de Guadalajara a Celta; no debe aparecer
    gdl = body["equipos"].get("Guadalajara", {"bajas": []})
    assert all(b["hacia"] != "Celta de Vigo" for b in gdl.get("bajas", []))


def test_365_transfers_filtra_por_status(client, monkeypatch):
    _patch_transfers_http(monkeypatch)
    body = client.get("/365scores/transfers", params={"year": 2026, "status": "confirmado"}).json()
    # Solo confirmados: la alta de Borja (Confirmado) sigue; el prestamo (Rumor) se va
    ame = body["equipos"]["América"]
    assert any(a["jugador"] == "Borja Iglesias" for a in ame["altas"])
    assert ame["bajas"] == []


def test_365_transfers_sin_datos(client, monkeypatch):
    _patch_transfers_http(monkeypatch, payload={"competitors": [], "athletes": [], "transfers": []})
    body = client.get("/365scores/transfers").json()
    assert body["disponible"] is False
    assert body["equipos"] == {}
    assert body["season"].startswith(("Apertura", "Clausura"))


# ---------- Impacto del XI confirmado (lineup-impact, 365Scores + BD) ----------


def _seed_lineup_impact_stats(db):
    """3 jugadores del equipo 1 (América) con produccion conocida (total = 10):
    501 -> 8 (goles+asist), 502 -> 2, 503 -> 0."""
    from app import models

    db.add(models.PlayerMatchStat(match_id=1, player_id=501, player_name="Estrella", team_id=1, team_name="América", season="Apertura 2026", goals=5, assists=3))
    db.add(models.PlayerMatchStat(match_id=1, player_id=502, player_name="Medio", team_id=1, team_name="América", season="Apertura 2026", goals=1, assists=1))
    db.add(models.PlayerMatchStat(match_id=1, player_id=503, player_name="Suplente", team_id=1, team_name="América", season="Apertura 2026", goals=0, assists=0))
    db.commit()


def test_365_lineup_impact(client, seeded, db, monkeypatch):
    from app.scrapers import scores365_scraper

    _seed_lineup_impact_stats(db)
    # XI: 502 y 503 arrancan; 501 (el mas importante) esta en la banca.
    fake = {
        "game_id": 123,
        "teams": [
            {
                "team_name": "Club América",
                "home_away": "home",
                "players": [
                    {"player_id": 501, "name": "Estrella", "starter": False},
                    {"player_id": 502, "name": "Medio", "starter": True},
                    {"player_id": 503, "name": "Suplente", "starter": True},
                ],
            },
        ],
    }
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_match_lineups", lambda self, game_id: fake)
    r = client.get("/365scores/matches/123/lineup-impact")
    assert r.status_code == 200
    body = r.json()
    assert body["disponible"] is True
    # Nombre normalizado a ESPN (via team_id detectado en la BD)
    ame = body["equipos"]["América"]
    # Titulares 502 (20%) + 503 (0%) => fuerza 20.0
    assert ame["fuerza_xi_pct"] == 20.0
    # 501 (80%) es el mas importante y NO arranca -> ausente clave
    assert {"jugador": "Estrella", "importancia_pct": 80.0} in ame["ausentes_clave"]
    # 502 arranca y esta en el top -> titular clave
    assert {"jugador": "Medio", "importancia_pct": 20.0} in ame["titulares_clave"]


def test_365_lineup_impact_sin_xi(client, seeded, monkeypatch):
    from app.scrapers import scores365_scraper

    # 365Scores aun no publica el XI (sin titulares) -> no disponible, sin inventar.
    fake = {
        "game_id": 123,
        "teams": [
            {"team_name": "Club América", "home_away": "home", "players": []},
        ],
    }
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "get_match_lineups", lambda self, game_id: fake)
    body = client.get("/365scores/matches/123/lineup-impact").json()
    assert body["disponible"] is False
    assert body["equipos"] == {}


# ---------- Robustez: frescura del sync y verificacion end-to-end del bot ----------


def test_sync_status_freshness_fresco(client, db):
    """Con una sync exitosa reciente, freshness.is_stale debe ser False."""
    from datetime import datetime
    from app import models

    db.add(
        models.SyncLog(
            source="espn", status="success", detail="ok", season="Apertura 2026", teams=18, players=500, matches=153, started_at=datetime.utcnow(), duration_seconds=42.0, finished_at=datetime.utcnow()
        )
    )
    db.commit()
    r = client.get("/sync/status").json()
    assert r["freshness"]["is_stale"] is False
    assert r["data_age_hours"] is not None and r["data_age_hours"] <= 1
    assert "frescos" in r["freshness"]["message"]


def test_bot_endpoints_end_to_end(client, seeded, db, monkeypatch):
    """Verificacion end-to-end: TODOS los endpoints que consume el bot Survivor
    responden 200 (con datos reales sembrados o `disponible: false` limpio),
    nunca 500. Las fuentes 365Scores se mockean (sin red)."""
    from app.scrapers import scores365_scraper as s365

    _seed_player_match_stats(db)  # Henry (id 999, equipo 1) y Rival X (id 998, equipo 2)

    # Mocks de 365Scores (sin red): transfers, porteros y alineaciones.
    monkeypatch.setattr(s365.Scores365Scraper, "get_transfers", lambda self, status=None, year=None: {"season": "Apertura 2026", "disponible": False, "equipos": {}})
    monkeypatch.setattr(s365.Scores365Scraper, "get_goalkeepers", lambda self: [])
    monkeypatch.setattr(
        s365.Scores365Scraper,
        "get_match_lineups",
        lambda self, game_id: {"game_id": game_id, "teams": [{"team_name": "América", "home_away": "home", "players": [{"player_id": 999, "name": "Henry Martín", "starter": True}]}]},
    )

    endpoints = [
        "/standings",
        "/calendar",
        "/predict?home=1&away=2",
        "/365scores/transfers",
        "/365scores/goalkeepers",
        "/matches/1/players-to-watch",
        "/365scores/matches/123/lineups",
        "/365scores/matches/123/lineup-impact",
        "/players/discipline",
        "/h2h/1/2/summary",
        "/news",
    ]
    for path in endpoints:
        r = client.get(path)
        assert r.status_code == 200, f"{path} devolvio {r.status_code}: {r.text[:200]}"
        # y su espejo /v1 tambien responde igual
        assert client.get("/v1" + path.split("?")[0]).status_code < 500


def test_bot_endpoints_pretemporada_sin_datos(client, monkeypatch):
    """En pretemporada (BD vacia) los endpoints NO deben dar 500: responden
    vacio o `disponible: false`, sin fabricar datos."""
    from app.scrapers import scores365_scraper as s365

    monkeypatch.setattr(s365.Scores365Scraper, "get_transfers", lambda self, status=None, year=None: {"season": "Apertura 2026", "disponible": False, "equipos": {}})
    monkeypatch.setattr(s365.Scores365Scraper, "get_goalkeepers", lambda self: [])
    monkeypatch.setattr(s365.Scores365Scraper, "get_match_lineups", lambda self, game_id: {"game_id": game_id, "teams": []})

    # Endpoints que no dependen de ids sembrados
    for path in ["/standings", "/calendar", "/365scores/transfers", "/365scores/goalkeepers", "/players/discipline", "/news", "/365scores/matches/123/lineup-impact"]:
        r = client.get(path)
        assert r.status_code < 500, f"{path} devolvio {r.status_code}"
    # lineup-impact sin XI -> disponible false, no inventa
    assert client.get("/365scores/matches/123/lineup-impact").json()["disponible"] is False


# ---------- H2H agregado en TODAS las temporadas ----------


def _seed_two_seasons_h2h(db):
    """2 temporadas con enfrentamientos América(227)–Pachuca(234) en cada una."""
    from datetime import datetime
    from app import models

    db.add(models.Season(id=1, name="Apertura 2024", year=2024, tournament_type="Apertura"))
    db.add(models.Season(id=2, name="Clausura 2025", year=2025, tournament_type="Clausura"))
    db.add(models.Team(id=227, name="América"))
    db.add(models.Team(id=234, name="Pachuca"))
    db.flush()

    def mk(sid, h, a, hs, as_, d):
        db.add(models.Match(season_id=sid, home_team_id=h, away_team_id=a, home_score=hs, away_score=as_, status="finished", match_date=d))

    mk(1, 227, 234, 2, 1, datetime(2024, 8, 1))  # América gana
    mk(1, 234, 227, 0, 0, datetime(2024, 11, 1))  # empate
    mk(2, 227, 234, 3, 0, datetime(2025, 2, 1))  # América gana
    mk(2, 234, 227, 1, 2, datetime(2025, 4, 1))  # América gana de visita
    db.commit()


def test_h2h_summary_agrega_todas_las_temporadas(client, db):
    _seed_two_seasons_h2h(db)
    r = client.get("/h2h/227/234/summary").json()
    assert r["played"] == 4  # antes solo contaba la temporada vigente
    assert r["team1"]["wins"] == 3  # 3 victorias de América en 2 temporadas
    assert r["team2"]["wins"] == 0
    assert r["draws"] == 1
    assert r["team1"]["goals"] == 7 and r["team2"]["goals"] == 2
    assert r["seasons_covered"] == 2
    # el listado tambien trae los 4
    assert len(client.get("/h2h/227/234").json()) == 4


def test_h2h_agrega_por_nombre_canonico_ids_duplicados(client, db):
    """Si un club aparece con team_id distinto en otra temporada (misma marca),
    el H2H debe agregarlos por nombre canonico y no perder partidos."""
    from datetime import datetime
    from app import models

    db.add(models.Season(id=1, name="Apertura 2024", year=2024, tournament_type="Apertura"))
    db.add(models.Team(id=227, name="América"))
    db.add(models.Team(id=9999, name="América"))  # duplicado con otro id
    db.add(models.Team(id=234, name="Pachuca"))
    db.flush()
    db.add(models.Match(season_id=1, home_team_id=227, away_team_id=234, home_score=1, away_score=0, status="finished", match_date=datetime(2024, 8, 1)))
    db.add(models.Match(season_id=1, home_team_id=234, away_team_id=9999, home_score=2, away_score=2, status="finished", match_date=datetime(2024, 9, 1)))
    db.commit()
    # Consultando por el id 227 se incluye tambien el partido del id 9999 (mismo nombre)
    r = client.get("/h2h/227/234/summary").json()
    assert r["played"] == 2
    assert r["team1"]["wins"] == 1 and r["draws"] == 1


def test_matches_sin_season_incluye_todas_las_temporadas(client, db):
    """GET /matches sin `season` debe devolver partidos de todas las temporadas,
    no solo la vigente."""
    from datetime import datetime
    from app import models

    db.add(models.Season(id=1, name="Apertura 2024", year=2024, tournament_type="Apertura"))
    db.add(models.Season(id=2, name="Apertura 2026", year=2026, tournament_type="Apertura"))
    db.add(models.Team(id=1, name="América"))
    db.add(models.Team(id=2, name="Pachuca"))
    db.flush()
    db.add(models.Match(season_id=1, home_team_id=1, away_team_id=2, home_score=1, away_score=0, status="finished", match_date=datetime(2024, 8, 1)))
    db.add(models.Match(season_id=2, home_team_id=2, away_team_id=1, home_score=2, away_score=2, status="finished", match_date=datetime(2026, 8, 1)))
    db.commit()
    r = client.get("/matches", params={"status": "finished", "limit": 100}).json()
    assert len(r) == 2  # una de cada temporada
    # filtrando por temporada, solo la pedida
    r1 = client.get("/matches", params={"status": "finished", "season": "Apertura 2024"}).json()
    assert len(r1) == 1


# ---------- Identidad/huella de la BD (diagnostico de entorno) ----------


def test_db_fingerprint_estable_y_sin_credenciales():
    from app.db_identity import db_fingerprint, db_target

    url = "postgresql://user:secretpass@ep-cool-123.us-east-2.aws.neon.tech/ligamx"
    fp = db_fingerprint(url)
    assert len(fp) == 12
    # No filtra credenciales
    assert "secretpass" not in fp and "user" not in fp
    # Estable: misma URL -> misma huella
    assert db_fingerprint(url) == fp
    # El endpoint pooled y el directo (misma base Neon) dan la MISMA huella
    pooled = "postgresql://user:secretpass@ep-cool-123-pooler.us-east-2.aws.neon.tech/ligamx"
    assert db_fingerprint(pooled) == fp
    # target sanitizado no incluye credenciales
    t = db_target(url)
    assert t["host"] == "ep-cool-123.us-east-2.aws.neon.tech" and t["dbname"] == "ligamx"


def test_db_fingerprint_distintas_bases_distinta_huella():
    from app.db_identity import db_fingerprint

    a = db_fingerprint("postgresql://u:p@host-a.neon.tech/ligamx")
    b = db_fingerprint("postgresql://u:p@host-b.neon.tech/ligamx")
    c = db_fingerprint("postgresql://u:p@host-a.neon.tech/otra")
    assert a != b and a != c


def test_sync_status_expone_fingerprint(client):
    r = client.get("/sync/status").json()
    assert "database" in r
    assert "fingerprint" in r["database"] and len(r["database"]["fingerprint"]) == 12


# ---------- XI probable/esperado (365Scores, solo dato real) ----------


def _fake_game_lineups(status_home, status_away):
    """Fabrica un game crudo de 365Scores con lineups en el estado dado."""
    members = [{"id": i, "name": f"Jugador {i}", "jerseyNumber": i} for i in range(1, 24)]

    def side(status, first_id):
        mem = [{"id": first_id + i, "status": 1, "position": {"name": "MF"}} for i in range(11)]
        mem += [{"id": first_id + 11 + i, "status": 2} for i in range(3)]  # suplentes
        return {"name": "Local" if first_id == 1 else "Visita", "lineups": {"status": status, "formation": "4-3-3", "members": mem}}

    return {
        "members": members,
        "homeCompetitor": side(status_home, 1),
        "awayCompetitor": side(status_away, 12),
    }


def test_365_probable_lineup_disponible(client, monkeypatch):
    from app.scrapers import scores365_scraper

    game = _fake_game_lineups("Sin confirmar", "NotConfirmed")  # ambos idiomas
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "_game_raw", lambda self, gid: game)
    r = client.get("/365scores/matches/123/probable-lineup")
    assert r.status_code == 200
    body = r.json()
    assert body["disponible"] is True
    assert body["fuente"] == "365scores"
    assert len(body["equipos"]) == 2
    eq = body["equipos"][0]
    assert eq["confirmada"] is False
    assert eq["condicion"] in ("local", "visitante")
    assert eq["formacion"] == "4-3-3"
    assert len(eq["titulares_probables"]) == 11  # solo titulares, no suplentes


def test_365_probable_lineup_ya_confirmada(client, monkeypatch):
    from app.scrapers import scores365_scraper

    game = _fake_game_lineups("Confirmada", "Confirmed")
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "_game_raw", lambda self, gid: game)
    body = client.get("/365scores/matches/123/probable-lineup").json()
    assert body["disponible"] is False
    assert body["equipos"] == []
    assert "confirmad" in body["motivo"].lower()


def test_365_probable_lineup_sin_alineacion(client, monkeypatch):
    from app.scrapers import scores365_scraper

    # Sin lineups aun (partido lejano): 365Scores no trae el campo
    game = {"members": [], "homeCompetitor": {"name": "Local"}, "awayCompetitor": {"name": "Visita"}}
    monkeypatch.setattr(scores365_scraper.Scores365Scraper, "_game_raw", lambda self, gid: game)
    body = client.get("/365scores/matches/123/probable-lineup").json()
    assert body["disponible"] is False
    assert "no publica" in body["motivo"].lower()
