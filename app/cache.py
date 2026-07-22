"""Cache con TTL para endpoints "en vivo", con backend opcional de Redis.

Por defecto usa un cache EN PROCESO (un dict por worker), suficiente para 1
worker. Si se define `REDIS_URL`, usa Redis como backend COMPARTIDO entre workers
(serializando los valores a JSON). Si Redis no esta disponible, cae de forma
transparente al cache en proceso, asi que nunca rompe.

Los endpoints cacheados devuelven datos JSON-serializables (dicts/listas de los
scrapers), por lo que el ida y vuelta por Redis es seguro.
"""
import time
import threading
import functools
import os
import json
import hashlib
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

_store: dict[str, Any] = {}
_lock = threading.Lock()
_PREFIX = "ligamx:cache:"

# Backend Redis opcional (solo si REDIS_URL esta definido y conecta)
_redis = None
REDIS_URL = os.getenv("REDIS_URL")
if REDIS_URL:
    try:
        import redis as _redis_lib
        _redis = _redis_lib.from_url(REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
        _redis.ping()
        logger.info("Cache: backend Redis activo")
    except Exception as e:  # pragma: no cover (depende del entorno)
        logger.warning(f"Cache: Redis no disponible ({e}); se usa cache en proceso")
        _redis = None


def _make_key(fn, args, kwargs) -> str:
    raw = f"{fn.__module__}.{fn.__qualname__}:{args}:{tuple(sorted(kwargs.items()))}"
    return _PREFIX + hashlib.md5(raw.encode()).hexdigest()


def cached(ttl: int) -> Callable:
    """Decorador: cachea el resultado de la funcion durante `ttl` segundos.
    Los argumentos deben ser hashables/serializables (str/int/None, como los
    query params)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _make_key(fn, args, kwargs)

            # --- Backend Redis (compartido) ---
            if _redis is not None:
                try:
                    hit = _redis.get(key)
                    if hit is not None:
                        return json.loads(hit)
                except Exception as e:  # pragma: no cover
                    logger.warning(f"Redis get fallo: {e}")
                value = fn(*args, **kwargs)
                try:
                    _redis.set(key, json.dumps(value, default=str), ex=ttl)
                except Exception as e:  # pragma: no cover
                    logger.warning(f"Redis set fallo: {e}")
                return value

            # --- Cache en proceso (por defecto) ---
            now = time.time()
            with _lock:
                entry = _store.get(key)
                if entry and entry[0] > now:
                    return entry[1]
            value = fn(*args, **kwargs)
            with _lock:
                _store[key] = (now + ttl, value)
            return value

        wrapper.__wrapped__ = fn
        return wrapper
    return decorator


def clear_cache():
    """Vacia el cache (util en tests). Limpia en proceso y, si aplica, Redis."""
    with _lock:
        _store.clear()
    if _redis is not None:  # pragma: no cover
        try:
            for k in _redis.scan_iter(_PREFIX + "*"):
                _redis.delete(k)
        except Exception as e:
            logger.warning(f"Redis clear fallo: {e}")


def cache_stats():
    """Estado del cache (para /metrics)."""
    if _redis is not None:  # pragma: no cover
        try:
            n = sum(1 for _ in _redis.scan_iter(_PREFIX + "*"))
            return {"backend": "redis", "entries": n, "live": n, "expired": 0}
        except Exception:
            pass
    now = time.time()
    with _lock:
        live = sum(1 for exp, _ in _store.values() if exp > now)
        return {"backend": "memory", "entries": len(_store), "live": live, "expired": len(_store) - live}
