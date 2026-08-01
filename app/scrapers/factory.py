from app.scrapers.demo_scraper import DemoScraper
from app.scrapers.espn_requests_scraper import ESPNRequestsScraper
from app.scrapers.scores365_scraper import Scores365Scraper

SCRAPERS = {"demo": DemoScraper, "espn": ESPNRequestsScraper, "365scores": Scores365Scraper}


def get_scraper(source: str = "demo"):
    if source not in SCRAPERS:
        raise ValueError(f"Fuente no soportada: {source}. Disponibles: {list(SCRAPERS.keys())}")
    return SCRAPERS[source]()  # type: ignore[abstract]
