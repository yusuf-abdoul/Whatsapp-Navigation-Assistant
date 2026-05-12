"""SQLAlchemy models for the corridor data layer.

Conceptual model:
- An ``Anchor`` is a named geographic point (bus stop, junction, landmark) with a city scope.
- A ``Corridor`` is a directed route ending at one anchor (``destination_anchor_id``). It carries
  optional applicability notes/time windows that say *when* the corridor is usable.
- A ``Segment`` is one ordered step inside a corridor connecting two anchors with a transport
  mode and a human-readable instruction. Time windows on a segment override the corridor's.

Lookup at query time: pick corridors-by-destination, find the user's nearest anchor on each
candidate corridor, and clip the segment list to start from that anchor.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

CORRIDOR_STATUSES = ("pending", "approved", "rejected")
SEGMENT_MODES = ("taxi", "keke", "bike", "walk", "bus", "car", "mixed")


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Anchor(Base):
    __tablename__ = "anchors"
    __table_args__ = (
        UniqueConstraint("name", "city", name="uq_anchors_name_city"),
        Index("ix_anchors_city", "city"),
    )

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    city: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Corridor(Base):
    __tablename__ = "corridors"
    __table_args__ = (
        CheckConstraint(
            f"status IN {CORRIDOR_STATUSES}",
            name="ck_corridors_status",
        ),
        Index("ix_corridors_destination", "destination_anchor_id"),
        Index("ix_corridors_status", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    destination_anchor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("anchors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    applicability_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # JSONB list of {start: "HH:MM", end: "HH:MM", days?: [...]} — empty list means always.
    applicability_windows: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    contributor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    destination: Mapped[Anchor] = relationship(
        Anchor, foreign_keys=[destination_anchor_id], lazy="joined"
    )
    segments: Mapped[list[Segment]] = relationship(
        "Segment",
        back_populates="corridor",
        cascade="all, delete-orphan",
        order_by="Segment.sequence",
        lazy="selectin",
    )


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("corridor_id", "sequence", name="uq_segments_corridor_sequence"),
        CheckConstraint(
            f"mode IN {SEGMENT_MODES}",
            name="ck_segments_mode",
        ),
        CheckConstraint(
            "from_anchor_id <> to_anchor_id",
            name="ck_segments_distinct_endpoints",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    corridor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corridors.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_anchor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("anchors.id", ondelete="RESTRICT"), nullable=False
    )
    to_anchor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("anchors.id", ondelete="RESTRICT"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    instruction: Mapped[str] = mapped_column(String(500), nullable=False)
    # Marks a vehicle change vs prior segment. Same-mode runs without a
    # transfer flag are collapsed by the renderer into one user-facing step.
    transfer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=expression.false()
    )
    cost_ngn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_windows: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    availability_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Anchors the vehicle physically passes through on this leg but doesn't
    # stop at as an endpoint. Used at query time so a rider near any of these
    # places can join mid-leg and get the same clipped instructions. Stored
    # as a Postgres UUID array — order-insensitive, dedup at the app layer.
    passthrough_anchor_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    corridor: Mapped[Corridor] = relationship(Corridor, back_populates="segments")
    from_anchor: Mapped[Anchor] = relationship(Anchor, foreign_keys=[from_anchor_id])
    to_anchor: Mapped[Anchor] = relationship(Anchor, foreign_keys=[to_anchor_id])
