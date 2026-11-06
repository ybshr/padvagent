import logging
import threading
from collections import OrderedDict, defaultdict
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from src.agent import build_graph, run_turn
from src.config import config
from src.odoo import OdooCRM
from src.schema import AgentResponse, WhatsAppMessage

logger = logging.getLogger(__name__)
router = APIRouter()

_sender_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)  # one turn at a time per chat
_seen: OrderedDict[str, AgentResponse] = OrderedDict()  # WhatsApp retries the same message_id


@lru_cache
def get_graph():
    return build_graph(crm=OdooCRM())


@router.post("/webhook", response_model=AgentResponse)
def whatsapp_webhook(payload: WhatsAppMessage) -> AgentResponse:
    """Simulated WhatsApp webhook: one incoming customer message → one assistant reply."""
    if payload.message_id and payload.message_id in _seen:
        return _seen[payload.message_id]

    try:
        with _sender_locks[payload.sender]:
            state = run_turn(get_graph(), payload.sender, payload.message)
    except Exception:
        logger.exception("Agent failed for sender %s", payload.sender)
        raise HTTPException(status_code=500, detail="Failed to process WhatsApp message")

    resp = AgentResponse(
        status=state["status"],
        reply=state["reply"],
        collected=state.get("slots") or {},
        missing_fields=state.get("missing") or [],
        quote=state.get("quote") if state["status"] != "needs_human" else None,
        handoff=bool(state.get("handed_off")),
        odoo_opportunity_id=state.get("odoo_lead_id"),
        odoo_url=f"{config.ODOO_URL.rstrip('/')}/odoo/crm/{state['odoo_lead_id']}" if state.get("odoo_lead_id") else None,
    )
    if payload.message_id:
        _seen[payload.message_id] = resp
        while len(_seen) > 1000:
            _seen.popitem(last=False)
    return resp
