"""Provider model discovery with normalized, secret-free responses.

Model catalogs are fetched by the local backend so provider credentials never
need to leave ClipForge's existing local API boundary.  Discovery is explicitly
requested by the UI; it does not persist profiles or change the active model.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import requests

from backend.services.provider_service import (
    DEEPGRAM_ENDPOINT,
    DEEPGRAM_MODEL,
    LLM_PROFILE_PROVIDERS,
    TRANSCRIPTION_PROFILE_PROVIDERS,
    WHISPER_MODELS,
    sanitize_provider_error,
)
from backend.services.render_settings import valid_http_url


MAX_MODELS = 200
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MODEL_ID_LIMIT = 200
MODEL_LABEL_LIMIT = 240
MODEL_DISCOVERY_TIMEOUT = (5, 20)


class ModelDiscoveryError(ValueError):
    """A safe, user-facing model discovery failure."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _redact(value, secret=None):
    message = sanitize_provider_error(value, max_length=500)
    if secret:
        message = message.replace(secret, '[redacted]')
    return message


def _model(model_id, label=None, owned_by=None, context_length=None, supports_images=None):
    if not isinstance(model_id, str):
        return None
    model_id = model_id.strip()
    if not model_id or len(model_id) > MODEL_ID_LIMIT or any(char in model_id for char in '\r\n\x00'):
        return None
    result = {'id': model_id, 'label': model_id}
    if isinstance(label, str) and label.strip():
        result['label'] = label.strip()[:MODEL_LABEL_LIMIT]
    if isinstance(owned_by, str) and owned_by.strip():
        result['ownedBy'] = owned_by.strip()[:MODEL_LABEL_LIMIT]
    if isinstance(context_length, int) and not isinstance(context_length, bool) and context_length > 0:
        result['contextLength'] = min(context_length, 10_000_000)
    if isinstance(supports_images, bool):
        result['supportsImages'] = supports_images
    return result


def _normalize(models):
    """Keep a bounded, deterministic list and discard malformed provider data."""
    result = []
    seen = set()
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict):
            continue
        candidate = _model(
            item.get('id') or item.get('name') or item.get('model'),
            item.get('label') or item.get('display_name') or item.get('displayName'),
            item.get('owned_by') or item.get('ownedBy'),
            item.get('context_length') or item.get('contextLength'),
            item.get('supportsImages'),
        )
        if candidate and candidate['id'] not in seen:
            seen.add(candidate['id'])
            result.append(candidate)
        if len(result) >= MAX_MODELS:
            break
    result.sort(key=lambda value: value['id'].lower())
    return result


def _response_json(response, provider, secret=None):
    try:
        content = response.content
    except (AttributeError, TypeError):
        content = b''
    if isinstance(content, (bytes, bytearray)) and len(content) > MAX_RESPONSE_BYTES:
        raise ModelDiscoveryError(f'{provider}: model catalog response is too large')
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        status = getattr(response, 'status_code', 502)
        raise ModelDiscoveryError(
            f'{provider}: model catalog request failed (HTTP {status}). {_redact(detail, secret)}',
            502,
        ) from exc
    try:
        value = response.json()
    except (TypeError, ValueError) as exc:
        raise ModelDiscoveryError(f'{provider}: model catalog returned invalid JSON') from exc
    if not isinstance(value, dict):
        raise ModelDiscoveryError(f'{provider}: model catalog must return a JSON object')
    return value


def _get_json(url, provider, headers=None, secret=None):
    try:
        response = requests.get(
            url,
            headers=headers or {},
            timeout=MODEL_DISCOVERY_TIMEOUT,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise ModelDiscoveryError(f'{provider}: model catalog request timed out. Try again.') from exc
    except requests.ConnectionError as exc:
        raise ModelDiscoveryError(f'{provider}: could not connect while fetching models. Check the connection.') from exc
    except requests.RequestException as exc:
        raise ModelDiscoveryError(f'{provider}: model catalog request failed. {_redact(exc, secret)}') from exc
    return _response_json(response, provider, secret)


def _append_path(base_url, suffix):
    parsed = urlparse(base_url)
    path = parsed.path.rstrip('/')
    suffix = suffix if suffix.startswith('/') else f'/{suffix}'
    return urlunparse((parsed.scheme, parsed.netloc, f'{path}{suffix}', '', '', ''))


def _replace_path(base_url, path):
    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))


