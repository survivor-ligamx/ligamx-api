from pathlib import Path

p = Path('app/routers/analytics.py')
s = p.read_text()
s = s.replace('def _reduce_outcome_extremes(probabilities, confidence: float):', 'def _temper_outcome_extremes(probabilities, confidence: float):')
s = s.replace('def _match_sample_stats(db: Session):\n    rows = (\n        db.query(models.Match)\n        .filter(', '''def _match_sample_stats(db: Session, season_id: int | None):
    query = db.query(models.Match)
    # Nunca mezclar temporadas: evita que una predicción histórica vea partidos
    # posteriores. El endpoint no conoce una fecha de fixture, por lo que usa
    # únicamente los partidos ya terminados del torneo solicitado.
    if season_id is not None:
        query = query.filter(models.Match.season_id == season_id)
    rows = (
        query
        .filter(''')
s = s.replace("    prior_mean = side_mean if context_name != 'overall' else league['overall_avg']\n", "    # Aun con contexto overall, el prior conserva la sede; de otro modo la\n    # ventaja local desaparecía precisamente cuando la muestra era pequeña.\n    prior_mean = side_mean\n")
s = s.replace('    team_stats, league = _match_sample_stats(db)\n', '    team_stats, league = _match_sample_stats(db, season_id)\n')
s = s.replace("    calibrated = _reduce_outcome_extremes({'home_win': home_win, 'draw': draw, 'away_win': away_win}, confidence)\n", "    tempered = _temper_outcome_extremes({'home_win': home_win, 'draw': draw, 'away_win': away_win}, confidence)\n")
s = s.replace('calibrated[', 'tempered[')
s = s.replace('            "Fallback a medias de liga cuando hay pocos o nulos datos historicos",\n', '            "Fallback a medias de liga cuando hay pocos o nulos datos historicos",\n            "Tempering heuristico de extremos; no es calibracion empirica",\n')
s = s.replace('        "prior_strength": round(prior_strength, 2),\n', '        "prior_strength": round(prior_strength, 2),\n        "tempering": {"applied": True, "method": "confidence_aware_cap", "empirically_calibrated": False},\n')
assert 'def _match_sample_stats(db: Session, season_id: int | None):' in s
assert 'def _temper_outcome_extremes' in s
assert 'prior_mean = side_mean\n' in s
assert '_match_sample_stats(db, season_id)' in s
p.write_text(s)

t = Path('tests/test_api.py')
ts = t.read_text()
needle = '''    assert r["expected_goals"]["home"] > 0
    assert r["expected_goals"]["away"] > 0
'''
replacement = '''    assert r["expected_goals"]["home"] > 0
    assert r["expected_goals"]["away"] > 0
    # Con equipos neutrales y sin muestra, el prior debe conservar la localía.
    assert p["home_win"] > p["away_win"]
    assert r["tempering"]["empirically_calibrated"] is False
'''
assert needle in ts
ts = ts.replace(needle, replacement, 1)
t.write_text(ts)

Path('tools/apply_manual_review_fixes.py').unlink()
Path('.github/workflows/apply-manual-review-fixes.yml').unlink()
