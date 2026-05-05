"""Seed corridor loader.

Reads hand-authored YAML files under ``data/corridors/<city>/*.yaml``, upserts
their anchors (matched by name+city), and inserts a fresh corridor with its
ordered segments. Designed for dev/seed use; idempotency is best-effort
(re-running re-creates corridors but keeps anchors stable). Once the
contributor portal lands, this becomes a one-shot bootstrap script.

CLI:

    uv run python -m app.corridors.seed             # loads everything under data/corridors/
    uv run python -m app.corridors.seed --city abuja  # one city only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.corridors.db import session_factory
from app.corridors.models import Anchor, Corridor, Segment

log = structlog.get_logger("seed")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SEED_ROOT = REPO_ROOT / "data" / "corridors"


class SeedError(Exception):
    """Anything wrong with a seed file's shape or references."""


async def load_directory(db: AsyncSession, root: Path) -> dict[str, int]:
    """Load every ``*.yaml`` file under ``root`` (recursively).

    Returns counts of {anchors_upserted, corridors_inserted, segments_inserted}.
    """
    totals = {"anchors": 0, "corridors": 0, "segments": 0}
    for path in sorted(root.rglob("*.yaml")):
        counts = await load_file(db, path)
        for k, v in counts.items():
            totals[k] += v
    return totals


async def load_file(db: AsyncSession, path: Path) -> dict[str, int]:
    """Load one corridor YAML file. Caller commits the session."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise SeedError(f"{path}: top-level must be a mapping")

    city = _required(data, "city", path)
    anchors_by_name = await _upsert_anchors(db, data.get("anchors", []), city, path)
    destination_name = _required(data, "destination", path)
    if destination_name not in anchors_by_name:
        raise SeedError(f"{path}: destination '{destination_name}' not in anchors list")

    corridor = Corridor(
        destination_anchor_id=anchors_by_name[destination_name].id,
        status=data.get("status", "pending"),
        applicability_notes=data.get("applicability_notes"),
        applicability_windows=data.get("applicability_windows", []),
        contributor_id=data.get("contributor"),
    )
    db.add(corridor)
    await db.flush()

    segments = data.get("segments", []) or []
    for s in segments:
        if s["from"] not in anchors_by_name:
            raise SeedError(f"{path}: segment 'from' anchor '{s['from']}' not declared")
        if s["to"] not in anchors_by_name:
            raise SeedError(f"{path}: segment 'to' anchor '{s['to']}' not declared")
        db.add(
            Segment(
                corridor_id=corridor.id,
                sequence=s["sequence"],
                from_anchor_id=anchors_by_name[s["from"]].id,
                to_anchor_id=anchors_by_name[s["to"]].id,
                mode=s["mode"],
                instruction=s["instruction"],
                transfer=bool(s.get("transfer", False)),
                cost_ngn=s.get("cost_ngn"),
                duration_min=s.get("duration_min"),
                time_windows=s.get("time_windows"),
                availability_notes=s.get("availability_notes"),
            )
        )
    await db.flush()

    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    log.info(
        "corridor_seeded",
        path=rel,
        destination=destination_name,
        segments=len(segments),
    )
    return {"anchors": len(anchors_by_name), "corridors": 1, "segments": len(segments)}


async def _upsert_anchors(
    db: AsyncSession, raw_anchors: list[dict[str, Any]], city: str, path: Path
) -> dict[str, Anchor]:
    """Upsert by (name, city). Returns name → Anchor for use by segments."""
    if not raw_anchors:
        raise SeedError(f"{path}: 'anchors' list is required and non-empty")

    by_name: dict[str, Anchor] = {}
    for raw in raw_anchors:
        name = raw["name"]
        existing = (
            await db.execute(select(Anchor).where(Anchor.name == name, Anchor.city == city))
        ).scalar_one_or_none()

        if existing is None:
            anchor = Anchor(
                name=name,
                lat=float(raw["lat"]),
                lon=float(raw["lon"]),
                city=city,
                aliases=[a.lower() for a in raw.get("aliases", [])],
            )
            db.add(anchor)
        else:
            existing.lat = float(raw["lat"])
            existing.lon = float(raw["lon"])
            new_aliases = {a.lower() for a in raw.get("aliases", [])}
            existing.aliases = sorted(set(existing.aliases) | new_aliases)
            anchor = existing
        by_name[name] = anchor

    await db.flush()
    return by_name


def _required(data: dict[str, Any], key: str, path: Path) -> Any:
    if key not in data:
        raise SeedError(f"{path}: missing required key '{key}'")
    return data[key]


async def _main(city: str | None) -> None:
    root = DEFAULT_SEED_ROOT / city if city else DEFAULT_SEED_ROOT
    if not root.exists():
        print(f"no seed directory at {root}", file=sys.stderr)
        sys.exit(1)

    factory = session_factory()
    async with factory() as db:
        totals = await load_directory(db, root)
        await db.commit()
    print(
        f"seeded: {totals['anchors']} anchor refs, "
        f"{totals['corridors']} corridors, {totals['segments']} segments"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load corridor seed YAMLs into the database.")
    parser.add_argument("--city", help="Only load corridors under data/corridors/<city>/")
    args = parser.parse_args()
    asyncio.run(_main(args.city))
