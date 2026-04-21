from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="WNA", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "env": settings.env}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "wna", "docs": "/docs"}
