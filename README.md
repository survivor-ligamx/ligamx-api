# Liga MX API ⚽🇲🇽

API REST de la **Liga MX (Torneo Apertura 2026)** construida con **FastAPI**.
Reúne datos de **múltiples fuentes públicas** (no solo ESPN) y los sirve de forma
estructurada: equipos, jugadores, partidos, tabla de posiciones, goleadores,
estadísticas, alineaciones, eventos en vivo, noticias y más.

---

## 🔌 Fuentes de datos

| Fuente | Uso | Estado |
|--------|-----|--------|
| **ESPN** (`site.api.espn.com`) | Equipos, escudos, plantillas, estadios, partidos, tabla, goleadores y estadísticas | ✅ Fuente principal del sync |
| **365Scores** (`webws.365scores.com`) | Fixtures/resultados frescos del Apertura, tabla, alineaciones con posiciones, eventos (goles/tarjetas/cambios) | ✅ Datos en vivo, no bloqueado |
| **TheSportsDB** | Año de fundación, capacidad de estadios, escudos/jerseys (cruce por `idESPN`), **highlights en video + miniaturas** y calendario | ✅ Enriquecimiento + media |
| **Google Noticias / ESPN (RSS)** | Noticias de Liga MX en español | ✅ Vía `feedparser` |
| **365Scores (noticias)** | Feed propio de noticias de Liga MX (con imagen) | ✅ Unificado en `/news` |
| **SofaScore** | Detalle de partidos / incidencias | ⚠️ Bloqueado por Cloudflare (403) desde servidores; endpoints quedan como *best-effort* |

---

## 🚀 Puesta en marcha local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # ajusta tus variables
uvicorn app.main:app --reload
```

Documentación interactiva (Swagger): **http://localhost:8000/docs**

### Cargar datos

```bash
# Opción A: script directo (usa ESPN)
python sync.py

# Opción B: endpoint protegido por API key
curl -X POST "http://localhost:8000/sync?source=espn" -H "X-API-Key: $SYNC_API_KEY"
```

Fuentes válidas para `source`: `espn` (recomendada), `365scores`, `demo` (datos de prueba sin red).

### Variables de entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `DATABASE_URL` | URL de la BD. SQLite local o PostgreSQL en producción | `sqlite:///./ligamx.db` |
| `SYNC_API_KEY` | Clave requerida para `POST /sync` | — |
| `RUN_SCHEDULER` | Si `true`, el web service corre el sync cada 6h | `false` |
| `RATE_LIMIT_ENABLED` | Activa el rate limiting por IP | `true` |
| `RATE_LIMIT_DEFAULT` | Límite global por IP | `200/minute` |
| `RATE_LIMIT_SYNC` | Límite para `POST /sync` y `/sync/backfill` | `10/minute` |
| `LOG_LEVEL` | Nivel de logging (`DEBUG`/`INFO`/`WARNING`...) | `INFO` |
| `REDIS_URL` | Si se define, usa Redis como caché compartido entre workers (si no, caché en proceso) | — |

> El esquema `postgres://` se normaliza automáticamente a `postgresql://`.

---

## 🧬 Migraciones de base de datos (Alembic)

El esquema de la base de datos se gestiona con **Alembic**.

```bash
# Aplicar todas las migraciones (crea/actualiza tablas)
alembic upgrade head

# Tras cambiar los modelos en app/models.py, generar una migración nueva
alembic revision --autogenerate -m "describe el cambio"
```

- En **desarrollo con SQLite**, la app crea las tablas automáticamente al
  arrancar (no necesitas correr Alembic).
- En **producción con PostgreSQL**, el esquema lo maneja Alembic. El despliegue
  en Render y el workflow de sincronización ejecutan **`python scripts/migrate.py`**
  (migración *self-healing*) antes de arrancar/sincronizar. Esto resuelve el
  *drift* de esquema y, sobre todo, el caso en que la base free queda con las
  tablas creadas pero **sin el sello de versión de Alembic** (que hacía fallar a
  `alembic upgrade head` con *"relation already exists"* y rompía el auto-sync).

  > 🔧 En Render, deja el **Start Command** como:
  > `python scripts/migrate.py && uvicorn app.main:app --host 0.0.0.0 --port 10000`

---

## 🛟 Kit de recuperación de la base (plan free)

La base PostgreSQL del plan **free de Render caduca a los ~30 días**. Como esta
API se usa por temporada, cuando caduque solo tienes que **crear una base nueva
y correr un comando** para dejarla lista (verifica conexión → migra → sincroniza):