def _openai_models_url(base_url):
    parsed = urlparse(base_url)
    path = parsed.path.rstrip('/')
    for suffix in ('/chat/completions', '/models'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunparse((parsed.scheme, parsed.netloc, f'{path}/models', '', '', ''))


def _ollama_models_url(base_url):
    parsed = urlparse(base_url)
    path = parsed.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    if path.endswith('/api'):
        return urlunparse((parsed.scheme, parsed.netloc, f'{path}/tags', '', '', ''))
    return urlunparse((parsed.scheme, parsed.netloc, f'{path}/api/tags', '', '', ''))


def _gemini_models_url(base_url):
    parsed = urlparse(base_url)
    if parsed.hostname == 'generativelanguage.googleapis.com':
        return _replace_path(base_url, '/v1beta/models')
    return _openai_models_url(base_url)


def _static_models(provider, kind):
    if kind == 'transcription' and provider == 'deepgram':
        return [{
            'id': DEEPGRAM_MODEL,
            'label': DEEPGRAM_MODEL,
            'ownedBy': 'Deepgram',
        }]
    if kind == 'transcription' and provider == 'whisper':
        return _normalize([{'id': model, 'label': model, 'ownedBy': 'Local Whisper'}
                           for model in WHISPER_MODELS])
    return None


def _catalog_from_payload(provider, payload):
    if provider == 'ollama':
        return _normalize([
            {'id': item.get('name') or item.get('model'),
             'label': item.get('name') or item.get('model'),
             'ownedBy': 'Ollama'}
            for item in payload.get('models', []) if isinstance(item, dict)
        ])
    if provider == 'gemini':
        models = []
        for item in payload.get('models', []):
            if not isinstance(item, dict):
                continue
            methods = item.get('supportedGenerationMethods')
            if isinstance(methods, list) and 'generateContent' not in methods:
                continue
            model_id = item.get('name')
            if isinstance(model_id, str) and model_id.startswith('models/'):
                model_id = model_id[7:]
            models.append({
                'id': model_id,
                'label': item.get('displayName') or model_id,
                'ownedBy': 'Google',
            })
        return _normalize(models)
    return _normalize(payload.get('data', payload.get('models', [])))


def _remote_models(provider, base_url, api_key):
    headers = {'Accept': 'application/json'}
    if provider == 'claude':
        parsed = urlparse(base_url)
        path = parsed.path.rstrip('/')
        if path.endswith('/messages'):
            path = path[:-len('/messages')]
        if not path.endswith('/v1'):
            path = f'{path}/v1' if path else '/v1'
        url = urlunparse((parsed.scheme, parsed.netloc, f'{path}/models', '', '', ''))
        if api_key:
            headers['x-api-key'] = api_key
        headers['anthropic-version'] = '2023-06-01'
    elif provider == 'gemini':
        url = _gemini_models_url(base_url)
        if api_key:
            headers['x-goog-api-key'] = api_key
    elif provider == 'ollama':
        url = _ollama_models_url(base_url)
    else:
        url = _openai_models_url(base_url)
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
    return _catalog_from_payload(provider, _get_json(url, provider, headers, api_key)), url


def _connection(provider, kind, profile=None, api_key=None, base_url=None):
    """Resolve a discovery connection without borrowing secrets across profiles."""
    from backend.services.llm_service import PROVIDERS

    profile = dict(profile) if profile is not None else None
    if kind == 'llm':
        allowed = LLM_PROFILE_PROVIDERS
    else:
        allowed = TRANSCRIPTION_PROFILE_PROVIDERS
    if provider not in allowed:
        raise ModelDiscoveryError(f'Unknown {kind} provider', 400)

    if profile is not None and profile.get('provider') != provider:
        raise ModelDiscoveryError('provider does not match profileId', 400)

    if profile is not None:
        stored_key = (profile.get('api_key') or '').strip()
        key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else stored_key
        stored_base = profile.get('base_url') or ''
        if isinstance(base_url, str) and base_url.strip():
            base = base_url.strip()
        elif stored_base:
            base = stored_base
        else:
            config = PROVIDERS.get(provider, {})
            base = config.get('base_url', '')
    else:
        config = PROVIDERS.get(provider, {})
        env_key = config.get('env_key')
        key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else (
            os.environ.get(env_key, '').strip() if env_key else '')
        base = base_url.strip() if isinstance(base_url, str) and base_url.strip() else (
            os.environ.get('CUSTOM_BASE_URL', '').strip() if provider == 'custom'
            else config.get('base_url', ''))

    if provider == 'deepgram':
        return '', DEEPGRAM_ENDPOINT
    if provider == 'whisper':
        return '', ''
    if not base:
        raise ModelDiscoveryError(f'{provider}: configure a base URL before fetching models', 400)
    try:
        base = valid_http_url(base)
    except ValueError as exc:
        raise ModelDiscoveryError(str(exc), 400) from exc
    if provider != 'custom' and not key and provider != 'ollama':
        env_name = PROVIDERS.get(provider, {}).get('env_key') or 'API_KEY'
        raise ModelDiscoveryError(f'{env_name} is not configured', 400)
    return key, base


def discover_models(provider, kind='llm', *, profile=None, api_key=None, base_url=None):
    """Return models for a provider without changing persistence or selection."""
    if not isinstance(kind, str) or kind.strip().lower() not in ('llm', 'transcription'):
        raise ModelDiscoveryError('kind must be llm or transcription', 400)
    kind = kind.strip().lower()
    if not isinstance(provider, str) or not provider.strip():
        raise ModelDiscoveryError('provider is required', 400)
    provider = provider.strip().lower()
    static = _static_models(provider, kind)
    if static is not None:
        return {
            'kind': kind,
            'provider': provider,
            'source': 'local',
            'models': static,
            'manualEntryAllowed': provider not in ('deepgram', 'whisper'),
        }
    if kind != 'llm':
        raise ModelDiscoveryError('This transcription provider has no model catalog', 400)

    api_key, base_url = _connection(
        provider, kind, profile=profile, api_key=api_key, base_url=base_url)
    models, _ = _remote_models(provider, base_url, api_key)
    return {
        'kind': kind,
        'provider': provider,
        'source': 'remote',
        'models': models,
        'manualEntryAllowed': True,
    }
