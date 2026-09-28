"""Support tool: user-scoped order/voucher/refund lookups (main_eg phase).

Wraps support/support_queries.py as a typed agent tool. Gated by
config.SUPPORT_QUERIES_ENABLED + a resolved user_id, same trust boundary
as the personal catalog tool: no user_id -> deterministic negative,
never a cross-user lookup.
"""
from __future__ import annotations

from typing import ClassVar

from agent.tools import Tool, ToolContext, ToolResult


class SupportLookupTool(Tool):
    """Look up the signed-in user's vouchers, orders, refunds, and failures."""

    name = "support_lookup"
    purpose = ("Look up the user's own vouchers, orders, refunds, and order "
                "problems (bill/medical/trip/gift/deals) by order id, voucher "
                "code, or vertical. Requires a signed-in user.")
    input_schema: ClassVar[dict] = {
        "query": {"type": "str", "optional": True},
        "order_id": {"type": "str", "optional": True},
        "voucher_sn": {"type": "str", "optional": True},
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        query = str(args.get("query") or ctx.query or "").strip()
        if args.get("order_id") and args["order_id"] not in query:
            query = f"{query} order {args['order_id']}".strip()
        if args.get("voucher_sn") and args["voucher_sn"] not in query:
            query = f"{query} voucher {args['voucher_sn']}".strip()
        if not ctx.user_id:
            return ToolResult(
                ok=True, items=[], summary="support lookup needs a signed-in user",
                note={"kind": "no_user", "provenance": "support:none"})
        try:
            from core import config
            enabled = bool(getattr(config, "SUPPORT_QUERIES_ENABLED", True))
        except Exception:  # noqa: BLE001
            enabled = True
        if not enabled:
            return ToolResult(
                ok=True, items=[], summary="support lookup disabled",
                note={"kind": "support_disabled", "provenance": "support:none"})
        try:
            from support.support_queries import SupportQueryService, is_support_query
        except Exception:  # noqa: BLE001
            return ToolResult(
                ok=False, summary="support service unavailable",
                error="support_package_unavailable")
        if query and not is_support_query(query):
            return ToolResult(
                ok=True, items=[],
                summary="not a support question",
                note={"kind": "not_support", "provenance": "support:none"})
        try:
            out = SupportQueryService().handle(query, int(ctx.user_id), ctx.reply_lang)
        except Exception:  # noqa: BLE001
            return ToolResult(
                ok=False, summary="support query failed", error="support_error")
        text = str((out or {}).get("answer") or "").strip()
        if not text:
            return ToolResult(ok=True, items=[], summary="no support data",
                              note={"kind": "no_match", "provenance": "support:none"})
        return ToolResult(
            ok=True, items=[{"metadata": {"source": "support",
                                          "id": "support", "answer": text}}],
            summary="support answer",
            note={"kind": "support", "provenance": "support", "text": text})


def register_support_tools(registry):
    registry.register(SupportLookupTool())
    return registry
