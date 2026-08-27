#!/usr/bin/env python3
"""Shared authored-section vocabulary for both report renderers."""

SECTION_KEYS: tuple[str, ...] = (
    "introduction", "methods", "key_findings", "external_findings",
    "conclusions", "limitations", "next_steps",
)
SECTION_TITLE: dict[str, str] = {
    "introduction": "Introduction",
    "methods": "Methods",
    "key_findings": "Key findings",
    "external_findings": "Material findings not grounded in retrieved full text",
    "conclusions": "Conclusions",
    "limitations": "Limitations & evidence gaps",
    "next_steps": "Next steps",
}
INLINE_SECTIONS: frozenset[str] = frozenset({"key_findings", "external_findings"})
