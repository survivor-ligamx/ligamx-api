"""Suspensiones: roja directa, ciclo de amarillas y la trampa del marcador 0-0.

La suite anterior solo cubria `suspension_risk` (el aviso de "esta a una
amarilla"), nunca `suspended_next_match` ("no puede jugar la proxima jornada").
Por eso paso en verde un arreglo que en produccion no marcaba a ningun
expulsado. Estos tests cubren ese hueco.
"""

from datetime import datetime


def _evento(db, match_id, tipo, jugador, team_id, minuto):
    from app import models

    db.add(
        models.MatchEvent(
            match_id=match_id,
            event_type=tipo,
            event_time=minuto,
            player_name=jugador,
            team_id=team_id,
            team_name="América" if team_id == 1 else "Chivas",
            description=tipo,
            is_home=1 if team_id == 1 else 0,
        )
    )
    db.commit()


def _roja(db, match_id, jugador, team_id=1, minuto=45):
    _evento(db, match_id, "red_card", jugador, team_id, minuto)


def _amarillas(db, match_id, jugador, cuantas, team_id=1):
    for i in range(cuantas):
        _evento(db, match_id, "yellow_card", jugador, team_id, 10 + i)


def _partido(db, match_id, fecha, status, home_score, away_score, week):
    from app import models

    db.add(
        models.Match(
            id=match_id,
            season_id=1,
            home_team_id=1,
            away_team_id=2,
            home_score=home_score,
            away_score=away_score,
            status=status,
            match_date=fecha,
            week_number=week,
            external_event_id=f"ESP{match_id}",
            espn_event_id=f"ESP{match_id}",
        )
    )
    db.commit()


def _por_nombre(payload):
    return {p["player"]: p for p in payload["players"]}


def test_roja_en_el_ultimo_partido_inhabilita_el_siguiente(client, seeded, db):
    _roja(db, 1, "Expulsado")
    p = _por_nombre(client.get("/players/discipline").json())["Expulsado"]
    assert p["red_cards"] == 1
    assert p["suspended_next_match"] is True
    assert p["suspension_reason"] == "roja"
    # Una roja NO es lo mismo que estar a una amarilla de suspenderse.
    assert p["suspension_risk"] is False


def test_partidos_futuros_con_marcador_cero_no_ocultan_la_suspension(client, seeded, db):
    """Regresion del bug real de produccion.

    Los partidos por jugar NO se guardan con marcador nulo: se guardan 0-0 con
    `status='scheduled'`. Detectar "partido jugado" mirando el marcador daba por
    disputada la temporada entera, el ultimo partido de cada equipo resultaba ser
    uno de la jornada 17 y ninguna expulsion llegaba a marcarse como suspension.
    """
    _roja(db, 1, "Expulsado")
    for semana in range(2, 6):
        _partido(db, match_id=semana, fecha=datetime(2026, 8, 1 + semana), status="scheduled", home_score=0, away_score=0, week=semana)
    r = client.get("/players/discipline", params={"unavailable": True}).json()
    assert [p["player"] for p in r["players"]] == ["Expulsado"]
    assert r["count"] == 1


def test_roja_ya_cumplida_deja_de_pesar(client, seeded, db):
    """Si el equipo jugo otro partido despues de la expulsion, el castigo ya se cumplio."""
    _roja(db, 1, "Expulsado")
    _partido(db, match_id=3, fecha=datetime(2026, 7, 27), status="finished", home_score=1, away_score=1, week=2)
    p = _por_nombre(client.get("/players/discipline").json())["Expulsado"]
    assert p["red_cards"] == 1
    assert p["suspended_next_match"] is False
    assert p["suspension_reason"] is None


def test_ciclo_de_cinco_amarillas_inhabilita(client, seeded, db):
    """Regla Liga MX: al llegar a 5 amarillas se cumple un partido de castigo."""
    _amarillas(db, 1, "Amarillento", 5)
    p = _por_nombre(client.get("/players/discipline").json())["Amarillento"]
    assert p["yellow_cards"] == 5
    assert p["suspended_next_match"] is True
    assert p["suspension_reason"] == "acumulacion de amarillas"


def test_cuatro_amarillas_avisan_pero_no_inhabilitan(client, seeded, db):
    _amarillas(db, 1, "Amarillento", 4)
    p = _por_nombre(client.get("/players/discipline").json())["Amarillento"]
    assert p["suspension_risk"] is True  # aviso preventivo
    assert p["suspended_next_match"] is False  # pero si puede jugar
    assert p["suspension_reason"] is None


def test_filtro_unavailable_solo_devuelve_inhabilitados(client, seeded, db):
    _roja(db, 1, "Expulsado")
    _amarillas(db, 1, "Amarillento", 4)
    nombres = {p["player"] for p in client.get("/players/discipline", params={"unavailable": True}).json()["players"]}
    assert nombres == {"Expulsado"}
    # el filtro preventivo es otro conjunto: el que esta a una amarilla
    riesgo = {p["player"] for p in client.get("/players/discipline", params={"at_risk": True}).json()["players"]}
    assert riesgo == {"Amarillento"}


def test_endpoint_individual_coincide_con_la_tabla(client, seeded, db):
    from app import models

    db.add(models.Player(id=30, team_id=1, name="Expulsado"))
    db.commit()
    _roja(db, 1, "Expulsado")
    individual = client.get("/players/30/discipline").json()
    tabla = _por_nombre(client.get("/players/discipline").json())["Expulsado"]
    assert individual["suspended_next_match"] == tabla["suspended_next_match"] is True
    assert individual["suspension_reason"] == tabla["suspension_reason"] == "roja"