```bash
# 1) Crea una nueva base PostgreSQL free en Render y copia su "External URL"
# 2) Apunta la app a ella (en Render: variable de entorno DATABASE_URL)
# 3) Reinicializa todo en un solo comando:
export DATABASE_URL="postgresql://usuario:pass@host/db"
python scripts/recover.py
```

Opciones: `--source 365scores` (fuente alterna), `--skip-migrate` (si ya migraste).
El script **no necesita `SYNC_API_KEY`** (llama al sync internamente). Al terminar
imprime temporada, equipos, jugadores y partidos cargados. Verifica con
`GET /sync/status` y `GET /standings`.

> 💡 También puedes recrear los datos sin el script: deja el *Start Command* de
> Render como `alembic upgrade head && uvicorn app.main:app ...` y luego haz un
> `POST /sync` con tu `X-API-Key`. El script es el atajo de un solo paso.

---

## 🩺 Chequeo rápido (post-deploy / post-jornada)

Un comando le pega a los endpoints clave de la API en producción y reporta
OK/FALLO en segundos, incluyendo la frescura de los datos:

```bash
python scripts/healthcheck.py                       # URL por defecto (producción)
python scripts/healthcheck.py https://mi-api.com    # otra URL
python scripts/healthcheck.py --max-age-hours 12    # umbral de frescura
```

Sale con código `!= 0` si algún chequeo **crítico** falla (útil para automatizar
o correrlo después de cada jornada para confirmar que el sync actualizó bien).

---

## 📋 Qué revisar en la Jornada 1 (arranque de temporada)

Varios endpoints dependen de **partidos ya jugados**, así que en pretemporada
salen vacíos/en cero y **se llenan solos** conforme se disputan partidos y el
sync captura las estadísticas de 365Scores. Tras la Jornada 1, verifica que se
poblaron:

| Endpoint | Qué debería aparecer tras la J1 |
|---|---|
| `GET /players/leaderboard?metric=goals` | Goleadores, asistencias, rating, xG… |
| `GET /players/discipline` | Tarjetas acumuladas + riesgo de suspensión |
| `GET /players/identity-map` | `coverage_pct` > 0 (cruce ESPN↔365 poblado) |
| `GET /matches/{id}/players-to-watch` | Jugadores destacados por partido |
| `GET /matches/{id}/player-stats` | Stats por jugador del partido (xG, rating…) |
| `GET /standings` · `/power-ranking` | Puntos y posiciones reales (ya no en cero) |
| `GET /liguilla/results` | Se llena al llegar la fase final (fin del torneo) |

Comando rápido para confirmar frescura y que todo responde:
`python scripts/healthcheck.py`

---

## 📚 Catálogo de endpoints

> **Versionado:** todas las rutas están disponibles en la raíz (`/...`, por
> compatibilidad) y bajo **`/v1/...`** (recomendado para nuevos clientes).
> `GET /version` devuelve las versiones disponibles. Ejemplos abajo en la raíz.

### General
- `GET /` — info de la API
- `GET /dashboard?season=` — **resumen todo-en-uno**: líder de tabla, goleador, próximos partidos, últimos resultados y noticias (ideal para una home/bot) 🆕
- `GET /health` — health check (liveness)
- `GET /health/ready` — **readiness**: verifica conexión a BD y Redis 🆕
- `GET /version` — versiones de la API disponibles
- `GET /metrics` — **observabilidad**: uptime, total de requests, desglose por código, latencias, rutas más usadas y estado del caché 🆕
- `GET /season` — **torneo vigente y datos cargados** (Apertura/Clausura, si ya inició, total de partidos) 🆕

### Equipos
- `GET /teams` — lista (paginada) con escudo, fundación y estadio
- `GET /teams/search?q=` — búsqueda por nombre (ignora acentos)
- `GET /teams/{id}` — detalle
- `GET /teams/{id}/players` — plantilla
- `GET /teams/{id}/last-matches` — últimos partidos
- `GET /teams/{id}/form` — **forma reciente** (W/D/L + racha) 🆕
- `GET /teams/{id}/stats?season=` — promedios/totales de estadísticas
- `GET /teams/{id}/season-stats?season=` — **~100 estadísticas del equipo en la temporada vía ESPN** (porterías a cero, goles recibidos, pases completados, tackles, intercepciones, duelos...) 🆕
- `GET /teams/{id}/discipline?season=` — **disciplina del equipo**: totales de tarjetas + desglose por jugador y quién está en riesgo de suspensión 🆕
- `GET /teams/{id}/streak` — **rachas actuales** del equipo (invicto, victorias, sin ganar, anotando, portería a cero) 🆕
- `GET /teams/xg-performance?season=&order=over|under` — **goles vs xG por equipo** (efectivos vs desperdiciadores) 🆕

