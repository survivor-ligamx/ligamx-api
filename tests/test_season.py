from datetime import datetime

from app.season import current_tournament, tournament_from_matches, current_season_name


def test_apertura_incluye_junio_a_diciembre():
    assert current_tournament(datetime(2026, 6, 15))[0] == "Apertura"
    assert current_tournament(datetime(2026, 7, 1))[0] == "Apertura"
    assert current_tournament(datetime(2026, 12, 31))[0] == "Apertura"


def test_clausura_enero_a_mayo():
    assert current_tournament(datetime(2026, 1, 10))[0] == "Clausura"
    assert current_tournament(datetime(2026, 5, 30))[0] == "Clausura"


def test_nombre_temporada():
    assert current_season_name(datetime(2026, 8, 1)) == "Apertura 2026"


def test_torneo_desde_partidos_reales():
    matches = [
        {"match_date": datetime(2026, 8, 1)},
        {"match_date": datetime(2026, 9, 1)},
        {"match_date": datetime(2026, 10, 1)},
        {"match_date": None},
    ]
    assert tournament_from_matches(matches) == ("Apertura", 2026)


def test_torneo_sin_fechas_no_truena():
    res = tournament_from_matches([])
    assert isinstance(res[0], str) and isinstance(res[1], int)



def test_to_naive_utc():
    from datetime import datetime, timezone
    from app.season import to_naive_utc
    aware = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
    n = to_naive_utc(aware)
    assert n.tzinfo is None and n == datetime(2026, 7, 1, 18, 0)
    assert to_naive_utc(None) is None
    naive = datetime(2026, 7, 1, 12, 0)
    assert to_naive_utc(naive) == naive  # naive se queda igual
