"""Tests de scrapers con respuestas mock (sin tocar la red).

Cubre factory, DemoScraper (datos hardcoded) y ESPNRequestsScraper (mockeando
_get_json, el unico metodo que hace HTTP). Sube la cobertura de los scrapers.
"""
from unittest import mock

import pytest

from app.scrapers.factory import get_scraper
from app.scrapers.demo_scraper import DemoScraper
from app.scrapers.espn_requests_scraper import ESPNRequestsScraper


# --- Factory ---
def test_factory_demo():
    assert isinstance(get_scraper("demo"), DemoScraper)


def test_factory_espn():
    assert isinstance(get_scraper("espn"), ESPNRequestsScraper)


def test_factory_default_is_demo():
    assert isinstance(get_scraper(), DemoScraper)


def test_factory_invalid_raises():
    with pytest.raises(ValueError):
        get_scraper("no_existe")


# --- DemoScraper (datos hardcoded, sin red) ---
def test_demo_source_name():
    assert DemoScraper().source_name == "demo"


def test_demo_teams():
    teams = DemoScraper().get_teams()
    assert len(teams) >= 4
    assert any(t["name"] == "Club América" for t in teams)


def test_demo_stadiums():
    assert len(DemoScraper().get_stadiums()) >= 4


def test_demo_players():
    assert len(DemoScraper().get_players()) >= 4


def test_demo_matches():
    assert len(DemoScraper().get_matches()) >= 1


def test_demo_standings():
    standings = DemoScraper().get_standings()
    assert len(standings) >= 4
    assert standings[0]["position"] == 1


# --- ESPNRequestsScraper (mock de _get_json, sin red) ---
TEAMS_JSON = {
    "sports": [{"leagues": [{"teams": [
        {"team": {"id": "1", "displayName": "Club América", "abbreviation": "AME",
                  "location": "CDMX", "color": "Azul",
                  "logos": [{"href": "http://x/a.png"}],
                  "venue": {"name": "Estadio Azteca"}}},
        {"team": {"id": "2", "displayName": "Chivas", "abbreviation": "GDL",
                  "location": "Guadalajara", "color": "Rojo",
                  "logos": [], "venue": {}}},
    ]}]}]
}

STANDINGS_JSON = {
    "children": [{"standings": {"entries": [
        {"team": {"displayName": "Club América"},
         "stats": [{"name": "rank", "value": 1}, {"name": "gamesPlayed", "value": 2},
                   {"name": "wins", "value": 2}, {"name": "ties", "value": 0},
                   {"name": "losses", "value": 0}, {"name": "pointsFor", "value": 5},
                   {"name": "pointsAgainst", "value": 2},
                   {"name": "pointDifferential", "value": 3}, {"name": "points", "value": 6}]},
    ]}}]
}

ROSTER_JSON = {
    "athletes": [{"id": "100", "displayName": "Henry Martín",
                  "position": {"abbreviation": "FW"}, "jersey": "21",
                  "citizenship": "México"}]
}


def _fake_get_json(url, params=None, retries=3):
    if "/roster" in url:
        return ROSTER_JSON
    if "/standings" in url:
        return STANDINGS_JSON
    if "/teams" in url:
        return TEAMS_JSON
    return {}


def test_espn_source_name():
    assert ESPNRequestsScraper().source_name == "espn_requests"


def test_espn_get_teams():
    s = ESPNRequestsScraper()
    with mock.patch.object(s, "_get_json", side_effect=_fake_get_json):
        teams = s.get_teams()
    assert len(teams) == 2
    assert teams[0]["name"] == "Club América"
    assert teams[0]["id"] == 1
    assert teams[0]["logo_url"] == "http://x/a.png"


def test_espn_get_standings():
    s = ESPNRequestsScraper()
    with mock.patch.object(s, "_get_json", side_effect=_fake_get_json):
        standings = s.get_standings()
    assert len(standings) == 1
    assert standings[0]["team_name"] == "Club América"
    assert standings[0]["points"] == 6
    assert standings[0]["position"] == 1


def test_espn_get_stadiums():
    s = ESPNRequestsScraper()
    with mock.patch.object(s, "_get_json", side_effect=_fake_get_json):
        stadiums = s.get_stadiums()
    assert isinstance(stadiums, list)


def test_espn_get_players():
    s = ESPNRequestsScraper()
    with mock.patch.object(s, "_get_json", side_effect=_fake_get_json):
        players = s.get_players()
    # 2 equipos x 1 atleta cada uno
    assert len(players) == 2
    assert any(p["name"] == "Henry Martín" for p in players)


def test_espn_get_json_reintenta_y_falla():
    """_get_json reintenta y relanza si todas las llamadas fallan."""
    s = ESPNRequestsScraper()
    with mock.patch("app.scrapers.espn_requests_scraper.requests.get",
                    side_effect=Exception("red caída")):
        with mock.patch("app.scrapers.espn_requests_scraper.time.sleep"):
            with pytest.raises(Exception):
                s._get_json("http://x", retries=2)
