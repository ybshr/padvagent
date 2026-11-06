# padvagent – POWR WhatsApp Advertising Assistant

LangGraph agent that receives an Arabic advertising partnership request on WhatsApp, prices it from
the price sheet (`api/data/price_sheet.csv`), creates/updates the opportunity in Odoo CRM, and replies
with a quote — or hands over to a human when the requested discount exceeds the sheet's limit.

```
START → extract (LLM) → analyze (code) ─┬─ incomplete/unsupported ─────────► respond (LLM + guard) → END
                                        ├─ quoted ──────► crm_quote ───────► respond
                                        └─ needs_human ─► crm_handoff ─────► respond
```

- **LLM only extracts and phrases.** Pricing, package choice, discount limits and handoff are plain Python.
- **No invented values:** extraction returns null for anything not stated; values are validated against the sheet;
  slots merge without erasing; a guard rejects any reply containing a number not in the computed facts.
- **One opportunity per conversation:** the Odoo id is kept in checkpointed state (thread = sender number) and updated.

## Pricing rules (from the sheet)
`price_sar` covers `usage_days_included` (30) days; each extra started 30-day block adds `extra_30_days_pct`.
Among matching packages the most valuable one within budget is quoted (else the cheapest, with the gap shown).
Discounts ≤ `max_discount_pct` are applied; above it → human handoff, no price promised.

## Run
```bash
cp .env.example .env   # fill OPENAI_API_KEY + Odoo trial credentials (never commit .env)
make install
make test              # offline: fake LLM + fake Odoo
make demo              # test cases A, B, C live (OpenAI + Odoo)
make run               # http://localhost:8110/chat  (API: POST /api/webhook) {"sender": "+9665...", "message": "..."}
```
