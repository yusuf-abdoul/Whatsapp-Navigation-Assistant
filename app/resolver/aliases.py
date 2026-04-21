"""Landmark alias loader — expands local nicknames before geocoding.

Data source: data/aliases/abuja.yaml (PM-curated).
"""

from functools import lru_cache
from pathlib import Path

import yaml

ALIAS_FILE = Path(__file__).parent.parent.parent / "data" / "aliases" / "abuja.yaml"


@lru_cache
def _aliases() -> dict[str, str]:
    if not ALIAS_FILE.exists():
        return {}
    with ALIAS_FILE.open() as f:
        data = yaml.safe_load(f) or {}
    return {k.lower(): v for k, v in data.items()}


def expand(query: str) -> str:
    return _aliases().get(query.strip().lower(), query)
