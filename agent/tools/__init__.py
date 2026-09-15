"""
Typed tool/capability layer for the agent.

Tools are the ONLY way the agent reaches application data. Every tool wraps
existing deterministic business logic (FacetedCatalog lookups, hybrid
retrieval, catalog/personal services) behind a typed input schema, safe
failure behavior, and provenance-tagged output.

The model never touches the database, writes SQL, or reads the filesystem:
it selects a tool by name + args, and the tool does the rest. Tool results
carry provenance ("faceted:merchant=KFC", "hybrid:semantic") so grounding is
auditable end to end.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class ToolResult:
    """Typed outcome of a tool execution."""
    ok: bool = False
    items: list = field(default_factory=list)
    summary: str = "no items"
    note: dict | None = None
    error: str | None = None


@dataclass
class ToolContext:
    """Everything a tool needs to do its job. The `facade` is the RagEngine
    instance (or a test fake) exposing the underlying capabilities; the
    `state` records budget/observations so the loop stays bounded and traceable."""
    facade: Any
    reply_lang: str = "en"
    query: str = ""
    normalized_query: str = ""
    history: list = field(default_factory=list)
    recent_offers: list = field(default_factory=list)
    user_id: int | None = None
    state: Any = None


class Tool:
    """Base class: typed in, typed out, provenance-tagged, safe failure."""

    name: str = ""
    description: str = ""
    purpose: str = ""
    input_schema: ClassVar[dict] = {}

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        raise NotImplementedError

    def describe(self) -> str:
        """Compact schema description fed to the planner prompt."""
        return f"{self.name}({self._schema_brief()}) -- {self.purpose}"

    def _schema_brief(self) -> str:
        bits = []
        for k, spec in (self.input_schema or {}).items():
            typ = spec.get("type", "any")
            if "values" in spec:
                typ = "|".join(str(v) for v in spec["values"])
            opt = "?" if spec.get("optional") else ""
            bits.append(f"{k}{opt}:{typ}")
        return ", ".join(bits)

    def coerce_args(self, args: dict) -> dict:
        """Validate/normalise raw model-provided args against the schema.
        Unknown keys are dropped; missing optional keys default to None."""
        out: dict[str, Any] = {}
        for key, spec in (self.input_schema or {}).items():
            typ = spec.get("type")
            value = args.get(key) if isinstance(args, dict) else None
            out[key] = self._coerce(key, value, typ, spec)
        return out

    def _coerce(self, key, value, typ, spec):
        if value is None or value == "":
            return None
        if typ in ("str", "string"):
            if isinstance(value, list):
                value = next((x for x in value if str(x).strip()), None)
            return str(value).strip() or None
        if typ in ("int", "integer"):
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ValueError(f"arg '{key}' must be an integer, got {value!r}")
        if typ == "float":
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValueError(f"arg '{key}' must be a number, got {value!r}")
        if typ == "bool":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("1", "true", "yes", "y")
        if typ == "number_range":
            return self._coerce_range(value)
        if typ == "str_list":
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            if isinstance(value, list):
                return [str(v) for v in value if str(v).strip()]
            return None
        return value

    @staticmethod
    def _coerce_range(value) -> list | None:
        """[lo, hi] -> [float, float]; single number -> [n, None]; else None."""
        if isinstance(value, (list, tuple)):
            nums = []
            for v in value[:2]:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    nums.append(None)
            lo, hi = (nums + [None] * 2)[:2]
            return _normalise_range(lo, hi)
        try:
            n = float(value)
        except (TypeError, ValueError):
            return None
        return _normalise_range(None, n)


def _normalise_range(lo, hi) -> list | None:
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return [lo, hi]


class ToolRegistry:
    """Named registry of tools the agent may select. The planner only ever
    sees `names()` / `description_block()`; execution goes through `call()`."""

    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool):
        if not tool.name:
            raise ValueError("tool must have a name")
        self._tools[tool.name] = tool

    def select(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def has(self, name: str) -> bool:
        return name in self._tools

    def description_block(self) -> str:
        lines = []
        for name in self.names():
            tool = self._tools[name]
            lines.append(f"- {tool.describe()}")
        return "\n".join(lines)

    def call(self, name: str, ctx: ToolContext, args: dict) -> ToolResult:
        tool = self.select(name)
        if tool is None:
            return ToolResult(
                ok=False, summary=f"unknown tool '{name}'",
                error=f"tool '{name}' is not registered",
            )
        try:
            clean = tool.coerce_args(args or {})
            started = time.perf_counter()
            result = tool.run(ctx, clean)
            ms = (time.perf_counter() - started) * 1000.0
        except ValueError as e:
            return ToolResult(ok=False, summary=f"bad args for {name}", error=str(e))
        except Exception as e:  # noqa: BLE001 -- tools must fail safely
            return ToolResult(ok=False, summary=f"{name} failed", error=str(e))
        result.summary = f"{name}: {result.summary}"
        result.note = dict(result.note or {})
        result.note.update({"tool": name, "tool_ms": round(ms, 1)})
        ctx_state = getattr(ctx, "state", None)
        if ctx_state is not None:
            ctx_state.tool_calls += 1
            ctx_state.metrics.setdefault("tool_ms", 0.0)
            ctx_state.metrics["tool_ms"] += ms
        return result


from agent.tools.cascade_tools import (  # noqa: F401
    CatalogTool,
    CompareOffersTool,
    RetrieveFaqTool,
    SuperlativeOfferTool,
    register_cascade_tools,
)
from agent.tools.catalog_tools import (  # noqa: F401
    GetOfferTool,
    SearchOffersTool,
    register_catalog_tools,
)
