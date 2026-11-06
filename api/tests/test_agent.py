"""The three required test cases, run offline with a scripted extractor, fake writer and fake CRM."""

import pytest

from src import pricing
from src.agent import build_graph, reply_is_grounded, run_turn
from src.schema import ExtractedRequest, Status


class FakeCRM:
    def __init__(self):
        self.leads: dict[int, dict] = {}
        self.notes: list[tuple[int, str]] = []

    def upsert_opportunity(self, lead_id, values, tags):
        lead_id = lead_id or len(self.leads) + 1
        self.leads[lead_id] = {**values, "tags": tags}
        return lead_id

    def post_note(self, lead_id, body):
        self.notes.append((lead_id, body))


def scripted_extractor(script: list[dict]):
    """Returns the next scripted extraction per turn (stands in for the LLM)."""
    it = iter(script)
    return lambda messages, known: ExtractedRequest(**next(it))


def writer_with_numbers(extra: str = ""):
    """A writer that echoes the facts' template-like content; `extra` lets us inject a hallucination."""
    def write(facts):
        q = facts.get("quote")
        return (f"total {q['final_price']}" if q else "ok") + extra
    return write


def make(script, writer=None):
    crm = FakeCRM()
    graph = build_graph(crm=crm, extractor=scripted_extractor(script), writer=writer or writer_with_numbers())
    return graph, crm


def test_price_sheet_loads():
    sheet = pricing.load_sheet()
    assert len(sheet) == 12
    assert sheet[0].code == "YT-INT-A"  # BOM stripped


def test_pricing_extension_and_discount():
    pkg = next(p for p in pricing.load_sheet() if p.code == "IG-REEL-A")
    assert pricing.price(pkg, 30).final_price == 35000
    assert pricing.price(pkg, 31).final_price == 42000  # one started extra block = +20%
    assert pricing.price(pkg, 60).final_price == 42000
    assert pricing.price(pkg, 90).final_price == 49000
    assert pricing.price(pkg, 60, 10).final_price == 37800


def test_case_a_clear_request():
    graph, crm = make([dict(brand="نخلة", platform="Instagram", format="Reel", duration_days=60, budget_sar=50000)])
    s = run_turn(graph, "+966500000001", "...")
    assert s["status"] == Status.QUOTED
    # Best package within 50k: IG-REEL-A 35,000 + 20% extension = 42,000
    assert s["quote"]["code"] == "IG-REEL-A"
    assert s["quote"]["final_price"] == 42000
    assert s["fits_budget"] is True
    lead = crm.leads[s["odoo_lead_id"]]
    assert lead["partner_name"] == "نخلة"
    assert lead["expected_revenue"] == 42000
    assert "42000" in s["reply"].replace(",", "")


def test_case_b_incomplete_asks_and_continues():
    graph, crm = make([
        dict(brand="لمسة", duration_days=30),
        dict(platform="TikTok"),
        dict(budget_sar=20000),
    ])
    sender = "+966500000002"
    s = run_turn(graph, sender, "...")
    assert s["status"] == Status.INCOMPLETE
    assert s["missing"] == ["platform", "budget_sar"]
    assert s.get("quote") is None and not crm.leads  # nothing invented, nothing created

    s = run_turn(graph, sender, "تيك توك")
    assert s["status"] == Status.INCOMPLETE and s["missing"] == ["budget_sar"]
    assert s["slots"]["brand"] == "لمسة"  # earlier values kept

    s = run_turn(graph, sender, "20 ألف")
    assert s["status"] == Status.QUOTED
    assert s["quote"]["code"] == "TT-VID-B" and s["quote"]["final_price"] == 15000
    assert len(crm.leads) == 1


def test_case_c_discount_above_limit_hands_off():
    graph, crm = make([
        dict(brand="سرعة", platform="YouTube", format="Dedicated video", tier="A", duration_days=30,
             budget_sar=220000, discount_pct=30, discount_requested=True),
        dict(),
    ])
    sender = "+966500000003"
    s = run_turn(graph, sender, "...")
    assert s["status"] == Status.NEEDS_HUMAN and s["handed_off"]
    lead = crm.leads[s["odoo_lead_id"]]
    assert "Needs Human Approval" in lead["tags"]
    assert "Requested discount: 30%" in lead["description"] and "Max allowed: 10%" in lead["description"]
    # The writer tried to say "total ..." but needs_human facts contain no price → guard → template
    assert "200" not in s["reply"] and "%" not in s["reply"]

    s = run_turn(graph, sender, "متى تردون؟")  # stays with the human, no new lead
    assert s["status"] == Status.NEEDS_HUMAN and len(crm.leads) == 1 and len(crm.notes) == 2


def test_discount_within_limit_is_applied():
    graph, _ = make([dict(brand="x", platform="X", duration_days=30, budget_sar=10000, discount_pct=20)])
    s = run_turn(graph, "+966500000004", "...")
    assert s["status"] == Status.QUOTED and s["quote"]["final_price"] == 8000


def test_unsupported_platform():
    graph, crm = make([dict(brand="x", unsupported_platform="Facebook", duration_days=30, budget_sar=5000)])
    s = run_turn(graph, "+966500000005", "...")
    assert s["status"] == Status.UNSUPPORTED and not crm.leads


def test_hallucinated_number_falls_back_to_template():
    graph, _ = make([dict(brand="x", platform="X", duration_days=30, budget_sar=10000)],
                    writer=writer_with_numbers(" with a special price of 7500"))
    s = run_turn(graph, "+966500000006", "...")
    assert "7500" not in s["reply"] and "10,000" in s["reply"]


@pytest.mark.parametrize("text,ok", [("السعر ٤٢٬٠٠٠ ريال", True), ("السعر 41,000 ريال", False)])
def test_grounding_guard(text, ok):
    assert reply_is_grounded(text, {"quote": {"final_price": 42000.0}}) is ok
