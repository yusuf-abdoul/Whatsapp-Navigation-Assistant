"""LocationIQ routing (OSRM) client.

Returns plain-language steps, distance (meters), and duration (seconds).
"""

from dataclasses import dataclass


@dataclass
class Route:
    steps: list[str]
    distance_m: float
    duration_s: float
    deep_link: str


async def route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> Route:
    raise NotImplementedError
