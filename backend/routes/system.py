from flask import Blueprint, jsonify, request
from backend.config import Config
import os
import threading
from datetime import datetime, timezone
from dotenv import set_key

from backend.database import get_db
from backend.services.provider_service import (
    get_active_profile, get_profile, new_profile_id, profile_public,
    import_legacy_provider_profiles, legacy_fallback_profile_ids,
    runtime_profile, validate_profile_payload, WHISPER_MODELS,
)
from backend.services.render_settings import CAPTION_PRESETS

_SETTINGS_LOCK = threading.Lock()
_KEYS = {
    'DEEPGRAM_API_KEY', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY',
    'ANTHROPIC_API_KEY', 'GROQ_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY',
    'CUSTOM_API_KEY', 'CUSTOM_BASE_URL', 'CUSTOM_MODEL', 'OLLAMA_MODEL',
    'LLM_PROVIDER', 'TRANSCRIPTION_MODE', 'WHISPER_MODEL', 'CAPTION_STYLE',
}

system_bp = Blueprint('system', __name__)


@system_bp.route('/status', methods=['GET'])
def get_status():
    """Return environment status — API keys, FFmpeg, etc."""
    return jsonify(Config.get_env_status())


@system_bp.route('/test-connection', methods=['POST'])
def test_connection():
    """Test LLM provider connectivity."""
    from backend.services.llm_service import PROVIDERS, check_connection
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400
    profile = None
    profile_id = data.get('profileId')
    if profile_id is not None:
        try:
            profile = get_profile(get_db(), profile_id, kind='llm')
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
    provider_was_supplied = 'provider' in data
    provider = data.get('provider')
    if profile is not None and provider is not None:
        if not isinstance(provider, str) or provider.strip().lower() != profile['provider']:
            return jsonify({'error': 'provider does not match profileId'}), 400
    if provider is None and profile is not None:
        provider = profile['provider']
    if provider is None and not provider_was_supplied:
        active = get_active_profile(get_db(), 'llm')
        if active is not None:
            profile = active
            provider = active['provider']
        else:
            provider = Config.get_llm_provider()
    if not isinstance(provider, str) or provider.strip().lower() not in (*PROVIDERS, 'custom'):
        return jsonify({'error': 'Unknown LLM provider'}), 400
    provider = provider.strip().lower()
    env_key = PROVIDERS.get(provider, {}).get('env_key')
    if env_key and profile is None and not os.environ.get(env_key, '').strip():
        return jsonify({'ok': False, 'message': f'{env_key} not configured', 'latencyMs': None})
    if env_key and profile is not None and not (profile['api_key'] or '').strip():
        return jsonify({'ok': False, 'message': f'{env_key} not configured', 'latencyMs': None})
    result = check_connection(provider, runtime_profile(profile)) if profile is not None else check_connection(provider)
    # Some upstream errors echo supplied credentials. Never send those back to
    # the browser, even though the profile itself is already write-only.
    legacy_key = 'CUSTOM_API_KEY' if provider == 'custom' else env_key
    secret = ((profile['api_key'] or '') if profile is not None else
              os.environ.get(legacy_key, '') if legacy_key else '').strip()
    if secret and isinstance(result.get('message'), str):
        result = {**result, 'message': result['message'].replace(secret, '[redacted]')}
    return jsonify(result)


def _profile_active_ids(db):
    return {row['kind']: row['profile_id'] for row in db.execute(
        'SELECT kind, profile_id FROM provider_active_profiles').fetchall()}


@system_bp.route('/provider-profiles', methods=['GET'])
def list_provider_profiles():
    db = get_db()
    # Serialize with legacy settings writes so a snapshot never combines fields
    # from two different saved connections. Existing profiles/selections survive.
    with _SETTINGS_LOCK:
        connections = import_legacy_provider_profiles(db)
        active = _profile_active_ids(db)
        rows = db.execute('SELECT * FROM provider_profiles ORDER BY kind, name, created_at, id').fetchall()
        fallback = legacy_fallback_profile_ids(rows, connections)
    return jsonify({
        'profiles': [profile_public(row, active.get(row['kind'])) for row in rows],
        'active': {'llm': active.get('llm'), 'transcription': active.get('transcription')},
        'fallback': fallback,
    })


