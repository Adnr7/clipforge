"""Generate safe FFmpeg drawtext filter chains for burned-in captions."""

import math
import textwrap

from backend.services.render_settings import FONT_FAMILIES, STYLES, validate_caption_settings


CAPTION_WORDS_PER_CHUNK = 2


def _escape_drawtext(text: str) -> str:
    """Escape option values, then filtergraph tokens (no shell quoting involved).

    FFmpeg parses these two levels separately. expansion=none handles literal %;
    doubling percent signs would instead change the displayed caption.
    """
    option = ''.join('\\' + char if char in "\\':" else char for char in text)
    return ''.join('\\' + char if char in "\\'[],;" else char for char in option)


def build_drawtext_filters(words: list, style_name: str = 'classic', clip_start: float = 0,
                           clip_end: float = None, settings: dict = None,
                           width: int = 1080, sample_time: float = None,
                           height: int = 1920) -> list:
    """
    Build FFmpeg drawtext filter strings for word-by-word captions.

    Args:
        words: List of {text, start, end} dicts (absolute timestamps)
        style_name: Legacy caption style preset name
        clip_start: The start time of the clip (to offset timestamps)
        clip_end: Optional absolute end; only intersecting words are emitted
        settings: Optional caption customization mapping
        width: Output canvas width, used for text fitting
        sample_time: Absolute source time for a static preview of the active chunk
        height: Output canvas height, used to bound wrapped text and placement

    Returns:
        List of drawtext filter strings
    """
    caption = validate_caption_settings(settings, style_name=style_name)
    if not caption['enabled']:
        return []
    filters = []

    visible = []
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get('text'), str):
            continue
        try:
            start, end = float(word['start']), float(word['end'])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (not math.isfinite(start) or not math.isfinite(end) or end <= start
                or end <= clip_start or (clip_end is not None and start >= clip_end)
                or not word['text'].strip()):
            continue
        visible.append({**word, 'start': max(start, clip_start),
                        'end': min(end, clip_end) if clip_end is not None else end})
    visible.sort(key=lambda word: (word['start'], word['end']))

    chunks = []
    chunk = []
    for word in visible:
        speaker_changed = (chunk and caption['splitOnSpeaker']
                           and word.get('speaker') != chunk[-1].get('speaker'))
        if chunk and (caption['chunkMode'] == 'word'
                      or len(chunk) >= caption['wordsPerChunk']
                      or speaker_changed
                      or word['start'] - max(item['end'] for item in chunk) > caption['maxGapSeconds']):
            chunks.append(chunk)
            chunk = []
        chunk.append(word)
    if chunk:
        chunks.append(chunk)

    for index, chunk in enumerate(chunks):
        text = ' '.join(w['text'].strip() for w in chunk)
        if caption['uppercase']:
            text = text.upper()
        text, font_size = _fit_text(text, caption, width, height)
        multiline = '\n' in text
        text = _escape_drawtext(text)
        start = chunk[0]['start'] - clip_start
        end = max(word['end'] for word in chunk) - clip_start
        if index + 1 < len(chunks):
            end = min(end, chunks[index + 1][0]['start'] - clip_start)
        if end <= start:
            continue
        intervals = []
        for word in chunk:
            word_start = word['start'] - clip_start
            word_end = min(word['end'] - clip_start, end)
            if word_end <= word_start:
                continue
            if intervals and word_start <= intervals[-1][1]:
                intervals[-1][1] = max(intervals[-1][1], word_end)
            else:
                intervals.append([word_start, word_end])
        if sample_time is not None and not any(
                left <= sample_time - clip_start < right for left, right in intervals):
            continue

        placement = {
            'top': 'h/8',
            'center': '(h-text_h)/2',
            'bottom': 'h-h/4',
        }[caption['placement']]
        if caption['verticalPosition'] is not None:
            placement = f"h*{caption['verticalPosition'] / 100:g}"
        if multiline or caption['verticalPosition'] is not None:
            # Long captions and low custom positions cannot fall off the canvas.
            padding = caption['backgroundPadding'] if caption['backgroundEnabled'] else 0
            margin = min(64, int(height * .06)) + padding + caption['outlineWidth']
            if caption['shadowEnabled']:
                margin += caption['shadowY']
            placement = f"'max(0,min({placement},h-text_h-{margin}))'"
        f = (
            f"drawtext=text={text}:expansion=none"
            f":fontsize={font_size}"
            f":fontcolor={caption['fontColor']}"
            f":borderw={caption['outlineWidth']}"
            f":bordercolor={caption['outlineColor']}"
            f":x=(w-text_w)/2:y={placement}"
        )
        font = FONT_FAMILIES[caption['fontFamily']]
        if font:
            f += f":font={_escape_drawtext(font)}"
        if caption['shadowEnabled']:
            f += (f":shadowx={caption['shadowX']}:shadowy={caption['shadowY']}"
                  f":shadowcolor={caption['shadowColor']}")
        if caption['backgroundEnabled']:
            # Compose one alpha value, including RGBA colors and transparent.
            color = caption['backgroundColor']
            opacity = caption['backgroundOpacity']
            if color == 'transparent':
                color, opacity = 'black', 0
            elif color.startswith('#') and len(color) == 9:
                opacity *= int(color[-2:], 16) / 255
                color = color[:7]
            f += (f":box=1:boxcolor={color}@{opacity:.3f}"
                  f":boxborderw={caption['backgroundPadding']}")
        if sample_time is None:
            # Half-open intervals prevent two captions appearing at a shared end/start.
            # Preserve chunk text across short pauses, but do not display during silence.
            enable = '+'.join(f'gte(t,{left:.6f})*lt(t,{right:.6f})' for left, right in intervals)
            f += f":enable='{enable}'"
        filters.append(f)

    return filters


def _fit_text(text, caption, width, height):
    """Wrap custom/long chunks conservatively, including long unbroken tokens.

    Legacy short two-word captions keep their exact typography. Custom chunks
    use a worst-case glyph width budget, then shrink to fit both canvas axes.
    At the minimum size, exceptionally large transcript tokens are ellipsized.
    """
    size = caption['fontSize']
    custom = (caption['wordsPerChunk'] > 2 or caption['fontFamily'] != 'default'
              or caption['uppercase'] or size > 60 or caption['verticalPosition'] is not None
              or width < 640 or height < size * 2)
    if not custom and len(text) <= 80:
        return text, size
    padding = (caption['backgroundPadding'] if caption['backgroundEnabled'] else 0)
    horizontal_margin = min(64, int(width * .06))
    vertical_margin = min(64, int(height * .06))
    available = max(1, width - 2 * (horizontal_margin + padding + caption['outlineWidth']))
    available_height = max(1, height - 2 * (vertical_margin + padding + caption['outlineWidth'])
                           - (caption['shadowY'] if caption['shadowEnabled'] else 0))
    while True:
        columns = max(1, int(available / (size * 1.1)))
        lines = textwrap.wrap(text, width=columns, break_long_words=True, break_on_hyphens=False)
        max_lines = max(1, min(6, int(available_height / (size * 1.25))))
        if len(lines) <= max_lines or size <= 12:
            break
        size -= 1
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + '…'
    return '\n'.join(lines), size
