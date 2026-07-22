from app.scrapers.base import BaseScraper
from typing import Optional, List, Dict

class DemoScraper(BaseScraper):
    """Scraper de demostración con datos de ejemplo.
    Úsalo para probar la API. Luego lo reemplazas por scrapers reales."""
    
    @property
    def source_name(self):
        return "demo"
    
    def get_stadiums(self) -> List[Dict]:
        return [
            {"id": 1, "name": "Estadio Azteca", "city": "Ciudad de México", "capacity": 87523},
            {"id": 2, "name": "Estadio Akron", "city": "Guadalajara", "capacity": 48071},
            {"id": 3, "name": "Estadio Universitario", "city": "San Nicolás de los Garza", "capacity": 41886},
            {"id": 4, "name": "Estadio BBVA", "city": "Monterrey", "capacity": 51348},
        ]
    
    def get_teams(self) -> List[Dict]:
        return [
            {"id": 1, "name": "Club América", "short_name": "AME", "city": "Ciudad de México", "colors": "Amarillo y Azul", "stadium_name": "Estadio Azteca", "founded": 1916},
            {"id": 2, "name": "Chivas", "short_name": "GDL", "city": "Guadalajara", "colors": "Rojo y Blanco", "stadium_name": "Estadio Akron", "founded": 1906},
            {"id": 3, "name": "Tigres UANL", "short_name": "TIG", "city": "San Nicolás de los Garza", "colors": "Azul y Dorado", "stadium_name": "Estadio Universitario", "founded": 1960},
            {"id": 4, "name": "Monterrey", "short_name": "MTY", "city": "Monterrey", "colors": "Azul y Blanco", "stadium_name": "Estadio BBVA", "founded": 1945},
        ]
    
    def get_players(self) -> List[Dict]:
        return [
            {"id": 1, "name": "Henry Martín", "position": "Delantero", "number": 21, "team_name": "Club América", "nationality": "México"},
            {"id": 2, "name": "Álvaro Fidalgo", "position": "Mediocampista", "number": 8, "team_name": "Club América", "nationality": "España"},
            {"id": 3, "name": "Fernando Beltrán", "position": "Mediocampista", "number": 20, "team_name": "Chivas", "nationality": "México"},
            {"id": 4, "name": "André-Pierre Gignac", "position": "Delantero", "number": 10, "team_name": "Tigres UANL", "nationality": "Francia"},
            {"id": 5, "name": "Sergio Canales", "position": "Mediocampista", "number": 10, "team_name": "Monterrey", "nationality": "España"},
        ]
    
    def get_matches(self, season_id: Optional[int] = None) -> List[Dict]:
        return [
            {"home_team": "Club América", "away_team": "Chivas", "home_score": 2, "away_score": 1, "status": "finished"},
            {"home_team": "Tigres UANL", "away_team": "Monterrey", "home_score": 1, "away_score": 1, "status": "finished"},
            {"home_team": "Chivas", "away_team": "Tigres UANL", "home_score": None, "away_score": None, "status": "scheduled"},
            {"home_team": "Monterrey", "away_team": "Club América", "home_score": None, "away_score": None, "status": "scheduled"},
        ]
    
    def get_standings(self) -> List[Dict]:
        return [
            {"team_name": "Club América", "position": 1, "played": 2, "won": 2, "drawn": 0, "lost": 0, "goals_for": 5, "goals_against": 2, "points": 6},
            {"team_name": "Tigres UANL", "position": 2, "played": 2, "won": 1, "drawn": 1, "lost": 0, "goals_for": 3, "goals_against": 2, "points": 4},
            {"team_name": "Monterrey", "position": 3, "played": 2, "won": 0, "drawn": 1, "lost": 1, "goals_for": 2, "goals_against": 3, "points": 1},
            {"team_name": "Chivas", "position": 4, "played": 2, "won": 0, "drawn": 0, "lost": 2, "goals_for": 1, "goals_against": 4, "points": 0},
        ]
