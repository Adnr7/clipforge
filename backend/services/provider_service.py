"""Persistent provider profiles and short, safe provider error messages."""

import hashlib
import html
import json
import os
import re
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser

from backend.services.render_settings import valid_http_url


PROFILE_KINDS = ('llm', 'transcription')
LLM_PROFILE_PROVIDERS = {
    'deepseek', 'openai', 'claude', 'groq', 'gemini', 'openrouter', 'ollama', 'custom',
}
TRANSCRIPTION_PROFILE_PROVIDERS = {'deepgram', 'whisper'}
# Named OpenAI Whisper models, without importing the optional local engine.
WHISPER_MODELS = {
    'tiny', 'tiny.en', 'base', 'base.en', 'small', 'small.en',
    'medium', 'medium.en', 'large', 'large-v1', 'large-v2', 'large-v3',
    'large-v3-turbo', 'turbo',
}
# These are fixed by transcribe_deepgram; profile overrides are not supported.
DEEPGRAM_MODEL = 'nova-2'
DEEPGRAM_ENDPOINT = 'https://api.deepgram.com/v1/listen'
PROVIDER_LABELS = {
    'deepseek': 'DeepSeek', 'openai': 'OpenAI', 'claude': 'Anthropic',
    'groq': 'Groq', 'gemini': 'Gemini', 'openrouter': 'OpenRouter',
    'ollama': 'Ollama', 'custom': 'Custom', 'deepgram': 'Deepgram', 'whisper': 'Whisper',
}


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {'script', 'style', 'noscript'}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in {'script', 'style', 'noscript'} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            text = data.strip()
            if text:
                self.parts.append(text)


def sanitize_provider_error(value, max_length=240):
    """Turn provider/HTML gateway failures into a short useful message."""
    if value is None:
        return 'Request failed'
    text = str(value).strip()
    if '<' in text and '>' in text:
        parser = _VisibleTextParser()
        try:
            parser.feed(text)
            text = ' '.join(parser.parts)
        except Exception:
            text = re.sub(r'<[^>]*>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Do not return an HTML fragment or a provider page's accidental dump.
    if not text:
        text = 'Request failed'
    return text[:max_length].rstrip()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, field, required=False, maximum=500):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    value = value.strip()
    if len(value) > maximum or any(char in value for char in '\r\n\x00'):
        raise ValueError(f'{field} is invalid or too long')
    return value


def validate_profile_payload(data, existing=None):
    """Validate a public profile request and return DB column values.

    ``apiKey`` is accepted for writes and intentionally excluded from all
    public representations. Omitting it or leaving it blank during an update
    preserves the existing secret, matching the write-only editor contract.
    """
    if not isinstance(data, dict):
        raise ValueError('Provider profile must be a JSON object')
    allowed = {'name', 'kind', 'provider', 'apiKey', 'baseUrl', 'model'}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f'Unknown provider profile field: {sorted(unknown)[0]}')
    existing = dict(existing) if existing is not None else {}

    name = _text(data.get('name', existing.get('name')), 'name', required=True, maximum=80)
    kind = _text(data.get('kind', existing.get('kind')), 'kind', required=True, maximum=30).lower()
    if kind not in PROFILE_KINDS:
        raise ValueError('kind must be llm or transcription')
    provider = _text(data.get('provider', existing.get('provider')), 'provider', required=True, maximum=40).lower()
    allowed_providers = (LLM_PROFILE_PROVIDERS if kind == 'llm'
                         else TRANSCRIPTION_PROFILE_PROVIDERS)
    if provider not in allowed_providers:
        raise ValueError(f'Unknown {kind} provider')
    if existing and (kind != existing['kind'] or provider != existing['provider']):
        raise ValueError('Profile kind and provider cannot be changed; create a separate profile')

    api_key = existing.get('api_key')
    if 'apiKey' in data:
        value = data['apiKey']
        if value is not None and not isinstance(value, str):
            raise ValueError('apiKey must be a string')
        if isinstance(value, str) and value.strip():
            api_key = _text(value, 'apiKey', maximum=2000)

    base_value = data.get('baseUrl', existing.get('base_url'))
    base_url = None
    if base_value not in (None, ''):
        base_url = valid_http_url(_text(base_value, 'baseUrl', maximum=500))
    model_value = data.get('model', existing.get('model'))
    model = None if model_value in (None, '') else _text(model_value, 'model', maximum=200)

    if kind == 'llm' and provider == 'custom' and (not base_url or not model):
        raise ValueError('Custom LLM profiles require baseUrl and model')
    if kind == 'transcription' and provider == 'deepgram' and not api_key:
        raise ValueError('Deepgram profiles require apiKey')
    if provider == 'deepgram':
        if ('model' in data and model not in (None, DEEPGRAM_MODEL)) or (
                'baseUrl' in data and base_url not in (None, DEEPGRAM_ENDPOINT)):
            raise ValueError('Deepgram uses the server model nova-2 and its fixed listen endpoint')
        # Earlier versions accepted fields the engine never used. Normalize
        # these on save instead of continuing to persist misleading overrides.
        model, base_url = DEEPGRAM_MODEL, DEEPGRAM_ENDPOINT
    if kind == 'transcription' and provider == 'whisper' and model is not None and model not in WHISPER_MODELS:
        raise ValueError('Unknown Whisper model')
    return {
        'name': name,
        'kind': kind,
        'provider': provider,
        'api_key': api_key,
        'base_url': base_url,
        'model': model,
    }


