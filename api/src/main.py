"""POWR WhatsApp advertising assistant API."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from starlette.responses import FileResponse, RedirectResponse


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = FastAPI(title="Power Advertising Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/docs/")


@app.get("/chat", include_in_schema=False)
def chat_ui():
    """WhatsApp-style demo chat (Arabic renders RTL correctly in the browser)."""
    return FileResponse(Path(__file__).parent / "static" / "chat.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}



from src.router import router as api_router
app.include_router(api_router, prefix="/api", tags=["API"])
