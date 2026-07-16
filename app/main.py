from dotenv import load_dotenv
load_dotenv()

import os
import re
import sys
import time
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import subprocess

from app.database import engine, Base
from app import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.rate_limit import limiter
from app.metrics import metrics
from app.routers import health, teams, matches, standings, stadiums, players, stats, news, sync, sofascore, scores365, extras, search, live, analytics, overview, odds

# Logging estructurado basico (timestamp, nivel, logger). En produccion se puede
# enviar a un colector; aqui dejamos un formato consistente para todos los logs.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
access_logger = logging.getLogger("ligamx.access")

# En desarrollo (SQLite) creamos las tablas automaticamente para arrancar sin
# pasos extra. En produccion (PostgreSQL) el esquema lo gestiona Alembic
# (`alembic upgrade head`), que SI maneja cambios de columnas/migraciones.
if engine.dialect.name == "sqlite":
    Base.metadata.create_all(bind=engine)

# Red de seguridad para TABLAS NUEVAS: si el deploy no corre `alembic upgrade head`
# (p. ej. Start Command sin migracion), garantizamos que las tablas que aun no
# existan se creen. `create_all` con checkfirst NO altera ni borra tablas
# existentes (los cambios de COLUMNAS los sigue gestionando Alembic). Idempotente.
try:
    Base.metadata.create_all(bind=engine)
except Exception:  # pragma: no cover - nunca debe impedir el arranque
    logging.getLogger("ligamx").warning("create_all de respaldo fallo (se ignora)", exc_info=True)

scheduler = BackgroundScheduler()

def auto_sync():
    python = sys.executable
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([python, "sync.py"], cwd=project_root)

if os.getenv("RUN_SCHEDULER", "false").lower() == "true":
    scheduler.add_job(auto_sync, "interval", hours=6)
    scheduler.start()

def _unique_route_id(route) -> str:
    """operationId unico por ruta (necesario porque cada router se monta dos
    veces: en la raiz y bajo /v1, lo que produciria ids duplicados)."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", f"{route.name}_{route.path}").strip("_")


app = FastAPI(
    title="Liga MX API",
    version="1.0",
    description=(
        "API de la Liga MX (Apertura 2026). Datos de múltiples fuentes públicas: "
        "equipos, jugadores, partidos, tabla, goleadores, estadísticas avanzadas "
        "(xG por tiro/jugador/equipo), árbitros, alineaciones, heatmaps, calendario, "
        "Liguilla, noticias, marcadores en vivo (SSE), comparador y predictor.\n\n"
        "Todas las rutas existen en la raíz (`/...`) y bajo `/v1/...`."
    ),
    openapi_tags=[
        {"name": "365scores", "description": "Datos en vivo y avanzados (xG, alineaciones, heatmaps, porteros, noticias)."},
        {"name": "meta", "description": "Versión, métricas y salud de la API."},
    ],
    contact={"name": "Liga MX API", "url": "https://github.com/BRUCEWAYNE0180/ligamx-api-"},
    generate_unique_id_function=_unique_route_id,
)

# Rate limiting por IP (slowapi). El limite por defecto aplica a todas las rutas;
# los endpoints sensibles (sync) anaden un limite mas estricto.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def observability(request, call_next):
    """Mide latencia, registra cada request (metodo, ruta, status, ms) y alimenta
    las metricas en proceso. La ruta se normaliza a su plantilla (p. ej.
    /matches/{match_id}) para no explotar la cardinalidad."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    try:
        metrics.record(path, response.status_code, duration_ms)
    except Exception:
        pass
    access_logger.info(f"{request.method} {path} {response.status_code} {duration_ms:.1f}ms")
    return response


@app.middleware("http")
async def security_headers(request, call_next):
    """Cabeceras de seguridad basicas para una API publica."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# CORS configurable: en producción usa CORS_ORIGINS="https://tudominio.com,https://otro.com"
# En desarrollo deja vacío o pon "*" (no recomendado en prod).
_cors_raw = os.getenv("CORS_ORIGINS", "").strip()
if _cors_raw:
    allow_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
else:
    allow_origins = ["*"]  # fallback dev; en prod DEBES definir CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Todos los routers se montan DOS veces: en la raiz (retrocompatibilidad con los
# clientes actuales) y bajo el prefijo /v1 (version estable para evolucionar sin
# romper a nadie). Asi, p. ej., /standings y /v1/standings devuelven lo mismo.
ROUTERS = [
    health.router, teams.router, matches.router, standings.router, stadiums.router,
    players.router, stats.router, news.router, sync.router, sofascore.router,
    scores365.router, extras.router, search.router, live.router, analytics.router,
    overview.router, odds.router,
]

for _r in ROUTERS:
    app.include_router(_r)
for _r in ROUTERS:
    app.include_router(_r, prefix="/v1")


@app.get("/version", tags=["meta"])
def api_version():
    """Versiones de la API disponibles. Las rutas existen en la raiz (legado) y
    bajo /v1 (recomendado para nuevos clientes)."""
    return {
        "api": "Liga MX API",
        "version": "1.0",
        "available_versions": ["v1"],
        "current": "v1",
        "note": "Las rutas estan disponibles en la raiz (/...) y bajo /v1 (/v1/...).",
    }
