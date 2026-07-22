from abc import ABC, abstractmethod
from typing import Optional, Dict, List

class BaseScraper(ABC):
    """Clase base para todos los scrapers. 
    Cada fuente debe implementar estos métodos."""
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        pass
    
    @abstractmethod
    def get_teams(self) -> List[Dict]:
        pass
    
    @abstractmethod
    def get_matches(self, season_id: Optional[int] = None) -> List[Dict]:
        pass
    
    @abstractmethod
    def get_standings(self) -> List[Dict]:
        pass
    
    @abstractmethod
    def get_players(self) -> List[Dict]:
        pass
    
    @abstractmethod
    def get_stadiums(self) -> List[Dict]:
        pass