def profile_public(row, active_id=None):
    row = dict(row)
    model, base_url = effective_profile_connection(row)
    return {
        'id': row['id'],
        'name': row['name'],
        'kind': row['kind'],
        'provider': row['provider'],
        'baseUrl': row.get('base_url') or '',
        'model': row.get('model') or '',
        'effectiveModel': model,
        'effectiveBaseUrl': base_url,
        'hasApiKey': bool((row.get('api_key') or '').strip()),
        'active': row['id'] == active_id,
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def effective_profile_connection(row):
    """Resolve the connection exactly as the current provider engines do."""
    row = dict(row)
    provider = row['provider']
    if provider == 'deepgram':
        return DEEPGRAM_MODEL, DEEPGRAM_ENDPOINT
    if provider == 'whisper':
        return row.get('model') or os.environ.get('WHISPER_MODEL', '').strip() or 'base', ''
    # Lazy import avoids the llm_service -> provider_service error-helper cycle.
    from backend.services.llm_service import PROVIDERS, provider_model
    model = provider_model(provider, row)
    base_url = row.get('base_url') or (
        os.environ.get('CUSTOM_BASE_URL', '').strip() if provider == 'custom'
        else PROVIDERS.get(provider, {}).get('base_url', ''))
    return model, base_url


def legacy_provider_connections():
    """Snapshot every configured legacy connection, including local models.

    Incomplete custom settings are still represented so they can be repaired in
    the same editor. Importing is not a connectivity test or an activation.
    """
    from backend.services.llm_service import PROVIDERS
    connections = []
    for provider, config in PROVIDERS.items():
        api_key = os.environ.get(config['env_key'], '').strip() if config['env_key'] else ''
        if provider == 'ollama':
            if (os.environ.get('LLM_PROVIDER', '').strip().lower() != 'ollama'
                    and not os.environ.get('OLLAMA_MODEL', '').strip()):
                continue
        elif not api_key:
            continue
        model, base_url = effective_profile_connection({'provider': provider})
        connections.append({'kind': 'llm', 'provider': provider, 'api_key': api_key or None,
                            'model': model, 'base_url': base_url})
    custom = {'kind': 'llm', 'provider': 'custom',
              'api_key': os.environ.get('CUSTOM_API_KEY', '').strip() or None,
              'model': os.environ.get('CUSTOM_MODEL', '').strip(),
              'base_url': os.environ.get('CUSTOM_BASE_URL', '').strip()}
    if any(custom[key] for key in ('api_key', 'model', 'base_url')):
        connections.append(custom)
    deepgram_key = os.environ.get('DEEPGRAM_API_KEY', '').strip()
    if deepgram_key:
        connections.append({'kind': 'transcription', 'provider': 'deepgram',
                            'api_key': deepgram_key, 'model': DEEPGRAM_MODEL,
                            'base_url': DEEPGRAM_ENDPOINT})
    if (os.environ.get('TRANSCRIPTION_MODE', '').strip().lower() in ('local', 'whisper')
            or os.environ.get('WHISPER_MODEL', '').strip()):
        model, base_url = effective_profile_connection({'provider': 'whisper'})
        connections.append({'kind': 'transcription', 'provider': 'whisper',
                            'api_key': None, 'model': model, 'base_url': base_url})
    return connections


def _connection_matches(row, connection):
    row = dict(row)
    return (row['kind'] == connection['kind'] and row['provider'] == connection['provider']
            and (row.get('api_key') or '').strip() == (connection['api_key'] or '')
            and effective_profile_connection(row) == (connection['model'], connection['base_url']))


def import_legacy_provider_profiles(db):
    """Add persistent legacy snapshots once, never replacing a user profile.

    The ledger intentionally survives profile deletion and edits. A changed
    legacy credential/model/endpoint is a new snapshot; a repeated one never
    recreates a deleted profile. No secret or fingerprint enters the public API.
    """
    connections = legacy_provider_connections()
    # Lazy additive migration keeps this self-contained for existing databases.
    # No FK: retaining import history after deletion is deliberate.
    db.execute('''CREATE TABLE IF NOT EXISTS provider_legacy_imports (
        fingerprint TEXT PRIMARY KEY, profile_id TEXT NOT NULL, created_at TEXT NOT NULL
    )''')
    try:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT * FROM provider_profiles ORDER BY created_at, id').fetchall()
        for connection in connections:
            fingerprint = hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest()
            if db.execute('SELECT 1 FROM provider_legacy_imports WHERE fingerprint=?',
                          (fingerprint,)).fetchone():
                continue
            # Adopt an exact pre-existing connection rather than duplicating it.
            existing = next((row for row in rows if _connection_matches(row, connection)), None)
            profile_id = existing['id'] if existing else new_profile_id()
            now = _now()
            if existing is None:
                label = PROVIDER_LABELS[connection['provider']]
                name = f"{label} · {connection['model'] or 'model not set'}"[:80]
                db.execute('''INSERT INTO provider_profiles
                    (id, name, kind, provider, api_key, base_url, model, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)''',
                           (profile_id, name, connection['kind'], connection['provider'],
                            connection['api_key'], connection['base_url'] or None,
                            connection['model'] or None, now, now))
            db.execute('INSERT INTO provider_legacy_imports (fingerprint, profile_id, created_at) VALUES (?,?,?)',
                       (fingerprint, profile_id, now))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return connections


def legacy_fallback_profile_ids(rows, connections):
    """Identify exact legacy defaults without selecting/overriding a profile."""
    selected = {
        'llm': os.environ.get('LLM_PROVIDER', 'deepseek').strip().lower(),
        'transcription': 'whisper' if os.environ.get('TRANSCRIPTION_MODE', 'cloud').strip().lower() in ('local', 'whisper') else 'deepgram',
    }
    result = {'llm': None, 'transcription': None}
    for connection in connections:
        if selected[connection['kind']] == connection['provider']:
            matching = next((row for row in rows if _connection_matches(row, connection)), None)
            result[connection['kind']] = matching['id'] if matching else None
    return result


def get_profile(db, profile_id, kind=None):
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError('profileId must be a non-empty string')
    row = db.execute('SELECT * FROM provider_profiles WHERE id=?', (profile_id.strip(),)).fetchone()
    if row is None:
        raise ValueError('Provider profile not found')
    if kind and row['kind'] != kind:
        raise ValueError(f'Provider profile must be a {kind} profile')
    return row


def get_active_profile(db, kind):
    row = db.execute('''
        SELECT p.* FROM provider_profiles p
        JOIN provider_active_profiles a ON a.profile_id=p.id
        WHERE a.kind=?
    ''', (kind,)).fetchone()
    return row


def runtime_profile(row):
    """Return the internal profile fields needed by a provider call."""
    if row is None:
        return None
    row = dict(row)
    model, base_url = effective_profile_connection(row)
    return {
        'id': row['id'], 'kind': row['kind'], 'provider': row['provider'],
        'api_key': row.get('api_key'), 'base_url': base_url,
        'model': model,
    }


def new_profile_id():
    return str(uuid.uuid4())
