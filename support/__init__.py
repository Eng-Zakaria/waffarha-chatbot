"""Unified customer-service lookups across main_eg order verticals."""
from support.support_queries import (
    SupportQueryService,
    is_support_query,
    extract_identifiers,
    SUPPORT_ERROR,
)

__all__ = [
    "SupportQueryService",
    "is_support_query",
    "extract_identifiers",
    "SUPPORT_ERROR",
]
