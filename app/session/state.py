from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.intent.types import Intent


class Place(BaseModel):
    query: str
    lat: float | None = None
    lon: float | None = None
    display_name: str | None = None


class SessionState(BaseModel):
    user_id: str
    origin: Place | None = None
    destination: Place | None = None
    last_intent: Intent = Intent.UNKNOWN
    pending_clarification: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
