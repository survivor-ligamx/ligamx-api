"""Cruce de identidad de jugadores ESPN <-> 365Scores.

Las fichas de jugadores vienen de ESPN (`players.id`) y las estadisticas por
partido de 365Scores (`player_match_stats.player_id`). Los ids NO coinciden entre
fuentes, asi que historicamente se emparejaba por NOMBRE (fragil ante nombres
cortos, apodos, acentos u homonimos).

Este modulo construye un mapa fiable rellenando `players.external_365_id` con el
id de 365Scores correspondiente. La clave para que sea robusto: emparejamos SOLO
DENTRO DEL MISMO EQUIPO (player_match_stats.team_id ya es el id de equipo de
nuestra BD), donde los apellidos son practicamente unicos.
"""

import logging
import unicodedata

from app import models
from typing import Optional

logger = logging.getLogger("ligamx.identity")

# Umbral minimo de confianza para aceptar un emparejamiento automatico.
_MATCH_THRESHOLD = 0.5
# Margen minimo entre el mejor y el segundo mejor candidato (evita homonimos).
_AMBIGUITY_MARGIN = 0.15


def _norm(s: str) -> str:
    """Minusculas sin acentos."""
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _tokens(name: str) -> list[str]:
    """Tokens del nombre (los puntos de iniciales se tratan como separador)."""
    return [t for t in _norm(name).replace(".", " ").replace("-", " ").split() if t]


def _initial_hit(a: list[str], b: list[str]) -> bool:
    """True si algun token de 1 letra en `a` coincide con la inicial de un token
    de `b` (caso 'J.' <-> 'Julian')."""
    for ta in a:
        if len(ta) == 1:
            for tb in b:
                if tb and tb[0] == ta and tb != ta:
                    return True
    return False


def name_match_score(name_a: str, name_b: str) -> float:
    """Puntua de 0 a 1 el parecido entre dos nombres de persona del MISMO equipo.

    Jerarquia (pensada para nombres del mismo plantel, donde el apellido casi
    siempre identifica):
      1.0  igualdad exacta
      0.95 mismo apellido + (mismo nombre de pila o inicial que coincide)
      0.9  comparten 2+ tokens (nombre y apellido) aunque haya tokens extra
      0.6  solo coincide el apellido (sin info de nombre que confirme)
      0.45 comparten un token (no el apellido)
      0.3  solo coincide una inicial
      0.0  sin relacion
    """
    a, b = _tokens(name_a), _tokens(name_b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    shared = len(sa & sb)
    first_eq = a[0] == b[0]
    last_eq = a[-1] == b[-1]
    initial = _initial_hit(a, b) or _initial_hit(b, a)
    if last_eq and (first_eq or initial):
        return 0.95
    if shared >= 2:
        return 0.9
    if last_eq:
        return 0.6
    if shared == 1:
        return 0.45
    if initial:
        return 0.3
    return 0.0


def _best_match(src_name: str, candidates: list[models.Player]):
    """Devuelve (mejor_player, score, es_inequivoco) para un nombre de 365Scores
    contra los jugadores ESPN de su equipo."""
    scored = sorted(
        ((name_match_score(src_name, p.name), p) for p in candidates),  # type: ignore[arg-type]
        key=lambda t: t[0],
        reverse=True,
    )
    if not scored:
        return None, 0.0, False
    best_score, best_player = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    unambiguous = best_score >= 1.0 or (best_score - second) >= _AMBIGUITY_MARGIN
    return best_player, best_score, unambiguous


def build_player_identity_map(db, season: Optional[str] = None, force: bool = False) -> dict:
    """Rellena `players.external_365_id` cruzando por nombre+equipo contra
    `player_match_stats`. Idempotente. Devuelve un resumen del resultado.

    - Solo considera filas con player_id (id de 365Scores) y team_id.
    - Empareja dentro del mismo equipo; acepta el match si supera el umbral y es
      inequivoco (margen sobre el segundo candidato).
    - RESPETA los enlaces ya existentes (p. ej. puestos a mano); no los sobrescribe
      salvo que `force=True`.
    """
    M = models.PlayerMatchStat
    q = db.query(M.player_id, M.player_name, M.team_id).distinct()
    if season:
        q = q.filter(M.season == season)
    sources = [(pid, name, tid) for (pid, name, tid) in q.all() if pid is not None and tid is not None]

    # Jugadores ESPN agrupados por equipo.
    players_by_team: dict[int, list[models.Player]] = {}
    for p in db.query(models.Player).all():
        players_by_team.setdefault(p.team_id, []).append(p)

    # Enlaces ya existentes: se preservan (a menos que force). Sus jugadores e ids
    # de 365 quedan "reservados" para no pisarlos.
    preserved = 0
    taken: dict[int, set] = {}  # team_id -> set de player.id ya asignados
    used_pids: set = set()  # ids de 365 ya enlazados
    for p_list in players_by_team.values():
        for p in p_list:
            if p.external_365_id is not None and not force:
                taken.setdefault(p.team_id, set()).add(p.id)
                used_pids.add(p.external_365_id)
                preserved += 1

    mapped = 0
    unmatched: list[str] = []

    # Procesamos primero los matches mas seguros (orden por mejor score) para que
    # los emparejamientos exactos "reserven" su jugador antes que los dudosos.
    scored_sources = []
    for pid, name, tid in sources:
        cands = players_by_team.get(tid, [])
        player, score, ok = _best_match(name, cands)
        scored_sources.append((score, ok, pid, name, tid, player))
    scored_sources.sort(key=lambda t: t[0], reverse=True)

    for score, ok, pid, name, tid, player in scored_sources:
        if pid in used_pids:
            continue  # ese id de 365 ya esta enlazado (manual o previo)
        if not player or score < _MATCH_THRESHOLD or not ok:
            unmatched.append(name)
            continue
        used = taken.setdefault(tid, set())
        if player.id in used:
            # jugador ya asignado (enlace manual/previo u homonimo) -> sin asignar
            unmatched.append(name)
            continue
        player.external_365_id = pid
        db.add(player)
        used.add(player.id)
        used_pids.add(pid)
        mapped += 1

    db.commit()
    total_sources = len(sources)
    return {
        "season": season,
        "sources_365": total_sources,
        "mapped": mapped,
        "preserved": preserved,
        "unmatched": len(unmatched),
        "unmatched_names": sorted(set(unmatched))[:50],
    }
