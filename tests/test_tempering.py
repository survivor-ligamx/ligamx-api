"""El recorte de extremos debe respetar su propio techo.

El bug: `_temper_outcome_extremes` recortaba al cap y luego renormalizaba
dividiendo entre la suma. Como la suma quedaba por debajo de 1, el valor
recortado volvia a subir por encima del cap que la funcion prometia.
"""

from app.routers.analytics import _outcome_cap, _temper_outcome_extremes

EPS = 1e-9


def test_el_valor_recortado_no_vuelve_a_subir():
    # El caso exacto del bug: 0.82 con cap 0.75 terminaba devolviendo 0.806.
    out = _temper_outcome_extremes({"home_win": 0.82, "draw": 0.12, "away_win": 0.06}, 0.0)
    assert out["home_win"] <= _outcome_cap(0.0) + EPS


def test_sigue_sumando_uno():
    out = _temper_outcome_extremes({"home_win": 0.82, "draw": 0.12, "away_win": 0.06}, 0.0)
    assert abs(sum(out.values()) - 1.0) < EPS


def test_el_excedente_se_reparte_entre_los_demas():
    out = _temper_outcome_extremes({"home_win": 0.90, "draw": 0.06, "away_win": 0.04}, 0.0)
    assert out["home_win"] <= _outcome_cap(0.0) + EPS
    assert out["draw"] > 0.06
    assert out["away_win"] > 0.04
    # El reparto es proporcional, asi que se conserva quien iba adelante.
    assert out["draw"] > out["away_win"]


def test_sin_extremos_solo_normaliza():
    entrada = {"home_win": 0.5, "draw": 0.3, "away_win": 0.2}
    out = _temper_outcome_extremes(entrada, 0.0)
    for clave, valor in entrada.items():
        assert abs(out[clave] - valor) < EPS


def test_entrada_sin_normalizar_se_normaliza():
    out = _temper_outcome_extremes({"home_win": 5.0, "draw": 3.0, "away_win": 2.0}, 1.0)
    assert abs(sum(out.values()) - 1.0) < EPS
    assert abs(out["home_win"] - 0.5) < EPS


def test_mas_confianza_permite_un_techo_mas_alto():
    entrada = {"home_win": 0.92, "draw": 0.05, "away_win": 0.03}
    bajo = _temper_outcome_extremes(entrada, 0.0)
    alto = _temper_outcome_extremes(entrada, 1.0)
    assert alto["home_win"] > bajo["home_win"]
    assert bajo["home_win"] <= _outcome_cap(0.0) + EPS
    assert alto["home_win"] <= _outcome_cap(1.0) + EPS


def test_todo_en_cero_no_revienta():
    out = _temper_outcome_extremes({"home_win": 0.0, "draw": 0.0, "away_win": 0.0}, 0.5)
    assert abs(sum(out.values()) - 1.0) < EPS
    assert all(abs(v - (1.0 / 3.0)) < EPS for v in out.values())


def test_diccionario_vacio_no_revienta():
    assert _temper_outcome_extremes({}, 0.5) == {}


def test_el_endpoint_predict_respeta_el_cap(client, seeded):
    r = client.get("/predict", params={"home": 1, "away": 2}).json()
    cap = _outcome_cap(r["confidence"])
    for valor in r["probabilities"].values():
        # +0.001 por el redondeo a tres decimales de la respuesta.
        assert valor <= cap + 0.001