@system_bp.route('/provider-profiles', methods=['POST'])
def create_provider_profile():
    data = request.get_json(silent=True)
    try:
        values = validate_profile_payload(data)
        profile_id = new_profile_id()
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.execute('''
            INSERT INTO provider_profiles
                (id, name, kind, provider, api_key, base_url, model, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (profile_id, values['name'], values['kind'], values['provider'], values['api_key'],
              values['base_url'], values['model'], now, now))
        db.commit()
        row = db.execute('SELECT * FROM provider_profiles WHERE id=?', (profile_id,)).fetchone()
        return jsonify(profile_public(row)), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@system_bp.route('/provider-profiles/<profile_id>', methods=['PUT'])
def update_provider_profile(profile_id):
    db = get_db()
    row = db.execute('SELECT * FROM provider_profiles WHERE id=?', (profile_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Provider profile not found'}), 404
    try:
        values = validate_profile_payload(request.get_json(silent=True), existing=row)
        now = datetime.now(timezone.utc).isoformat()
        db.execute('''
            UPDATE provider_profiles
            SET name=?, kind=?, provider=?, api_key=?, base_url=?, model=?, updated_at=?
            WHERE id=?
        ''', (values['name'], values['kind'], values['provider'], values['api_key'],
              values['base_url'], values['model'], now, profile_id))
        db.commit()
        row = db.execute('SELECT * FROM provider_profiles WHERE id=?', (profile_id,)).fetchone()
        active = _profile_active_ids(db)
        return jsonify(profile_public(row, active.get(row['kind'])))
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), 400


@system_bp.route('/provider-profiles/<profile_id>', methods=['DELETE'])
def delete_provider_profile(profile_id):
    db = get_db()
    if not db.execute('SELECT 1 FROM provider_profiles WHERE id=?', (profile_id,)).fetchone():
        return jsonify({'error': 'Provider profile not found'}), 404
    db.execute('DELETE FROM provider_active_profiles WHERE profile_id=?', (profile_id,))
    db.execute('DELETE FROM provider_profiles WHERE id=?', (profile_id,))
    db.commit()
    return '', 204


@system_bp.route('/provider-profiles/active', methods=['PUT'])
def select_active_provider_profile():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {'kind', 'profileId'}:
        return jsonify({'error': 'kind and profileId are required'}), 400
    kind = data.get('kind')
    profile_id = data.get('profileId')
    if kind not in ('llm', 'transcription'):
        return jsonify({'error': 'kind must be llm or transcription'}), 400
    db = get_db()
    try:
        if profile_id is None:
            db.execute('DELETE FROM provider_active_profiles WHERE kind=?', (kind,))
        else:
            row = get_profile(db, profile_id, kind=kind)
            if row['provider'] == 'deepgram' and not (row['api_key'] or '').strip():
                raise ValueError('Deepgram profiles require a saved apiKey before activation')
            db.execute('''
                INSERT INTO provider_active_profiles (kind, profile_id) VALUES (?,?)
                ON CONFLICT(kind) DO UPDATE SET profile_id=excluded.profile_id
            ''', (kind, row['id']))
        db.commit()
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), 400
    active = _profile_active_ids(db)
    return jsonify({'active': {'llm': active.get('llm'), 'transcription': active.get('transcription')}})


@system_bp.route('/settings', methods=['GET'])
def get_settings():
    """Get app settings (masked — never expose actual keys)."""
    mode = os.environ.get('TRANSCRIPTION_MODE', 'cloud')
    return jsonify({
        'llmProvider': Config.get_llm_provider(),
        'transcriptionMode': 'local' if mode == 'whisper' else mode,
        'whisperModel': os.environ.get('WHISPER_MODEL', 'base'),
        'captionStyle': os.environ.get('CAPTION_STYLE', 'classic'),
        'dataDir': Config.get_data_dir(),
        'customBaseUrl': os.environ.get('CUSTOM_BASE_URL', ''),
        'customModel': os.environ.get('CUSTOM_MODEL', ''),
        'ollamaModel': os.environ.get('OLLAMA_MODEL', 'llama3.2'),
    })


@system_bp.route('/settings', methods=['PUT'])
def update_settings():
    """Update app settings (persisted to .env in data dir)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - _KEYS:
        return jsonify({'error': 'Unrecognized settings or invalid JSON object'}), 400
    enums = {
        'LLM_PROVIDER': {'deepseek', 'openai', 'claude', 'groq', 'gemini', 'openrouter', 'ollama', 'custom'},
        'TRANSCRIPTION_MODE': {'cloud', 'whisper'},
        'CAPTION_STYLE': set(CAPTION_PRESETS),
        'WHISPER_MODEL': WHISPER_MODELS,
    }
    values = {}
    for key, value in data.items():
        if value is None:
            continue
        if not isinstance(value, str) or '\n' in value or '\r' in value or '\x00' in value:
            return jsonify({'error': f'Invalid value for {key}'}), 400
        value = value.strip()
        if key == 'TRANSCRIPTION_MODE' and value == 'local':
            value = 'whisper'
        if key in enums and value not in enums[key]:
            return jsonify({'error': f'Invalid value for {key}'}), 400
        values[key] = value
    env_file = os.path.join(Config.get_data_dir(), '.env')
    with _SETTINGS_LOCK:
        os.makedirs(Config.get_data_dir(), exist_ok=True)
        descriptor = os.open(env_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)
        for key, value in values.items():
            set_key(env_file, key, value)
        os.environ.update(values)
    return jsonify({'ok': True})