### Partidos
- `GET /calendar?season=` — **calendario completo por jornada** con rival, fecha, sede oficial, marcador y estado 🆕
- `GET /matches` — filtros: `team_id`, `week`, `status`, `limit`, `offset`
- `GET /matches/upcoming` — próximos partidos
- `GET /matches/team/{team_id}` — por equipo
- `GET /matches/week/{n}` — por jornada
- `GET /matches/{id}` — detalle
- `GET /matches/{id}/timeline` — **línea de tiempo guardada**: goles, tarjetas y cambios 🆕
- `GET /matches/{id}/squad` — **alineaciones guardadas** (titulares/suplentes, posición, dorsal) 🆕
- `GET /matches/{id}/full` — **TODO el partido en una respuesta** (marcador, eventos, alineaciones, stats) 🆕
- `GET /matches/{id}/live` — **marcador EN VIVO** (goles, reloj, periodo, estado) 🆕
- `GET /matches/{id}/stats` — estadísticas del partido (ESPN)
- `GET /matches/{id}/lineups` — alineaciones (ESPN)
- `GET /matches/{id}/events` — eventos clave (goles/tarjetas/cambios)
- `GET /matches/{id}/cards` — solo tarjetas
- `GET /matches/{id}/player-stats` — **stats completas por jugador del partido, guardadas en BD** (minutos, goles, xG, xA, pases, regates, rating...) 🆕
- `GET /matches/{id}/players-to-watch?limit=` — **jugadores a seguir**: los más destacados de cada equipo según su forma (goles, asistencias, xG, rating) con motivo explicable 🆕
- `GET /matches/live` — partidos en vivo (hoy)
- `GET /matches/today?date=YYYY-MM-DD` — partidos de un día
- `GET /h2h/{team1}/{team2}` — historial entre dos equipos
- `GET /h2h/{team1}/{team2}/summary` — **resumen del historial** (victorias, goles, empates) 🆕
- `GET /weeks` — jornadas disponibles
- `GET /weeks/current` — jornada actual

### Tabla y goleadores
- `GET /seasons` — **temporadas/torneos disponibles** (histórico multi-temporada) 🆕
- `GET /seasons/compare?a=&b=` — **compara dos temporadas lado a lado** (líder de la fase regular, goleador, goles totales y promedio, equipos y partidos) 🆕
- `GET /standings?season=` — tabla de posiciones (por defecto la temporada vigente) 🆕
- `GET /standings/projection?season=` — **proyección de la tabla final** (puntos esperados de los partidos restantes vía Poisson) 🆕
- `GET /liguilla?season=` — **clasificación a Liguilla / Play-In** (formato Liga MX)
- `GET /liguilla/bracket?season=` — **cuadro oficial de Liguilla** (siembra Play-In + Cuartos 1v8/2v7/3v6/4v5, ida y vuelta) 🆕
- `GET /liguilla/results?season=` — **resultados REALES de la Liguilla por serie**: agrupa los partidos de fase final jugados, calcula el marcador GLOBAL (ida y vuelta) y el ganador de cada llave (se llena al jugarse la Liguilla) 🆕
- `GET /top-scorers?season=` — tabla de goleo

> El parámetro `season` acepta la etiqueta del torneo (`Apertura 2026`) o el año
> (`2026`, devuelve el torneo más reciente de ese año). Sin `season`, se usa la
> temporada vigente. Los partidos pueden filtrarse con `GET /matches?season=`.

### Búsqueda global
- `GET /search?q=&limit=` — **busca equipos, jugadores y estadios** a la vez por nombre (ignora acentos; los que empiezan por la consulta aparecen primero) 🆕

### Analítica 🆕
- `GET /compare/players?a=&b=&season=` — **compara dos jugadores** lado a lado (goles, asistencias, xG, xA, rating...)
- `GET /compare/teams?a=&b=&season=` — **compara dos equipos** (posición, puntos, registro, goles, xG)
- `GET /predict?home=&away=&season=&prior_strength=` — **predice un partido** con Poisson regularizado + ajuste Dixon-Coles: conserva `probabilities` y agrega `probabilities_raw`, `probabilities_regularized`, `draw_probability`, `expected_goals`, `top_scorelines`, `sample_size`, `prior_strength`, `confidence/uncertainty` y factores explicables
- `GET /power-ranking?season=` — **ranking de poder** de equipos (puntos/partido + diferencia de goles, escala 0-100; xG informativo) 🆕

