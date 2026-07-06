"""Tests del router de momios (histórico). Usa las fixtures de conftest."""


def _snapshot():
    return {
        "home_team": "América", "away_team": "Chivas", "season": "Apertura 2026",
        "source": "odds-api.io",
        "odds_local": 1.80, "odds_empate": 3.40, "odds_visita": 4.20,
        "ou_linea": 2.5, "odds_over": 1.90, "odds_under": 1.90,
    }


def test_post_odds_requiere_api_key(client):
    # Sin X-API-Key -> 422 (header requerido).
    r = client.post("/odds", json=[_snapshot()])
    assert r.status_code == 422


def test_post_odds_key_invalida(client):
    r = client.post("/odds", json=[_snapshot()], headers={"X-API-Key": "mala"})
    assert r.status_code == 403


def test_post_y_get_odds(client):
    r = client.post("/odds", json=[_snapshot()], headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json()["guardados"] == 1

    r2 = client.get("/odds", params={"season": "Apertura 2026"})
    assert r2.status_code == 200
    data = r2.json()
    assert len(data) == 1
    assert data[0]["home_team"] == "América"
    assert data[0]["odds_local"] == 1.80
    assert data[0]["captured_at"] is not None


def test_get_odds_filtra_por_equipo(client):
    client.post("/odds", json=[_snapshot()], headers={"X-API-Key": "test-key"})
    assert client.get("/odds", params={"home_team": "América"}).json()
    assert client.get("/odds", params={"home_team": "Toluca"}).json() == []


def test_stadium_expone_altitud(client, seeded):
    r = client.get("/stadiums")
    assert r.status_code == 200
    # El campo altitude_m debe existir en el contrato (aunque sea None).
    assert "altitude_m" in r.json()[0]
