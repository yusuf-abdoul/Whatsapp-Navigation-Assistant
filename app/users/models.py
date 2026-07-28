"""User model.

Identity is the WhatsApp number (E.164, e.g. "+2348123456789"). Authentication
is via OTP delivered to that number — no passwords. ``is_admin`` gates the
review console; grant it with ``python -m app.users.admin_cli promote <wa>``
(see [app/users/admin_cli.py](../app/users/admin_cli.py)).

Shares the SQLAlchemy ``Base`` declared in ``app.corridors.models`` so a single
metadata feeds Alembic. (Future cleanup: extract Base to its own module.)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.corridors.models import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wa_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=expression.false()
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
