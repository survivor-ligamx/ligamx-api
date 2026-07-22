"""Tests de endpoints (smoke + aserciones clave) usando la BD seeded.

Cubre los routers DB-backed via TestClient para subir la cobertura sin tocar la red.
Los endpoints que requieren datos no presentes en el seed se prueban como smoke
(status < 500), lo que igualmente cubre el codigo del router.
"""


# --- Meta / health ---
def test_health(client):
    assert client.get("/health").status_code == 200


def test_health_ready(client):
    assert client.get("/health/ready").status_code == 200


def test_root(client):
    assert client.get("/").status_code == 200


def test_metrics(client):
    assert client.get("/metrics").status_code == 200


def test_season(client, seeded):
    assert client.get("/season").status_code < 500


# --- Teams ---
def test_teams_list(client, seeded):
    r = client.get("/teams")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_teams_search(client, seeded):
    assert client.get("/teams/search", params={"q": "America"}).status_code == 200


def test_team_detail(client, seeded):
    r = client.get("/teams/1")
    assert r.status_code == 200
    assert r.json()["name"] == "América"


def test_team_not_found(client, seeded):
    assert client.get("/teams/999").status_code == 404


def test_team_players(client, seeded):
    assert client.get("/teams/1/players").status_code == 200


def test_team_last_matches(client, seeded):
    assert client.get("/teams/1/last-matches").status_code == 200


def test_team_stats(client, seeded):
    assert client.get("/teams/1/stats").status_code < 500


def test_team_season_stats(client, seeded):
    assert client.get("/teams/1/season-stats").status_code < 500


def test_team_form(client, seeded):
    assert client.get("/teams/1/form").status_code < 500


def test_team_discipline(client, seeded):
    assert client.get("/teams/1/discipline").status_code < 500


def test_team_streak(client, seeded):
    assert client.get("/teams/1/streak").status_code < 500


def test_team_profile(client, seeded):
    assert client.get("/teams/1/profile").status_code < 500


def test_teams_xg_performance(client, seeded):
    assert client.get("/teams/xg-performance").status_code < 500


def test_teams_assets(client, seeded):
    assert client.get("/teams/assets").status_code < 500


# --- Standings ---
def test_seasons(client, seeded):
    assert client.get("/seasons").status_code == 200


def test_standings(client, seeded):
    r = client.get("/standings")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_standings_projection(client, seeded):
    assert client.get("/standings/projection").status_code < 500


def test_top_scorers(client, seeded):
    assert client.get("/top-scorers").status_code == 200


def test_liguilla(client, seeded):
    assert client.get("/liguilla").status_code < 500


def test_liguilla_results(client, seeded):
    assert client.get("/liguilla/results").status_code < 500


def test_liguilla_bracket(client, seeded):
    assert client.get("/liguilla/bracket").status_code < 500


def test_seasons_compare(client, seeded):
    assert client.get("/seasons/compare").status_code < 500


# --- Players ---
def test_players_list(client, seeded):
    assert client.get("/players").status_code == 200


def test_players_top(client, seeded):
    assert client.get("/players/top").status_code == 200


def test_players_search(client, seeded):
    assert client.get("/players/search", params={"query": "Henry"}).status_code == 200


def test_player_detail(client, seeded):
    assert client.get("/players/10").status_code == 200


def test_player_stats(client, seeded):
    assert client.get("/players/10/stats").status_code < 500


def test_player_match_stats(client, seeded):
    assert client.get("/players/10/match-stats").status_code < 500


def test_player_season_stats(client, seeded):
    assert client.get("/players/10/season-stats").status_code < 500


def test_player_discipline(client, seeded):
    assert client.get("/players/10/discipline").status_code < 500


def test_player_form(client, seeded):
    assert client.get("/players/10/form").status_code < 500


def test_player_profile(client, seeded):
    assert client.get("/players/10/profile").status_code < 500


def test_players_leaderboard(client, seeded):
    assert client.get("/players/leaderboard").status_code < 500


def test_players_season_leaders(client, seeded):
    assert client.get("/players/season-leaders").status_code < 500


def test_players_identity_map(client, seeded):
    assert client.get("/players/identity-map").status_code < 500


def test_players_xg_performance(client, seeded):
    assert client.get("/players/xg-performance").status_code < 500


# --- Matches ---
def test_matches_list(client, seeded):
    r = client.get("/matches")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_match_detail(client, seeded):
    assert client.get("/matches/1").status_code == 200


def test_matches_by_team(client, seeded):
    assert client.get("/matches/team/1").status_code == 200


def test_matches_by_week(client, seeded):
    assert client.get("/matches/week/1").status_code == 200


def test_matches_upcoming(client, seeded):
    assert client.get("/matches/upcoming").status_code == 200


def test_matches_today(client, seeded):
    assert client.get("/matches/today").status_code < 500


def test_h2h(client, seeded):
    assert client.get("/h2h/1/2").status_code == 200


def test_h2h_summary(client, seeded):
    assert client.get("/h2h/1/2/summary").status_code < 500


def test_match_events(client, seeded):
    assert client.get("/matches/1/events").status_code == 200


def test_match_lineups(client, seeded):
    assert client.get("/matches/1/lineups").status_code == 200


def test_match_stats(client, seeded):
    assert client.get("/matches/1/stats").status_code < 500


def test_match_cards(client, seeded):
    assert client.get("/matches/1/cards").status_code < 500


def test_match_timeline(client, seeded):
    assert client.get("/matches/1/timeline").status_code == 200


def test_match_squad(client, seeded):
    assert client.get("/matches/1/squad").status_code < 500


def test_match_player_stats(client, seeded):
    assert client.get("/matches/1/player-stats").status_code < 500


def test_match_full(client, seeded):
    assert client.get("/matches/1/full").status_code < 500


def test_match_players_to_watch(client, seeded):
    assert client.get("/matches/1/players-to-watch").status_code < 500


def test_weeks(client, seeded):
    assert client.get("/weeks").status_code < 500


def test_weeks_current(client, seeded):
    assert client.get("/weeks/current").status_code < 500


# --- Stadiums ---
def test_stadiums_list(client, seeded):
    assert client.get("/stadiums").status_code == 200


def test_stadium_detail(client, seeded):
    assert client.get("/stadiums/1").status_code == 200


# --- Search / Overview / Stats ---
def test_search(client, seeded):
    assert client.get("/search", params={"q": "America"}).status_code < 500


def test_dashboard(client, seeded):
    assert client.get("/dashboard").status_code < 500


def test_player_stats_endpoint(client, seeded):
    assert client.get("/player-stats").status_code == 200


# --- Analytics (smoke, algunos necesitan mas datos) ---
def test_compare_teams(client, seeded):
    assert client.get("/compare/teams", params={"team_ids": "1,2"}).status_code < 500


def test_compare_players(client, seeded):
    assert client.get("/compare/players", params={"player_ids": "10"}).status_code < 500


def test_power_ranking(client, seeded):
    assert client.get("/power-ranking").status_code < 500


def test_predict(client, seeded):
    assert client.get("/predict", params={"match_id": "1"}).status_code < 500


# --- v1 prefix (misma API montada dos veces) ---
def test_v1_teams(client, seeded):
    assert client.get("/v1/teams").status_code == 200


def test_v1_standings(client, seeded):
    assert client.get("/v1/standings").status_code == 200
