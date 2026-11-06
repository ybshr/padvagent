"""Minimal Odoo CRM client over XML-RPC (API key auth, credentials from env only)."""

from __future__ import annotations

import logging
import xmlrpc.client
from functools import cached_property
from typing import Protocol

from src.config import config

logger = logging.getLogger(__name__)

TAG_AGENT = "WhatsApp Agent"
TAG_APPROVAL = "Needs Human Approval"


class CRM(Protocol):
    def upsert_opportunity(self, lead_id: int | None, values: dict, tags: list[str]) -> int: ...
    def post_note(self, lead_id: int, body: str) -> None: ...


class OdooCRM:
    def __init__(self, url: str = "", db: str = "", username: str = "", api_key: str = ""):
        self.url = (url or config.ODOO_URL).rstrip("/")
        self.db = db or config.ODOO_DB
        self.username = username or config.ODOO_USERNAME
        self.api_key = api_key or config.ODOO_API_KEY
        if not all([self.url, self.db, self.username, self.api_key]):
            raise RuntimeError("Odoo is not configured: set ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY")
        self._tag_ids: dict[str, int] = {}

    @cached_property
    def uid(self) -> int:
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        uid = common.authenticate(self.db, self.username, self.api_key, {})
        if not uid:
            raise RuntimeError("Odoo authentication failed")
        return uid

    def _call(self, model: str, method: str, *args, **kwargs):
        # A fresh proxy per call: ServerProxy is not thread-safe and FastAPI runs sync code in a pool.
        proxy = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        return proxy.execute_kw(self.db, self.uid, self.api_key, model, method, list(args), kwargs)

    def _tag_id(self, name: str) -> int:
        if name not in self._tag_ids:
            found = self._call("crm.tag", "search", [["name", "=", name]], limit=1)
            self._tag_ids[name] = found[0] if found else self._call("crm.tag", "create", {"name": name})
        return self._tag_ids[name]

    def upsert_opportunity(self, lead_id: int | None, values: dict, tags: list[str]) -> int:
        vals = {**values, "type": "opportunity", "tag_ids": [(6, 0, [self._tag_id(t) for t in tags])]}
        if lead_id:
            self._call("crm.lead", "write", [lead_id], vals)
            logger.info("Odoo: updated opportunity %s", lead_id)
            return lead_id
        lead_id = self._call("crm.lead", "create", vals)
        logger.info("Odoo: created opportunity %s", lead_id)
        return lead_id

    def post_note(self, lead_id: int, body: str) -> None:
        self._call("crm.lead", "message_post", [lead_id], body=body, message_type="comment", subtype_xmlid="mail.mt_note")
