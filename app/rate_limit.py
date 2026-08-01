"""Rate limiting por IP (slowapi).

Protege la API publica de abuso/scraping agresivo y limita las llamadas a los
endpoints que golpean fuentes externas. Configurable por entorno:
  - RATE_LIMIT_ENABLED: "true"/"false" (por defecto true; los tests lo apagan).
  - RATE_LIMIT_DEFAULT: limite global por IP (por defecto "200/minute").
  - RATE_LIMIT_SYNC: limite para los POST /sync* (por defecto "10/minute").
"""
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
DEFAULT_LIMIT = os.getenv("RATE_LIMIT_DEFAULT", "200/minute")
SYNC_LIMIT = os.getenv("RATE_LIMIT_SYNC", "10/minute")

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[DEFAULT_LIMIT],
    enabled=RATE_LIMIT_ENABLED,
    headers_enabled=True,  # expone X-RateLimit-* en las respuestas
)
