"""Metricas en proceso para observabilidad.

Coleccion ligera y thread-safe de metricas de uso de la API: total de requests,
desglose por familia de codigo (2xx/4xx/5xx), por ruta (con plantilla, p. ej.
/matches/{match_id}), latencias y uptime. Se expone en GET /metrics.

Es por-proceso (suficiente para 1 worker). Para varios workers o monitoreo
historico, lo natural es exportar a Prometheus/StatsD mas adelante.
"""
import threading
import time
from collections import defaultdict


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.total = 0
        self.by_class = defaultdict(int)   # "2xx" -> n
        self.by_path = defaultdict(int)    # "/standings" -> n
        self.errors = 0                    # status >= 500
        self.latency_count = 0
        self.latency_sum_ms = 0.0
        self.latency_max_ms = 0.0

    def record(self, path: str, status: int, duration_ms: float):
        with self._lock:
            self.total += 1
            self.by_class[f"{status // 100}xx"] += 1
            self.by_path[path] += 1
            if status >= 500:
                self.errors += 1
            self.latency_count += 1
            self.latency_sum_ms += duration_ms
            if duration_ms > self.latency_max_ms:
                self.latency_max_ms = duration_ms

    def snapshot(self) -> dict:
        with self._lock:
            avg = (self.latency_sum_ms / self.latency_count) if self.latency_count else 0.0
            top = sorted(self.by_path.items(), key=lambda kv: kv[1], reverse=True)[:10]
            return {
                "uptime_seconds": int(time.time() - self.started_at),
                "requests": {
                    "total": self.total,
                    "by_class": dict(self.by_class),
                    "errors_5xx": self.errors,
                },
                "latency_ms": {
                    "avg": round(avg, 1),
                    "max": round(self.latency_max_ms, 1),
                },
                "top_paths": [{"path": p, "count": n} for p, n in top],
            }

    def reset(self):
        with self._lock:
            self.__init__()  # type: ignore[misc]


metrics = Metrics()