### Perfiles 🆕
- `GET /players/{id}/profile?season=` — **perfil completo del jugador** (ficha + bio: edad, nacionalidad con bandera, altura, peso + agregado de temporada + últimos 5 partidos) 🆕
- `GET /teams/{id}/profile?season=` — **perfil completo del equipo** (ficha+sede, posición, forma, xG, plantilla, próximo partido y último resultado)
- `GET /players/identity-map` — **diagnóstico del cruce de identidad** ESPN↔365Scores: cuántos jugadores tienen mapeado su id de 365Scores y la cobertura del cruce 🆕

### Tiempo real (SSE)
- `GET /live/stream?interval=&max_seconds=` — **marcadores en vivo por streaming** (Server-Sent Events). El servidor empuja un evento `live` con los partidos en curso cuando cambian. Ej: `new EventSource("/live/stream")` 🆕

### Jugadores y estadísticas
- `GET /players` — lista
- `GET /players/search?q=&position=&nationality=&team_id=` — **búsqueda y filtros** (ignora acentos) 🆕
- `GET /players/top?season=` — mejores por goles
- `GET /players/{id}` — detalle
- `GET /players/{id}/stats?season=` — estadísticas del jugador
- `GET /players/{id}/match-stats?season=` — **historial partido a partido** con stats completas 🆕
- `GET /players/{id}/season-stats?season=` — **resumen agregado de la temporada** (minutos, goles, asistencias, xG, xA, rating promedio) 🆕
- `GET /players/season-leaders?stat=&season=&min_appearances=` — **tabla de líderes desde la BD** (goals, assists, minutes, xg, xa, rating...) 🆕
- `GET /players/leaderboard?metric=&season=&order=&limit=` — **tabla de líderes UNIFICADA**: una sola entrada para cualquier métrica de rendimiento (goals, assists, minutes, shots, xg, xa, rating...) o de disciplina (yellow_cards, red_cards) 🆕
- `GET /players/xg-performance?season=&order=over|under` — **goles vs xG**: quién finaliza por encima/debajo de lo esperado 🆕
- `GET /players/discipline?season=&order=&at_risk=` — **tabla de disciplina**: tarjetas amarillas/rojas acumuladas por jugador, con riesgo de suspensión (regla Liga MX: 5 amarillas = 1 partido) 🆕
- `GET /players/{id}/discipline?season=` — **tarjetas acumuladas de un jugador** y su estado de suspensión 🆕
- `GET /players/{id}/form?last=&season=` — **forma reciente del jugador** (últimos N partidos: rating, goles, asistencias, racha de goleo) 🆕
- `GET /player-stats?season=` — estadísticas agregadas

### Datos en vivo (365Scores)
- `GET /365scores/matches?week=&status=` — fixtures/resultados frescos
- `GET /365scores/standings` — tabla
- `GET /365scores/teams` — equipos
- `GET /365scores/matches/{game_id}/info` — **ficha del partido: sede, árbitro y cuerpo arbitral** 🆕
- `GET /365scores/matches/{game_id}/lineups` — alineaciones con posiciones
- `GET /365scores/matches/{game_id}/events` — eventos
- `GET /365scores/matches/{game_id}/cards` — tarjetas
- `GET /365scores/matches/{game_id}/player-stats` — **estadísticas COMPLETAS por jugador** (minutos, goles, xG, xA, remates, pases, regates, duelos, intercepciones, rating...) para todos los jugadores 🆕
- `GET /365scores/matches/{game_id}/shots` — **mapa de tiros con xG** (cada disparo: xG, xGoT, parte del cuerpo, resultado, coordenadas) + totales de xG por equipo 🆕
- `GET /365scores/matches/{game_id}/top-performers` — **mejores jugadores del partido** por posición (local/visitante) 🆕
- `GET /365scores/matches/{game_id}/heatmaps` — **mapas de calor por jugador** (URL de imagen) 🆕
- `GET /365scores/goalkeepers` — **tabla de porteros** (vallas invictas, goles recibidos, salvadas, penales atajados) 🆕
- `GET /365scores/news` — **noticias de Liga MX** (feed propio de 365Scores: título, imagen, url, fecha) 🆕
- `GET /365scores/leaders?category_id=` — **líderes de temporada por jugador** en 16 categorías (goles, asistencias, xG, tarjetas, salvadas, valla invicta...) 🆕
- `GET /365scores/team-leaders?category_id=` — líderes de temporada por equipo 🆕

