from datetime import datetime

from app.services.sync_service import merge_official_week_numbers


def _match(home: str, away: str, date: datetime, week, event_id=None):
    return {
        "event_id": event_id,
        "home_team": home,
        "away_team": away,
        "match_date": date,
        "week": week,
    }


def test_365_corrige_jornada_erronea_de_espn():
    espn = [
        _match("Cruz Azul", "Puebla", datetime(2026, 7, 22, 1, 0), 1, "401877036"),
        _match("Toluca", "Pumas UNAM", datetime(2026, 7, 22, 3, 5), 1, "401877035"),
    ]
    oficiales = [
        _match("Cruz Azul", "Puebla", datetime(2026, 7, 22, 1, 0), 2),
        _match("Toluca", "Pumas UNAM", datetime(2026, 7, 22, 3, 5), "2"),
    ]

    actualizados = merge_official_week_numbers(espn, oficiales)

    assert actualizados == 2
    assert [match["week"] for match in espn] == [2, 2]


def test_365_no_sobrescribe_si_no_hay_partido_equivalente():
    espn = [_match("Cruz Azul", "Puebla", datetime(2026, 7, 22, 1, 0), 1, "401877036")]
    oficiales = [_match("Tigres UANL", "Puebla", datetime(2026, 7, 22, 1, 0), 2)]

    actualizados = merge_official_week_numbers(espn, oficiales)

    assert actualizados == 0
    assert espn[0]["week"] == 1


def test_365_no_contamina_partidos_fuera_de_allowlist():
    espn = [_match("Necaxa", "Atlante", datetime(2026, 7, 17, 1, 0), 1, "401877045")]
    oficiales = [_match("Necaxa", "Atlante", datetime(2026, 7, 17, 1, 0), 2)]

    actualizados = merge_official_week_numbers(espn, oficiales)

    assert actualizados == 0
    assert espn[0]["week"] == 1


def test_365_rechaza_jornada_invalida_y_fecha_lejana():
    espn = [_match("Cruz Azul", "Puebla", datetime(2026, 7, 22, 1, 0), 1, "401877036")]
    oficiales = [
        _match("Cruz Azul", "Puebla", datetime(2026, 7, 22, 1, 0), None),
        _match("Cruz Azul", "Puebla", datetime(2026, 8, 22, 1, 0), 4),
    ]

    actualizados = merge_official_week_numbers(espn, oficiales)

    assert actualizados == 0
    assert espn[0]["week"] == 1
