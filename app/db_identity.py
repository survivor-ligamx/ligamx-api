"""Identidad NO sensible de la base de datos, para diagnosticar a que BD apunta
cada entorno (web service vs GitHub Actions) sin exponer credenciales.

La "huella" (fingerprint) es un hash corto de host+dbname (sin usuario ni
password). Si dos entornos muestran la MISMA huella, apuntan a la misma base.
Esto permite verificar que el secret DATABASE_URL de Actions coincide con el
DATABASE_URL del web service, sin filtrar la cadena de conexion.
"""
import hashlib
import os
from urllib.parse import urlparse
from typing import Optional


def _normalize(url: str):
    """Devuelve (dialect, host, dbname) de una URL de conexion. Normaliza el
    host de Neon quitando el sufijo '-pooler' para que el endpoint pooled y el
    directo (misma base) den la misma huella."""
    if not url:
        return ("unknown", None, None)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    parsed = urlparse(url)
    dialect = (parsed.scheme or "unknown").split("+")[0]
    if dialect.startswith("sqlite"):
        # sqlite:///./x.db -> dbname = ruta del archivo
        return ("sqlite", None, (parsed.path or "").lstrip("/") or None)
    host = (parsed.hostname or "").lower()
    # Neon: 'ep-xxx-pooler.region.neon.tech' y 'ep-xxx.region.neon.tech' son la
    # misma base; unificamos quitando '-pooler'.
    host = host.replace("-pooler.", ".")
    dbname = (parsed.path or "").lstrip("/") or None
    return (dialect, host or None, dbname)


def db_target(url: Optional[str] = None) -> dict:
    """Objetivo de la BD en claro pero SIN credenciales (dialect/host/dbname).
    Apto para logs de CI (privados), no para respuestas publicas."""
    url = url if url is not None else os.environ.get("DATABASE_URL", "sqlite:///./ligamx.db")
    dialect, host, dbname = _normalize(url)
    return {"dialect": dialect, "host": host, "dbname": dbname}


def db_fingerprint(url: Optional[str] = None) -> str:
    """Huella corta y estable de host+dbname (sin credenciales). Segura para
    exponer en una API publica: no revela host ni usuario, solo permite comparar
    si dos entornos apuntan a la misma base."""
    dialect, host, dbname = _normalize(
        url if url is not None else os.environ.get("DATABASE_URL", "sqlite:///./ligamx.db")
    )
    raw = f"{dialect}://{host or ''}/{dbname or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
