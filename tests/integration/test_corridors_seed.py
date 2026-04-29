"""Tests for the corridor seed loader.

Covers:
- Loading a single YAML file produces the expected anchors / corridor / segments.
- Anchor upsert (re-running merges aliases, keeps the same anchor row).
- Bad YAML shapes raise SeedError with a useful message.
- Directory loader picks up all yaml files under a city.
"""

from pathlib import Path
from textwrap import dedent

import pytest
from sqlalchemy import select

from app.corridors.models import Anchor, Corridor, Segment
from app.corridors.repository import find_corridors_by_destination
from app.corridors.seed import SeedError, load_directory, load_file

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(dedent(body).lstrip())
    return p


async def test_load_file_inserts_anchors_corridor_and_segments(db, tmp_path):
    yaml_path = _write(
        tmp_path,
        "test.yaml",
        """
        city: testcity
        status: approved
        anchors:
          - {name: A, lat: 9.0, lon: 7.0, aliases: [a-alias]}
          - {name: B, lat: 9.1, lon: 7.1}
        destination: B
        segments:
          - sequence: 1
            from: A
            to: B
            mode: taxi
            instruction: Take a taxi from A to B
            cost_ngn: 200
            duration_min: 10
        """,
    )

    counts = await load_file(db, yaml_path)
    assert counts == {"anchors": 2, "corridors": 1, "segments": 1}

    anchors = (await db.execute(select(Anchor).where(Anchor.city == "testcity"))).scalars().all()
    assert {a.name for a in anchors} == {"A", "B"}
    a = next(x for x in anchors if x.name == "A")
    assert a.aliases == ["a-alias"]

    [c] = await find_corridors_by_destination(db, "B", city="testcity")
    assert c.status == "approved"
    assert len(c.segments) == 1
    assert c.segments[0].mode == "taxi"
    assert c.segments[0].cost_ngn == 200


async def test_anchor_upsert_merges_aliases_on_repeat(db, tmp_path):
    first = _write(
        tmp_path,
        "first.yaml",
        """
        city: testcity
        status: approved
        anchors:
          - {name: A, lat: 9.0, lon: 7.0, aliases: [old-name]}
          - {name: B, lat: 9.1, lon: 7.1}
        destination: B
        segments:
          - {sequence: 1, from: A, to: B, mode: taxi, instruction: x}
        """,
    )
    second = _write(
        tmp_path,
        "second.yaml",
        """
        city: testcity
        status: approved
        anchors:
          - {name: A, lat: 9.0, lon: 7.0, aliases: [new-name]}
          - {name: B, lat: 9.1, lon: 7.1}
        destination: B
        segments:
          - {sequence: 1, from: A, to: B, mode: taxi, instruction: y}
        """,
    )

    await load_file(db, first)
    await load_file(db, second)

    anchors = (
        await db.execute(select(Anchor).where(Anchor.city == "testcity", Anchor.name == "A"))
    ).scalars().all()
    assert len(anchors) == 1, "anchor should have been upserted, not duplicated"
    assert "old-name" in anchors[0].aliases
    assert "new-name" in anchors[0].aliases


async def test_load_file_rejects_unknown_destination_anchor(db, tmp_path):
    yaml_path = _write(
        tmp_path,
        "bad.yaml",
        """
        city: testcity
        anchors:
          - {name: A, lat: 9.0, lon: 7.0}
        destination: NotInList
        segments: []
        """,
    )
    with pytest.raises(SeedError, match="destination"):
        await load_file(db, yaml_path)


async def test_load_file_rejects_unknown_segment_anchor(db, tmp_path):
    yaml_path = _write(
        tmp_path,
        "bad.yaml",
        """
        city: testcity
        anchors:
          - {name: A, lat: 9.0, lon: 7.0}
          - {name: B, lat: 9.1, lon: 7.1}
        destination: B
        segments:
          - {sequence: 1, from: Ghost, to: B, mode: taxi, instruction: x}
        """,
    )
    with pytest.raises(SeedError, match="Ghost"):
        await load_file(db, yaml_path)


async def test_load_file_requires_city_and_destination(db, tmp_path):
    yaml_path = _write(tmp_path, "bad.yaml", "anchors: []\n")
    with pytest.raises(SeedError, match="city"):
        await load_file(db, yaml_path)


async def test_load_directory_picks_up_all_yamls(db, tmp_path):
    _write(
        tmp_path,
        "a.yaml",
        """
        city: testcity
        anchors:
          - {name: A, lat: 9.0, lon: 7.0}
          - {name: B, lat: 9.1, lon: 7.1}
        destination: B
        segments:
          - {sequence: 1, from: A, to: B, mode: taxi, instruction: x}
        """,
    )
    _write(
        tmp_path,
        "b.yaml",
        """
        city: testcity
        anchors:
          - {name: A, lat: 9.0, lon: 7.0}
          - {name: C, lat: 9.2, lon: 7.2}
        destination: C
        segments:
          - {sequence: 1, from: A, to: C, mode: keke, instruction: y}
        """,
    )

    totals = await load_directory(db, tmp_path)
    assert totals["corridors"] == 2

    corridors = (await db.execute(select(Corridor))).scalars().all()
    segments = (await db.execute(select(Segment))).scalars().all()
    assert len(corridors) == 2
    assert len(segments) == 2


async def test_real_abuja_seed_files_load_cleanly(db):
    """Smoke-test: every committed seed YAML actually loads end-to-end."""
    abuja = REPO_ROOT / "data" / "corridors" / "abuja"
    counts = await load_directory(db, abuja)
    assert counts["corridors"] >= 3
    # Spot-check: the Lugbe→Banex corridor should now resolve via alias.
    [banex_corr] = await find_corridors_by_destination(db, "banex", city="abuja")
    assert len(banex_corr.segments) == 4
