from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.channel import InboundRequest, get_channel
from app.config import get_settings
from app.flows.orchestrator import handle

app = FastAPI(title="WNA", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": get_settings().env}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "wna", "docs": "/docs"}


async def _inbound(request: Request) -> InboundRequest:
    return InboundRequest(
        url=str(request.url),
        headers={k.lower(): v for k, v in request.headers.items()},
        body=await request.body(),
    )


@app.post("/webhook/twilio")
async def twilio_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
    channel = get_channel()
    req = await _inbound(request)
    if not channel.verify(req):
        raise HTTPException(status_code=403, detail="invalid signature")
    message = channel.parse(req)
    if message is None:
        return {"status": "ignored"}
    background.add_task(handle, message, channel)
    return {"status": "accepted"}
