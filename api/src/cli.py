"""Terminal demo of the assistant (for the video).

    poetry run python -m src.cli                # interactive chat
    poetry run python -m src.cli --scenario A   # run a test case: A, B, C or all
"""

import argparse
import logging
import uuid

from src.agent import build_graph, run_turn
from src.odoo import OdooCRM

SCENARIOS = {
    "A": [
        "السلام عليكم، معكم شركة نخلة للتمور. نبغى إعلان ريل على انستقرام لمدة شهرين، وميزانيتنا 50 ألف ريال.",
    ],
    "B": [
        "مرحبا، أنا من براند لمسة للعطور ونبي نسوي حملة إعلانية لمدة شهر",
        "تيك توك",
        "ميزانيتنا تقريباً 20 ألف ريال",
    ],
    "C": [
        "السلام عليكم، براند سرعة للقهوة. نبي فيديو مخصص على يوتيوب مع مشهور Tier A لمدة 30 يوم، "
        "ميزانيتنا 220 ألف ريال، بس نبي خصم 30%.",
        "طيب متى تردون علينا؟",
    ],
}


RTL = False  # --rtl: reorder Arabic for plain terminals with no bidi support (not VS Code)


def show(text: str) -> None:
    """Print text so Arabic reads correctly in terminals without RTL support."""
    if not RTL:
        print(text)
        return
    import arabic_reshaper
    from bidi import get_display

    reshaper = arabic_reshaper.ArabicReshaper({"delete_harakat": False, "support_ligatures": True})
    for line in text.split("\n"):
        print(get_display(reshaper.reshape(line)))


def chat(graph, sender: str, text: str) -> None:
    print()
    show(f"👤 {text}")
    s = run_turn(graph, sender, text)
    show(f"🤖 {s['reply']}")
    show(f"   [status={s['status'].value} odoo_id={s.get('odoo_lead_id')}]")
    show(f"   {s.get('slots')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["A", "B", "C", "all"])
    ap.add_argument("--no-odoo", action="store_true", help="skip Odoo writes")
    ap.add_argument("--rtl", action="store_true", help="reorder Arabic for terminals without bidi support")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    global RTL
    RTL = args.rtl

    graph = build_graph(crm=None if args.no_odoo else OdooCRM())
    if args.scenario:
        for name in ["A", "B", "C"] if args.scenario == "all" else [args.scenario]:
            print(f"\n================ Test case {name} ================")
            sender = f"+9665{uuid.uuid4().int % 10**8:08d}"
            for msg in SCENARIOS[name]:
                chat(graph, sender, msg)
        return

    sender = "+966500000000"
    print("POWR WhatsApp assistant – type a message (Ctrl+C to exit)")
    try:
        while True:
            chat(graph, sender, input("\n> "))
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == "__main__":
    main()
