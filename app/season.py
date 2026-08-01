"""Utilidades de temporada para Liga MX.

Liga MX juega DOS torneos por ano civil:
  - Clausura: ~enero a mayo/junio
  - Apertura: ~julio a diciembre

Como ambos torneos caen en el mismo ano, identificar la temporada solo por el
ano (p. ej. "2026") es ambiguo. Estas funciones resuelven el torneo vigente a
partir del mes, para etiquetar los datos de forma clara (p. ej. "Apertura 2026").
"""
from datetime import datetime, timezone
from typing import Optional


def to_naive_utc(dt):
    """Normaliza un datetime a UTC *naive*. Evita el crash al comparar/restar
    fechas tz-aware (p. ej. de datos viejos en Postgres) con datetime.utcnow()."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def current_tournament(now: Optional[datetime] = None):
    """Devuelve (tipo_de_torneo, ano) vigente o proximo segun la fecha.

    Calendario Liga MX:
      - Clausura: enero a mayo
      - Apertura: julio a diciembre
      - Junio: receso; el torneo que viene es el Apertura.

    Por eso usamos meses 6-12 => Apertura, 1-5 => Clausura.
    """
    now = now or datetime.now(timezone.utc)
    if now.month >= 6:
        return "Apertura", now.year
    return "Clausura", now.year


def tournament_from_matches(matches):
    """Deduce (torneo, ano) a partir de las fechas REALES de los partidos
    cargados (lo mas robusto). Si no hay fechas, cae a current_tournament()."""
    from collections import Counter
    dated = [m.get("match_date") for m in matches if m.get("match_date")]
    if not dated:
        return current_tournament()
    counts = Counter(current_tournament(d) for d in dated)
    return counts.most_common(1)[0][0]


def current_season_year(now: Optional[datetime] = None) -> str:
    """Ano de la temporada vigente como string (clave usada en stats)."""
    return str(current_tournament(now)[1])


def current_season_name(now: Optional[datetime] = None) -> str:
    """Nombre completo de la temporada vigente, p. ej. 'Apertura 2026'."""
    tournament, year = current_tournament(now)
    return f"{tournament} {year}"
