"""LocationIQ geocoding + POI client.

Sprint 2 wires real HTTP calls. Apply alias dictionary before geocoding.
"""

from app.session.state import Place


async def geocode(query: str) -> list[Place]:
    raise NotImplementedError


async def nearby(lat: float, lon: float, category: str, limit: int = 5) -> list[Place]:
    raise NotImplementedError
