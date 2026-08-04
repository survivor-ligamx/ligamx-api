from datetime import datetime

from app.services.sync_service import apply_known_week_overrides


def _match(home: str, away: str, date: datetime, week, event_id=None):
    return {
        "event_id": event_id,
        "home_team": home,
        "away_team": away,
        "match_date": date,
        "week": week,
    }


def test_known_overrides_corrigen_j1_y_adelantados_de_j2():
    matches = [
        _match("Necaxa", "Atlante", datetime(2026, 7, 17, 1, 0), 2, "401877045"),
        _match("Tijuana", "Tigres UANL", datetime(2026, 7, 17, 3, 10), 2, "401877044"),
        _match("Atlético de San Luis", "Cruz Azul", datetime(2026, 7, 18, 1, 0), 2, "401877043"),
        _match("Cruz Azul", "Puebla", datetime(2026, 7, 22, 1, 0), 1, "401877036"),
        _match("Toluca", "Pumas UNAM", datetime(2026, 7, 22, 3, 5), 1, "401877035"),
    ]

    actualizados = apply_known_week_overrides(matches)

    assert actualizados == 5
    assert [match["week"] for match in matches] == [1, 1, 1, 2, 2]


def test_known_overrides_cubre_toda_la_jornada_1_de_apertura_2026():
    matches = [
        _match("Necaxa", "Atlante", datetime(2026, 7, 17, 1, 0), 2, "401877045"),
        _match("Tijuana", "Tigres UANL", datetime(2026, 7, 17, 3, 10), 2, "401877044"),
        _match("Atlético de San Luis", "Cruz Azul", datetime(2026, 7, 18, 1, 0), 2, "401877043"),
        _match("León", "Atlas", datetime(2026, 7, 18, 1, 0), 2, "401877042"),
        _match("FC Juarez", "Puebla", datetime(2026, 7, 18, 3, 0), 2, "401877041"),
        _match("Pumas UNAM", "Pachuca", datetime(2026, 7, 18, 23, 0), 2, "401877040"),
        _match("Monterrey", "Santos", datetime(2026, 7, 19, 1, 5), 2, "401877038"),
        _match("Guadalajara", "Toluca", datetime(2026, 7, 19, 1, 7), 2, "401877039"),
        _match("Querétaro", "América", datetime(2026, 7, 19, 3, 10), 2, "401877037"),
    ]

    actualizados = apply_known_week_overrides(matches)

    assert actualizados == 9
    assert all(match["week"] == 1 for match in matches)


def test_known_overrides_no_toca_partidos_fuera_del_mapa():
    matches = [
        _match("Necaxa", "Monterrey", datetime(2026, 7, 26, 23, 0), 2, "401877029"),
        _match("Puebla", "Guadalajara", datetime(2026, 8, 1, 1, 0), 3, "401877027"),
    ]

    actualizados = apply_known_week_overrides(matches)

    assert actualizados == 0
    assert [match["week"] for match in matches] == [2, 3]