### Extras
- `GET /extras/highlights` — highlights en **video + miniaturas** de los últimos partidos (TheSportsDB, con Scorebat de respaldo)
- `GET /extras/calendar` — **próximos partidos** con miniatura, sede y horario (TheSportsDB) 🆕
- `GET /extras/teams/assets` — escudos/jerseys/estadios (TheSportsDB)
- `GET /extras/teams/{espn_team_id}/assets` — assets de un equipo

### Noticias
- `GET /news` — noticias de Liga MX **unificadas** (RSS de Google/ESPN + feed propio de 365Scores), con **miniatura** (`image_url`) cuando está disponible 🆕

### Sincronización
- `POST /sync?source=espn` — recarga los datos (requiere header `X-API-Key`)
- `POST /sync/backfill?year=2025&tournament=Apertura` — **carga una temporada pasada al histórico** sin borrar las demás; la tabla se reconstruye desde los resultados (requiere `X-API-Key`) 🆕
- `POST /sync/player-identity?season=&force=` — **reconstruye el mapa de identidad** ESPN↔365Scores (rellena `external_365_id` para emparejar stats por id exacto en vez de por nombre); respeta enlaces manuales salvo `force=true`; se ejecuta solo en cada `POST /sync` (requiere `X-API-Key`) 🆕
- `POST /players/{id}/link-365?external_365_id=` — **enlaza a mano** un jugador con su id de 365Scores (para apodos que el cruce automático no resuelve); el enlace se conserva en futuras reconstrucciones (requiere `X-API-Key`) 🆕
- `GET /sync/status` — **estado y frescura de los datos** (último sync, si fue exitoso, antigüedad) 🆕

---

## 🗓️ Sincronización automática

El sync programado corre vía **GitHub Actions** (`.github/workflows/sync.yml`)
cada 6 horas, ejecutando `python sync.py` contra la BD de producción
(`secrets.DATABASE_URL`). Alternativamente, puedes activar `RUN_SCHEDULER=true`
en el web service para que sea él quien sincronice.

---

## ☁️ Despliegue en Render

El archivo `render.yaml` define el web service + base de datos PostgreSQL.
Recuerda definir `SYNC_API_KEY` en el dashboard (no se versiona en el repo).

```
buildCommand: pip install -r requirements.txt
startCommand: uvicorn app.main:app --host 0.0.0.0 --port 10000
```

---

## ⚡ Rendimiento (caché)

Los endpoints que consultan fuentes externas (`/matches/live`, `/matches/today`,
`/matches/{id}/stats|lineups|events|cards`, todos los `/365scores/*` y
`/extras/*`) usan un **caché en memoria con TTL** (de 30s para datos en vivo
hasta 24h para assets). Esto responde al instante y evita rate-limits/baneos de
las fuentes. Los endpoints que leen de la base de datos no se cachean (ya son
rápidos).

---

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

La suite cubre el helper de temporada, el caché, la red de seguridad del sync y
los endpoints principales (con una BD SQLite sembrada, sin tocar la red).
Se ejecuta automáticamente en cada push/PR vía GitHub Actions
(`.github/workflows/tests.yml`), que además valida que las migraciones de
Alembic apliquen correctamente.

---

```
app/
├── main.py            # App FastAPI + routers + scheduler opcional
├── database.py        # Engine SQLAlchemy (Postgres/SQLite)
├── models.py          # Modelos ORM
├── schemas.py         # Esquemas Pydantic (respuestas)
├── dependencies.py    # API key + helpers
├── routers/           # Endpoints por dominio
├── scrapers/          # Un scraper por fuente (patrón BaseScraper + factory)
└── services/
    └── sync_service.py  # Orquestación del sync (FETCH → WRITE → ENRICH)
alembic/                 # Migraciones de base de datos
└── versions/            # Scripts de migración generados
```

El **sync** es seguro por diseño: primero descarga todo a memoria (FETCH); si una
fuente crítica falla, **aborta sin tocar la BD**. La escritura ocurre en una sola
transacción (WRITE) y el enriquecimiento (stats, assets, noticias) corre aislado
(ENRICH), de modo que un fallo ahí no invalida el resto.

### Qué se guarda en la base de datos

Equipos, estadios, jugadores, temporada, jornadas, partidos, tabla de posiciones,
goleadores, estadísticas por equipo y por jugador, noticias y, por cada partido
jugado, su **línea de tiempo completa** (goles, **tarjetas amarillas y rojas**,
cambios) y sus **alineaciones** (titulares y suplentes con posición y dorsal).
El marcador en vivo se consulta en el momento a la fuente (con caché corto).
