from datetime import datetime

from app.services.sync_service import calculate_week_numbers


def _match(date: datetime, week=None):
    return {"match_date": date, "week": week}


def test_preserva_jornada_oficial_en_fecha_doble():
    matches = [
        _match(datetime(2026, 7, 18), 1),
        _match(datetime(2026, 7, 21), 2),
        _match(datetime(2026, 7, 24), "2"),
    ]

    calculate_week_numbers(matches)

    assert [match["week"] for match in matches] == [1, 2, 2]


def test_infiere_por_fecha_solo_cuando_falta_week():
    matches = [
        _match(datetime(2026, 7, 18)),
        _match(datetime(2026, 7, 25)),
    ]

    calculate_week_numbers(matches)

    assert [match["week"] for match in matches] == [1, 2]


def test_faltante_hereda_jornada_oficial_de_su_ventana():
    matches = [
        _match(datetime(2026, 7, 25), 4),
        _match(datetime(2026, 7, 26)),
    ]

    calculate_week_numbers(matches)

    assert [match["week"] for match in matches] == [4, 4]
