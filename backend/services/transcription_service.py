"""
Transcription service — supports:
- Deepgram API (cloud, fast, speaker diarization)
- OpenAI Whisper (local, offline)
Both return a normalized transcript: { language, duration, speakers, words[], segments[] }
"""

import math
import requests

from backend.services.provider_service import sanitize_provider_error


class InvalidDeepgramResponse(ValueError):
    """A successful HTTP response did not contain usable transcription JSON."""

    def __init__(self):
        super().__init__('Deepgram returned an invalid transcription response')


def transcribe_deepgram(audio_path: str, api_key: str) -> dict:
    """Send audio to Deepgram Nova-2 for transcription."""
    url = "https://api.deepgram.com/v1/listen"
    params = {
        "model": "nova-2",
        "smart_format": "true",
        "diarize": "true",
        "punctuate": "true",
        "filler_words": "true"
    }
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/wav"
    }
    with open(audio_path, 'rb') as f:
        response = requests.post(url, params=params, headers=headers, data=f, timeout=(10, 300))
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = ''
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get('error') or payload.get('message')
                detail = error.get('message', '') if isinstance(error, dict) else str(error or '')
        except ValueError:
            pass
        detail = sanitize_provider_error(detail or response.text)
        raise RuntimeError(f'Deepgram: HTTP {response.status_code}: {detail}') from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise InvalidDeepgramResponse() from exc
    _deepgram_channels(data, strict_words=True)
    return normalize_deepgram(data)


def transcribe_whisper(audio_path: str, model_size: str = 'base') -> dict:
    """Transcribe using local Whisper model."""
    import whisper
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, word_timestamps=True)
    return normalize_whisper(result)


def _deepgram_channels(data, allow_legacy=False, strict_words=False):
    """Validate the response envelope without mistaking real silence for failure."""
    if not isinstance(data, dict) or any(key in data for key in ('error', 'err_code', 'err_msg')):
        raise InvalidDeepgramResponse()
    metadata = data.get('metadata', {})
    if not isinstance(metadata, dict):
        raise InvalidDeepgramResponse()
    if 'results' in data:
        results = data['results']
        if not isinstance(results, dict):
            raise InvalidDeepgramResponse()
        channels = results.get('channels')
        legacy = False
    elif allow_legacy:
        channels = data.get('channels')
        legacy = True
    else:
        raise InvalidDeepgramResponse()
    if not isinstance(channels, list) or not channels:
        raise InvalidDeepgramResponse()
    for channel in channels:
        if not isinstance(channel, dict):
            raise InvalidDeepgramResponse()
        if legacy and 'alternatives' not in channel:
            alternative = channel
        else:
            alternatives = channel.get('alternatives')
            if not isinstance(alternatives, list) or not alternatives or not isinstance(alternatives[0], dict):
                raise InvalidDeepgramResponse()
            alternative = alternatives[0]
        if not isinstance(alternative.get('words'), list):
            raise InvalidDeepgramResponse()
        text = alternative.get('transcript', None if strict_words else '')
        if not isinstance(text, str) or (text.strip() and not alternative['words']):
            raise InvalidDeepgramResponse()
        # Tolerant normalization is useful for old saved/mock shapes, but live
        # malformed tokens must not silently replace a valid transcript as silence.
        if strict_words and any(
                _normalize_word(word) is None
                or isinstance(word.get('start'), bool) or isinstance(word.get('end'), bool)
                for word in alternative['words']):
            raise InvalidDeepgramResponse()
    return channels


def normalize_deepgram(data: dict) -> dict:
    """Convert Deepgram response to normalized format.

    Deepgram Nova-2 nests channels under results.channels, with words under
    channel.alternatives[0].words (falls back to a top-level `channels` key
    for older/mock response shapes).
    """
    channels = _deepgram_channels(data, allow_legacy=True)
    speakers = set()
    words = []
    segments = []

    detected_language = None
    for channel_index, channel in enumerate(channels):
        if not isinstance(channel, dict):
            continue
        detected_language = detected_language or channel.get('detected_language')
        alts = channel.get('alternatives') or []
        # Real responses use alternatives[0]; retain the older direct-words shape.
        alt = alts[0] if alts and isinstance(alts[0], dict) else channel
        for w in alt.get('words') or []:
            word = _normalize_word(w)
            if word is None:
                continue
            speaker_idx = w.get('speaker', channel.get('channel', channel_index))
            try:
                speaker_idx = max(0, int(speaker_idx))
            except (TypeError, ValueError, OverflowError):
                speaker_idx = channel_index
            speaker_label = f"Speaker {speaker_idx + 1}"
            speakers.add(speaker_idx)
            words.append({**word, 'speaker': speaker_label})

    words.sort(key=lambda word: (word['start'], word['end']))
    # Group words into segments by speaker or silence (>1.2s gap)
    current_seg = None
    for w in words:
        if (current_seg is None or
                current_seg['speaker'] != w['speaker'] or
                (w['start'] - current_seg['end']) > 1.2):
            if current_seg:
                segments.append(current_seg)
            current_seg = {
                'start': w['start'],
                'end': w['end'],
                'speaker': w['speaker'],
                'text': w['text']
            }
        else:
            current_seg['end'] = max(current_seg['end'], w['end'])
            current_seg['text'] += ' ' + w['text']
    if current_seg:
        segments.append(current_seg)

    metadata = data.get('metadata') or {}
    language = detected_language or metadata.get('language') or 'en'
    duration = metadata.get('duration')
    if isinstance(duration, dict):
        duration = duration.get('value')

    return {
        'language': language,
        'duration': max(_nonnegative_number(duration), max((w['end'] for w in words), default=0)),
        'speakers': [f'Speaker {index + 1}' for index in sorted(speakers)] if speakers else ['Speaker 1'],
        'words': words,
        'segments': segments,
    }


def normalize_whisper(data: dict) -> dict:
    """Convert Whisper output to normalized format."""
    words = []
    segments = []

    for seg in data.get('segments') or []:
        if not isinstance(seg, dict):
            continue
        segment = _normalize_word({**seg, 'word': seg.get('text')})
        if segment:
            segments.append({**segment, 'speaker': 'Speaker 1'})
        for word_info in seg.get('words') or []:
            word = _normalize_word(word_info)
            if word:
                words.append({**word, 'speaker': 'Speaker 1'})

    words.sort(key=lambda word: (word['start'], word['end']))
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    # Whisper normally omits a top-level duration.
    duration = max(_nonnegative_number(data.get('duration')),
                   max((item['end'] for item in words + segments), default=0))

    return {
        'language': data.get('language', 'en'),
        'duration': duration,
        'speakers': ['Speaker 1'],
        'words': words,
        'segments': segments,
    }


def _nonnegative_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _normalize_word(word):
    """Ignore unusable provider tokens rather than emitting invalid timestamps."""
    if not isinstance(word, dict):
        return None
    text = word.get('punctuated_word') or word.get('word')
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        start, end = float(word['start']), float(word['end'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
        return None
    return {'text': text.strip(), 'start': start, 'end': end}
