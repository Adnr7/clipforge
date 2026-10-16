"""Shared bounded brief contract for AI Edit speech and visual analysis."""

import json

# The form allows 1,000-character audience/goal fields and 2,000-character notes.
# Leave room for their labels and the destination, length and caption choices.
EDITING_BRIEF_LIMIT = 5000


def validate_editing_brief(value):
    if not isinstance(value, str) or len(value) > EDITING_BRIEF_LIMIT or '\x00' in value:
        raise ValueError(f'brief must be text of at most {EDITING_BRIEF_LIMIT} characters')
    return value.strip()


def editing_brief_fingerprint(brief, audience_brief):
    """Return a stable, non-reversible identity for a completed visual brief."""
    if not isinstance(brief, str) or not isinstance(audience_brief, dict):
        raise ValueError('Cannot fingerprint an invalid editing brief')
    payload = json.dumps({
        'brief': brief,
        'audienceBrief': {
            key: audience_brief.get(key, '') for key in ('audience', 'goal', 'notes')
        },
    }, ensure_ascii=False, separators=(',', ':'))
    # FNV-1a is used as an identity, not as a security primitive. Keeping this
    # small and synchronous lets the browser compare persisted recommendations
    # without an asynchronous Web Crypto round trip.
    value = 0xCBF29CE484222325
    for byte in payload.encode('utf-8'):
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f'fnv1a64-v1:{value:016x}'
