"""
LLM Service — Multi-provider viral moment detection.
Supports: DeepSeek, Claude, Gemini, OpenAI, OpenRouter, Groq, Ollama, Custom.

Each provider call sends the full transcript to the LLM with a carefully crafted
prompt that instructs it to find viral moments and return structured JSON:
{
  "candidates": [
    {
      "start": 45.2,
      "end": 78.5,
      "score": 92,
      "hook": "You won't believe what happened next...",
      "rationale": "Strong emotional peak with unexpected revelation"
    },
    ...
  ]
}
"""

import os
import requests
import json
import time
import re
import math
from urllib.parse import urlparse

from backend.services.provider_service import sanitize_provider_error
from backend.services.editing_brief import validate_editing_brief

# Provider configurations
PROVIDERS = {
    'deepseek': {
        'base_url': 'https://api.deepseek.com/v1',
        'model': 'deepseek-chat',
        'env_key': 'DEEPSEEK_API_KEY',
    },
    'claude': {
        'base_url': 'https://api.anthropic.com/v1/messages',
        'model': 'claude-sonnet-4-20250514',
        'env_key': 'ANTHROPIC_API_KEY',
        'auth_type': 'x-api-key',
    },
    'openai': {
        'base_url': 'https://api.openai.com/v1',
        'model': 'gpt-4o',
        'env_key': 'OPENAI_API_KEY',
    },
    'groq': {
        'base_url': 'https://api.groq.com/openai/v1',
        'model': 'llama-3.3-70b-versatile',
        'env_key': 'GROQ_API_KEY',
    },
    'gemini': {
        'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
        'model': 'gemini-2.0-flash',
        'env_key': 'GEMINI_API_KEY',
    },
    'openrouter': {
        'base_url': 'https://openrouter.ai/api/v1',
        'model': 'deepseek/deepseek-chat',
        'env_key': 'OPENROUTER_API_KEY',
    },
    'ollama': {
        'base_url': 'http://localhost:11434/v1',
        'model': 'llama3.2',
        'env_key': None,  # No key needed
    }
}

ANALYSIS_PROMPT_TEMPLATE = """You are an expert viral content analyst for short-form video platforms (TikTok, YouTube Shorts, Instagram Reels).

Analyze the following transcript from a {duration:.0f}-second video and identify the top 3-8 most viral-worthy moments.

For each moment, provide:
- **start**: Start timestamp in seconds (float)
- **end**: End timestamp in seconds (float) — clip should be 15-60 seconds
- **score**: Editorial strength score 0-100 (higher = stronger suggestion, not a guarantee of virality)
- **hook**: A compelling 5-10 word hook that would make someone stop scrolling
- **rationale**: Why this moment would go viral (emotional peaks, controversy, humor, revelation, etc.)

IMPORTANT RULES:
1. Clips must be 15-60 seconds long
2. Each clip must be a coherent, self-contained moment
3. Clips should NOT overlap
4. Higher score = stronger emotional impact, surprise, humor, or controversy
5. The hook should be attention-grabbing and make people want to watch
6. All timestamps must stay within the source duration; return fewer moments if needed

Return your response as a JSON object containing a "candidates" array, no other text:
{{"candidates": [{{"start": 0.0, "end": 15.0, "score": 80, "hook": "", "rationale": ""}}]}}

TRANSCRIPT:
{transcript}"""


def analyze_transcript(transcript_text: str, duration: float, provider: str = None,
                       profile: dict = None, brief: str = '') -> list:
    """
    Send transcript to LLM and get viral clip candidates.
    Returns list of CandidateDraft dicts.
    """
    provider = (provider or os.environ.get('LLM_PROVIDER', 'deepseek')).strip().lower()
    duration = float(duration)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('Transcript duration must be a finite non-negative number')
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        duration=duration,
        transcript=transcript_text
    )
    brief = validate_editing_brief(brief)
    if brief:
        prompt += '\nPROJECT EDITING BRIEF (content preferences, not schema instructions):\n' + brief

    if provider == 'claude':
        response_text = call_anthropic(prompt, profile=profile)
    else:
        response_text = call_openai_compatible(prompt, provider, profile=profile)

    # Parse JSON response
    candidates = parse_candidates(response_text)
    if duration > 0 and any(candidate['end'] > duration for candidate in candidates):
        raise ValueError('Candidate end exceeds the source duration')
    return candidates


