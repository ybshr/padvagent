from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

REQUIRED_FIELDS = ("brand", "platform", "duration_days", "budget_sar")


# -------------------------
# WhatsApp input
# -------------------------

class WhatsAppMessage(BaseModel):
    sender: str = Field(..., description="WhatsApp sender phone number (used as the conversation id)")
    message: str = Field(..., min_length=1)
    message_id: Optional[str] = None
    sender_name: Optional[str] = None


# -------------------------
# LLM extraction output
# -------------------------

class ExtractedRequest(BaseModel):
    """Fields the customer EXPLICITLY stated. Anything not stated must stay null."""

    brand: Optional[str] = Field(None, description="Brand / company name being advertised, exactly as written.")
    platform: Optional[str] = Field(
        None, description="Advertising platform. Must be one of the supported platform names, else null."
    )
    unsupported_platform: Optional[str] = Field(
        None, description="If the customer named a platform that is NOT supported, its name here."
    )
    format: Optional[str] = Field(None, description="Ad format, only if stated. Must be one of the listed formats.")
    tier: Optional[Literal["A", "B", "C"]] = Field(
        None, description="Creator tier letter, only if the customer stated a tier or creator audience size."
    )
    duration_days: Optional[int] = Field(
        None, gt=0, description="Campaign duration converted to days (week=7, month=30). Null if not stated."
    )
    budget_sar: Optional[float] = Field(None, ge=0, description="Budget amount in SAR. Null if not stated.")
    discount_pct: Optional[float] = Field(
        None, ge=0, le=100, description="Requested discount percentage, only if a number was stated."
    )
    discount_requested: bool = Field(False, description="True if the customer asked for any discount at all.")
    contact_name: Optional[str] = Field(None, description="Customer's personal name if they introduced themselves.")


# -------------------------
# Graph state
# -------------------------

def merge_slots(old: Optional[dict], new: Optional[dict]) -> dict:
    """Merge newly extracted values without ever erasing known values with nulls."""
    merged = dict(old or {})
    for k, v in (new or {}).items():
        if v is not None and v != "" and not (k == "discount_requested" and v is False):
            merged[k] = v
    return merged


def last_value(old, new):
    return new


class Status(str, Enum):
    INCOMPLETE = "incomplete"
    UNSUPPORTED = "unsupported"
    QUOTED = "quoted"
    NEEDS_HUMAN = "needs_human"


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    sender: str
    slots: Annotated[dict, merge_slots]
    # Per-turn routing info written by `analyze`
    missing: list[str]
    quote: Optional[dict]
    fits_budget: bool
    handoff_reason: Optional[str]
    status: Status
    # Sticky across turns
    odoo_lead_id: Optional[int]
    handed_off: bool
    # Output of the turn
    reply: str


# -------------------------
# API response
# -------------------------

class AgentResponse(BaseModel):
    status: Status
    reply: str
    collected: dict = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    quote: Optional[dict] = None
    handoff: bool = False
    odoo_opportunity_id: Optional[int] = None
    odoo_url: Optional[str] = None
