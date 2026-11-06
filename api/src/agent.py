"""LangGraph workflow for the POWR WhatsApp advertising assistant.

    START → extract → analyze ─┬─ incomplete / unsupported ───────────────► respond → END
                               ├─ quoted ──────────► crm_quote ───────────► respond
                               └─ needs_human ─────► crm_handoff ─────────► respond

- extract  (LLM): structured extraction of stated fields only; validated against the price sheet.
- analyze  (code): missing fields, package selection, pricing, discount-limit check.
- crm_*    (code): create/update ONE Odoo opportunity per conversation (id kept in state).
- respond  (LLM): phrases the reply from computed facts; a number guard rejects any number that is
           not in the facts and falls back to a deterministic template.

Conversation state is checkpointed per sender (thread_id = WhatsApp number).
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Callable, Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from src import pricing
from src.config import config
from src.odoo import CRM, TAG_AGENT, TAG_APPROVAL
from src.prompt import EXTRACT_PROMPT, REPLY_PROMPT
from src.schema import REQUIRED_FIELDS, AgentState, ExtractedRequest, Status

logger = logging.getLogger(__name__)

Extractor = Callable[[list[AnyMessage], dict], ExtractedRequest]
Writer = Callable[[dict], str]

FIELD_LABELS_AR = {
    "brand": "اسم البراند",
    "platform": "المنصة",
    "duration_days": "مدة الحملة",
    "budget_sar": "الميزانية",
}


# ---------------------------------------------------------------------------
# LLM-backed defaults (replaced by fakes in tests)
# ---------------------------------------------------------------------------

def _llm():
    from langchain_openai import ChatOpenAI

    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return ChatOpenAI(model=config.OPENAI_MODEL, api_key=config.OPENAI_API_KEY, timeout=60, max_retries=2)


def openai_extractor() -> Extractor:
    llm = _llm().with_structured_output(ExtractedRequest)

    def extract(messages: list[AnyMessage], known: dict) -> ExtractedRequest:
        fmts = "\n".join(f"- {p}: {', '.join(pricing.formats(p))}" for p in pricing.platforms())
        tiers = "; ".join(sorted({f"{p.platform} {p.creator_tier}" for p in pricing.load_sheet()}))
        system = EXTRACT_PROMPT.format(
            platforms=", ".join(pricing.platforms()), formats=fmts, tiers=tiers,
            known=json.dumps(known, ensure_ascii=False),
        )
        return llm.invoke([SystemMessage(system), *messages[-12:]])

    return extract


def openai_writer() -> Writer:
    llm = _llm()

    def write(facts: dict) -> str:
        msg = llm.invoke([SystemMessage(REPLY_PROMPT.format(facts=json.dumps(facts, ensure_ascii=False, indent=1)))])
        return msg.content if isinstance(msg.content, str) else str(msg.content)

    return write


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONEY_KEYS = {"base_price", "extension_amount", "list_price", "discount_amount", "final_price"}


def _money(x: float | str) -> str:
    if isinstance(x, str):  # already formatted
        return x
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}"


def _sanitize(ex: ExtractedRequest) -> dict:
    """Drop any value the price sheet does not know (the LLM must not introduce new options)."""
    data = ex.model_dump()
    if data.get("platform") not in pricing.platforms():
        if data.get("platform"):
            data["unsupported_platform"] = data.get("unsupported_platform") or data["platform"]
        data["platform"] = None
    if data.get("format") and data["format"] not in pricing.formats():
        data["format"] = None
    if data.get("brand"):
        data["brand"] = data["brand"].strip()
    return data


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[float]:
    text = text.translate(_AR_DIGITS)
    text = re.sub(r"(?<=\d)[,٬](?=\d{3})", "", text)
    return {float(n) for n in _NUM.findall(text)}


def _fact_numbers(obj) -> set[float]:
    out: set[float] = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        out |= _numbers(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _fact_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _fact_numbers(v)
    return out


def reply_is_grounded(reply: str, facts: dict) -> bool:
    """True if every number in the reply appears in the facts (small list counters 1-5 allowed)."""
    allowed = _fact_numbers(facts) | {1.0, 2.0, 3.0, 4.0, 5.0}
    bad = _numbers(reply) - allowed
    if bad:
        logger.warning("Reply rejected, ungrounded numbers: %s", sorted(bad))
    return not bad


def template_reply(facts: dict) -> str:
    """Deterministic Arabic fallback for every status."""
    status = facts["status"]
    if status == Status.NEEDS_HUMAN:
        return (
            "شكراً لتواصلك مع POWR 🙏\n"
            "طلبك يحتاج مراجعة من فريق المبيعات، وتم تحويله لأحد الزملاء مع ملخص كامل، وراح يتواصل معك قريباً."
        )
    if status in (Status.INCOMPLETE, Status.UNSUPPORTED):
        lines = ["شكراً لتواصلك مع POWR 🙏"]
        if status == Status.UNSUPPORTED:
            lines.append(
                f"للأسف منصة {facts['unsupported_platform']} غير متاحة حالياً. "
                f"المنصات المتاحة: {', '.join(facts['supported_platforms'])}."
            )
        if facts.get("ask_for"):
            lines.append("عشان نجهز لك عرض السعر، ممكن توضح لنا:")
            lines += [f"• {q}" for q in facts["ask_for"]]
        return "\n".join(lines)
    q = facts["quote"]
    lines = [
        f"عرض سعر لـ {facts['brand']} 📋",
        f"• الباقة: {q['platform']} – {q['format']} – {q['creator_tier']} ({q['code']})",
        f"• المدة: {q['duration_days']} يوم (السعر يشمل {q['usage_days_included']} يوم)",
        f"• السعر الأساسي: {_money(q['base_price'])} {q['currency']}",
    ]
    if q["extra_30_day_blocks"]:
        lines.append(f"• تمديد {q['extra_30_day_blocks']} × 30 يوم: {_money(q['extension_amount'])} {q['currency']}")
    if q["discount_pct"]:
        lines.append(f"• خصم {q['discount_pct']:g}%: -{_money(q['discount_amount'])} {q['currency']}")
    lines.append(f"• الإجمالي: {_money(q['final_price'])} {q['currency']}")
    if not facts["fits_budget"]:
        lines.append(f"ملاحظة: السعر أعلى من ميزانيتك بـ {_money(facts['budget_gap'])} {q['currency']}.")
    if facts.get("discount_note"):
        lines.append(facts["discount_note"])
    if facts.get("opportunity_ref"):
        lines.append(f"رقم الطلب: {facts['opportunity_ref']}")
    lines.append("تحب نعتمد الطلب؟")
    return "\n".join(lines)


def _transcript(messages: list[AnyMessage], n: int = 10) -> str:
    rows = []
    for m in messages[-n:]:
        who = "Customer" if isinstance(m, HumanMessage) else "Assistant"
        rows.append(f"{who}: {m.content}")
    return "\n".join(rows)


def _html(text: str) -> str:
    return html.escape(text).replace("\n", "<br/>")


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_graph(
    crm: CRM | None,
    extractor: Extractor | None = None,
    writer: Writer | None = None,
    checkpointer=None,
):
    extractor = extractor or openai_extractor()
    writer = writer or openai_writer()

    # -- nodes ---------------------------------------------------------------

    def extract(state: AgentState) -> dict:
        ex = extractor(state["messages"], state.get("slots") or {})
        data = _sanitize(ex)
        logger.info("extracted: %s", {k: v for k, v in data.items() if v not in (None, False)})
        # Clear per-turn flags; slots merge never erases known values.
        return {"slots": data, "handoff_reason": None, "quote": None, "missing": []}

    def analyze(state: AgentState) -> dict:
        s = state.get("slots") or {}

        if state.get("handed_off"):
            return {"status": Status.NEEDS_HUMAN, "handoff_reason": "already_with_human"}

        missing = [f for f in REQUIRED_FIELDS if s.get(f) in (None, "")]
        if s.get("unsupported_platform") and not s.get("platform"):
            return {"status": Status.UNSUPPORTED, "missing": missing}
        if missing:
            return {"status": Status.INCOMPLETE, "missing": missing}

        pkg, fits = pricing.select_package(
            s["platform"], s["duration_days"], s["budget_sar"], s.get("format"), s.get("tier")
        )
        if pkg is None:  # format/tier combination not on the sheet → fall back to platform only
            pkg, fits = pricing.select_package(s["platform"], s["duration_days"], s["budget_sar"])

        requested = float(s.get("discount_pct") or 0)
        if requested > pkg.max_discount_pct:
            quote = pricing.price(pkg, s["duration_days"]).as_dict()  # internal only, for the human
            quote["requested_discount_pct"] = requested
            return {"status": Status.NEEDS_HUMAN, "handoff_reason": "discount_above_limit",
                    "quote": quote, "fits_budget": fits}

        quote = pricing.price(pkg, s["duration_days"], requested).as_dict()
        return {"status": Status.QUOTED, "quote": quote, "fits_budget": fits}

    def route(state: AgentState) -> Literal["respond", "crm_quote", "crm_handoff"]:
        return {Status.QUOTED: "crm_quote", Status.NEEDS_HUMAN: "crm_handoff"}.get(state["status"], "respond")

    def _base_values(state: AgentState, q: dict) -> dict:
        s = state["slots"]
        return {
            "name": f"{s['brand']} – {q['platform']} {q['format']} ({q['code']})",
            "partner_name": s["brand"],
            "contact_name": s.get("contact_name") or False,
            "phone": state.get("sender") or False,
        }

    def crm_quote(state: AgentState) -> dict:
        if crm is None:
            return {}
        q, s = state["quote"], state["slots"]
        desc = (
            f"Source: WhatsApp assistant\nCurrency: {q['currency']}\n"
            f"Package: {q['code']} – {q['platform']} / {q['format']} / {q['creator_tier']}\n"
            f"Duration: {q['duration_days']} days (includes {q['usage_days_included']})\n"
            f"Base: {_money(q['base_price'])} | Extension: {_money(q['extension_amount'])} "
            f"({q['extra_30_day_blocks']} × {q['extra_30_days_pct']:g}%)\n"
            f"Discount: {q['discount_pct']:g}% (max allowed {q['max_discount_pct']:g}%)\n"
            f"Quoted total: {_money(q['final_price'])} {q['currency']}\n"
            f"Customer budget: {_money(s['budget_sar'])} {q['currency']} "
            f"({'fits' if state.get('fits_budget') else 'below quote'})"
        )
        try:
            lead_id = crm.upsert_opportunity(
                state.get("odoo_lead_id"),
                {**_base_values(state, q), "expected_revenue": q["final_price"], "description": _html(desc)},
                [TAG_AGENT],
            )
            return {"odoo_lead_id": lead_id}
        except Exception:
            logger.exception("Odoo upsert failed")
            return {}

    def crm_handoff(state: AgentState) -> dict:
        s, q = state.get("slots") or {}, state.get("quote")
        last_msg = state["messages"][-1].content
        if crm is None:
            return {"handed_off": True}
        try:
            if state.get("handoff_reason") == "already_with_human" and state.get("odoo_lead_id"):
                crm.post_note(state["odoo_lead_id"], _html(f"New customer message (WhatsApp):\n{last_msg}"))
                return {}
            summary = (
                "⚠️ HUMAN APPROVAL NEEDED – discount above price-sheet limit. No price was promised.\n"
                f"Brand: {s.get('brand')} | Phone: {state.get('sender')}\n"
                f"Package: {q['code']} – {q['platform']} / {q['format']} / {q['creator_tier']}\n"
                f"Duration: {q['duration_days']} days | Budget: {_money(s['budget_sar'])} {q['currency']}\n"
                f"List price: {_money(q['list_price'])} {q['currency']}\n"
                f"Requested discount: {q['requested_discount_pct']:g}% | Max allowed: {q['max_discount_pct']:g}%\n"
                f"\nConversation:\n{_transcript(state['messages'])}"
            )
            lead_id = crm.upsert_opportunity(
                state.get("odoo_lead_id"),
                {**_base_values(state, q), "expected_revenue": q["list_price"], "priority": "2",
                 "description": _html(summary)},
                [TAG_AGENT, TAG_APPROVAL],
            )
            crm.post_note(lead_id, _html(summary))
            return {"odoo_lead_id": lead_id, "handed_off": True}
        except Exception:
            logger.exception("Odoo handoff failed")
            return {"handed_off": True}

    def respond(state: AgentState) -> dict:
        s, status = state.get("slots") or {}, state["status"]
        facts: dict = {"status": status.value}
        if status in (Status.INCOMPLETE, Status.UNSUPPORTED):
            shown = {**s}
            if s.get("duration_days"):
                shown["duration_days"] = f"{s['duration_days']} يوم"
            if s.get("budget_sar"):
                shown["budget_sar"] = f"{_money(s['budget_sar'])} {config.CURRENCY}"
            facts["received"] = {FIELD_LABELS_AR[f]: shown[f] for f in REQUIRED_FIELDS if s.get(f)}
            facts["ask_for"] = [FIELD_LABELS_AR[f] for f in state.get("missing", [])]
            if status == Status.UNSUPPORTED:
                facts["unsupported_platform"] = s["unsupported_platform"]
                facts["supported_platforms"] = pricing.platforms()
        elif status == Status.QUOTED:
            q = state["quote"]
            facts.update(brand=s["brand"], quote=q, fits_budget=state["fits_budget"],
                         opportunity_ref=state.get("odoo_lead_id"))
            if not state["fits_budget"]:
                facts["budget_sar"] = s["budget_sar"]
                facts["budget_gap"] = round(q["final_price"] - s["budget_sar"], 2)
            if s.get("discount_requested") and not s.get("discount_pct"):
                facts["discount_note"] = "Customer asked for a discount without a percentage: tell them " \
                                         "to state the discount they want and the team will review it."
            # Display-ready amounts ("42,000"); never leak the discount limit to the customer.
            facts["quote"] = {
                k: (_money(v) if k in _MONEY_KEYS else v) for k, v in q.items() if k != "max_discount_pct"
            }
            if "budget_gap" in facts:
                facts["budget_gap"] = _money(facts["budget_gap"])
                facts["budget_sar"] = _money(facts["budget_sar"])
        # needs_human: no prices in facts → the guard rejects any price in the reply

        try:
            reply = writer(facts).strip()
            if not reply or not reply_is_grounded(reply, facts):
                reply = template_reply(facts)
        except Exception:
            logger.exception("Reply writer failed, using template")
            reply = template_reply(facts)
        return {"reply": reply, "messages": [AIMessage(reply)]}

    # -- wiring --------------------------------------------------------------

    g = StateGraph(AgentState)
    g.add_node("extract", extract)
    g.add_node("analyze", analyze)
    g.add_node("crm_quote", crm_quote)
    g.add_node("crm_handoff", crm_handoff)
    g.add_node("respond", respond)
    g.add_edge(START, "extract")
    g.add_edge("extract", "analyze")
    g.add_conditional_edges("analyze", route)
    g.add_edge("crm_quote", "respond")
    g.add_edge("crm_handoff", "respond")
    g.add_edge("respond", END)
    if checkpointer is None:
        # Explicitly allow our Status enum to be (de)serialized from checkpoints.
        serde = JsonPlusSerializer(allowed_msgpack_modules=[("src.schema", "Status")])
        checkpointer = InMemorySaver(serde=serde)
    return g.compile(checkpointer=checkpointer)


def run_turn(graph, sender: str, text: str) -> AgentState:
    return graph.invoke(
        {"messages": [HumanMessage(text)], "sender": sender},
        config={"configurable": {"thread_id": sender}},
    )