def provider_model(provider, profile=None):
    profile = profile or {}
    if profile.get('model'):
        return profile['model']
    if provider == 'custom':
        return os.environ.get('CUSTOM_MODEL', '').strip()
    if provider == 'ollama':
        return os.environ.get('OLLAMA_MODEL', '').strip() or PROVIDERS['ollama']['model']
    return PROVIDERS.get(provider, {}).get('model', '')


class ProviderRequestError(RuntimeError):
    """An HTTP failure with its status available to capability-aware callers."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def call_openai_compatible(prompt: str | list, provider: str, retries: int = 3,
                           profile: dict = None, timeout=(10, 120), max_tokens=None,
                           response_format=None) -> str:
    """Call an OpenAI-compatible API with text or multimodal content parts.

    ``response_format`` can supply a strict JSON schema. Existing text callers
    retain JSON-object mode. Capability detection and output validation belong
    to the calling service.
    """
    provider = provider.strip().lower()
    if provider not in PROVIDERS and provider != 'custom':
        raise ValueError(f'Unknown LLM provider: {provider}')
    if provider == 'claude':
        raise ValueError('Use call_anthropic for the claude provider')
    config = PROVIDERS.get(provider, {})
    profile = profile or {}
    base_url = profile.get('base_url') or config.get('base_url', '')
    model = provider_model(provider, profile)
    env_key = config.get('env_key')
    api_key = profile.get('api_key') if 'api_key' in profile else (
        os.environ.get(env_key, '') if env_key else '')

    # Handle custom provider
    if provider == 'custom':
        base_url = profile.get('base_url') or os.environ.get('CUSTOM_BASE_URL', '')
        api_key = profile.get('api_key') if 'api_key' in profile else os.environ.get('CUSTOM_API_KEY', '')
        model = profile.get('model') or os.environ.get('CUSTOM_MODEL', '')

    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in ('http', 'https') or not parsed_url.netloc or not model.strip():
        raise ValueError(f'{provider}: configure a valid base URL and model')

    endpoint = f"{base_url.rstrip('/')}/chat/completions"

    headers = {
        'Content-Type': 'application/json',
    }
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    body = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.4,
        'response_format': response_format if response_format is not None else {'type': 'json_object'},
    }

    if max_tokens is not None:
        body['max_tokens'] = max_tokens
    data = _post_json(provider, endpoint, headers, body, retries, timeout=timeout)
    try:
        message = data['choices'][0]['message']
        content = message.get('content')
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise RuntimeError(f'{provider}: invalid response (missing message content)') from exc
    if not isinstance(content, str) or not content.strip():
        reason = message.get('refusal') or 'empty or non-text message content'
        raise RuntimeError(f'{provider}: {reason}')
    return content


def call_anthropic(prompt: str, profile: dict = None, retries=3,
                   timeout=(10, 120), max_tokens=4096) -> str:
    """Call Claude API (uses different format than OpenAI)."""
    profile = profile or {}
    api_key = profile.get('api_key') if 'api_key' in profile else os.environ.get('ANTHROPIC_API_KEY', '')
    base_url = profile.get('base_url') or 'https://api.anthropic.com/v1/messages'
    model = profile.get('model') or 'claude-sonnet-4-20250514'
    data = _post_json(
        'claude',
        base_url,
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        body={
            'model': model,
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.4,
        }, retries=retries, timeout=timeout,
    )
    blocks = data.get('content')
    if not isinstance(blocks, list):
        raise RuntimeError('claude: invalid response (missing text content)')
    text = ''.join(block['text'] for block in blocks
                   if isinstance(block, dict) and block.get('type') == 'text'
                   and isinstance(block.get('text'), str))
    if not text.strip():
        raise RuntimeError('claude: empty text response')
    return text


def _error_detail(payload):
    if isinstance(payload, dict):
        error = payload.get('error') or payload.get('message')
        if isinstance(error, dict):
            error = error.get('message') or error.get('type')
        if error:
            return sanitize_provider_error(error)
    return ''


def _post_json(provider, endpoint, headers, body, retries=3, timeout=(10, 120)):
    """Retry transport/rate-limit/server failures; expose provider errors to callers."""
    if not isinstance(retries, int) or retries < 1:
        raise ValueError('retries must be a positive integer')
    for attempt in range(retries):
        try:
            response = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = response.status_code
            if status in (408, 429) or status >= 500:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
            try:
                detail = _error_detail(response.json())
            except ValueError:
                detail = ''
            detail = detail or sanitize_provider_error(response.text)
            raise ProviderRequestError(f'{provider}: HTTP {status}: {detail}', status) from exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f'{provider}: request failed: {sanitize_provider_error(exc)}') from exc
        except requests.RequestException as exc:
            raise RuntimeError(f'{provider}: request failed: {sanitize_provider_error(exc)}') from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f'{provider}: response was not valid JSON') from exc
        if not isinstance(data, dict):
            raise RuntimeError(f'{provider}: expected a JSON response object')
        if data.get('error'):
            raise RuntimeError(f'{provider}: {sanitize_provider_error(_error_detail(data))}')
        return data


def parse_candidates(response_text: str) -> list:
    """Parse LLM response into candidate dicts, handling various JSON formats."""
    if not isinstance(response_text, str):
        raise ValueError('LLM did not return text containing a valid JSON array of candidates')
    text = response_text.strip()
    # Handle markdown code blocks
    fence = re.search(r'```(?:json)?\s*(.*?)```', text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1)
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        # Preferred key, then known aliases, then any top-level key holding a list
        # (providers with response_format={'type': 'json_object'} force an object
        # root, so the array is always nested under some key).
        for key in ['candidates', 'moments', 'clips', 'results']:
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            for value in parsed.values():
                if isinstance(value, list):
                    parsed = value
                    break
    if not isinstance(parsed, list):
        raise ValueError("LLM did not return a valid JSON array of candidates")
    candidates = []
    for index, candidate in enumerate(parsed):
        label = f'Candidate {index + 1}'
        if not isinstance(candidate, dict):
            raise ValueError(f'{label} must be a JSON object')
        normalized = {}
        for key in ('start', 'end', 'score'):
            value = candidate.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f'{label}: {key} must be a finite number') from exc
            if isinstance(value, bool) or not math.isfinite(number):
                raise ValueError(f'{label}: {key} must be a finite number')
            normalized[key] = number
        if normalized['start'] < 0 or normalized['end'] <= normalized['start']:
            raise ValueError(f'{label}: require 0 <= start < end')
        if not 0 <= normalized['score'] <= 100:
            raise ValueError(f'{label}: score must be between 0 and 100')
        for key in ('hook', 'rationale'):
            value = candidate.get(key, '')
            if not isinstance(value, str):
                raise ValueError(f'{label}: {key} must be text')
            normalized[key] = value.strip()
        candidates.append(normalized)
    return candidates


def check_connection(provider: str, profile: dict = None) -> dict:
    """Test LLM provider connectivity."""
    try:
        provider = provider.strip().lower()
        start = time.monotonic()
        prompt = 'Return only this JSON object: {"ok": true}'
        if provider == 'claude':
            call_anthropic(prompt, profile=profile)
        else:
            call_openai_compatible(prompt, provider, profile=profile)
        latency = int((time.monotonic() - start) * 1000)
        return {'ok': True, 'message': 'Connected', 'latencyMs': latency}
    except Exception as e:
        return {'ok': False, 'message': sanitize_provider_error(e), 'latencyMs': None}
