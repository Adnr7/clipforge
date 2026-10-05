"""Shared bounded brief contract for AI Edit chat, speech and visual analysis."""

# The form allows 1,000-character audience/goal fields and 2,000-character notes.
# Leave room for their labels and the destination, length and caption choices.
EDITING_BRIEF_LIMIT = 5000


def validate_editing_brief(value):
    if not isinstance(value, str) or len(value) > EDITING_BRIEF_LIMIT or '\x00' in value:
        raise ValueError(f'brief must be text of at most {EDITING_BRIEF_LIMIT} characters')
    return value.strip()
