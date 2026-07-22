"""Noticias de Liga MX via RSS (feedparser).

Antes se usaba Playwright + Chromium para raspar Flashscore, lo cual era
fragil (anti-bot, selectores que cambian) y pesado (descargaba un navegador
completo en cada build). Ahora se leen feeds RSS publicos en espanol, que son
estables, rapidos y sin dependencias pesadas.

Fuentes:
- Google Noticias (busqueda "Liga MX") -> agrega muchos medios mexicanos.
- ESPN Deportes (futbol mexicano).
"""
from datetime import datetime
from time import mktime
from typing import List, Dict, Optional
import logging

import feedparser

logger = logging.getLogger(__name__)

# (url_del_feed, nombre_fuente)
RSS_FEEDS = [
    ("https://news.google.com/rss/search?q=Liga+MX+cuando:7d&hl=es-419&gl=MX&ceid=MX:es-419", "Google News"),
    ("https://news.google.com/rss/search?q=%22Liga+MX%22+Apertura&hl=es-419&gl=MX&ceid=MX:es-419", "Google News"),
    ("https://www.espn.com.mx/espn/rss/futbol/mexico/news", "ESPN Deportes"),
]


def _published_to_dt(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime.fromtimestamp(mktime(parsed))
        except (ValueError, OverflowError, TypeError):
            return None
    return None


def _clean(text: str) -> str:
    if not text:
        return ""
    # feedparser ya decodifica entidades; recortamos espacios sobrantes
    return " ".join(text.split()).strip()


def _entry_image(entry) -> Optional[str]:
    """Mejor esfuerzo para sacar la miniatura de una entrada RSS."""
    media = entry.get("media_thumbnail") or entry.get("media_content")
    if isinstance(media, list) and media:
        url = media[0].get("url")
        if url:
            return url  # type: ignore[no-any-return]
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return link.get("href")  # type: ignore[no-any-return]
    return None


def fetch_news(limit: int = 50) -> List[Dict]:
    """Devuelve noticias recientes de Liga MX, deduplicadas por enlace."""
    news: List[Dict] = []
    seen_links = set()

    for url, source in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logger.warning(f"RSS fallo ({source}): {e}")
            continue

        for entry in feed.entries:
            link = entry.get("link")
            title = _clean(entry.get("title", ""))
            if not link or not title or link in seen_links:
                continue
            seen_links.add(link)

            # Google News expone el medio real en entry.source.title
            real_source = source
            src = entry.get("source")
            if isinstance(src, dict) and src.get("title"):
                real_source = _clean(src["title"])

            news.append({
                "title": title,
                "link": link,
                "description": _clean(entry.get("summary", ""))[:500] or title,
                "source": real_source,
                "image_url": _entry_image(entry),
                "published_at": _published_to_dt(entry),
            })

    # Mas recientes primero (los que no traen fecha van al final)
    news.sort(key=lambda n: n["published_at"] or datetime.min, reverse=True)
    return news[:limit]
