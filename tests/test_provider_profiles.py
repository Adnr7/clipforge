"""Real SQLite regressions for legacy imports and exact profile selection."""

import inspect
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from flask import Flask

from backend.database import get_db, init_db
from backend.routes.analysis import _run_analysis, analysis_bp
from backend.routes.system import _KEYS, system_bp
from backend.routes.transcription import transcription_bp
from backend.services.llm_service import PROVIDERS
from backend.services.provider_service import (
    DEEPGRAM_ENDPOINT, DEEPGRAM_MODEL, import_legacy_provider_profiles,
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Do not load developer .env files, inherit keys, or invoke external providers.
    for key in _KEYS:
        monkeypatch.setenv(key, '')
        monkeypatch.delenv(key)
    app = Flask(__name__)
    app.config.update(TESTING=True, DATA_DIR=str(tmp_path / 'data'))
    init_db(app)
    app.register_blueprint(system_bp, url_prefix='/api')
    app.register_blueprint(analysis_bp, url_prefix='/api')
    app.register_blueprint(transcription_bp, url_prefix='/api')
    return app


def listing(client):
    response = client.get('/api/provider-profiles')
    assert response.status_code == 200
    return response.json


def create(client, **fields):
    response = client.post('/api/provider-profiles', json={
        'name': 'Studio', 'kind': 'llm', 'provider': 'deepseek', **fields,
    })
    assert response.status_code == 201, response.json
    return response.json


def test_imports_every_legacy_connection_with_real_models_endpoints_and_hidden_keys(app, monkeypatch):
    secrets = []
    for provider, config in PROVIDERS.items():
        if config['env_key']:
            secret = f'private-{provider}-credential'
            secrets.append(secret)
            monkeypatch.setenv(config['env_key'], f' {secret} ')
    monkeypatch.setenv('CUSTOM_API_KEY', 'private-custom-credential')
    monkeypatch.setenv('CUSTOM_BASE_URL', 'https://gateway.example/v1')
    monkeypatch.setenv('CUSTOM_MODEL', 'studio-analysis')
    monkeypatch.setenv('OLLAMA_MODEL', 'qwen3:8b')
    monkeypatch.setenv('WHISPER_MODEL', 'large-v3')
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'private-deepgram-credential')
    monkeypatch.setenv('LLM_PROVIDER', 'custom')
    monkeypatch.setenv('TRANSCRIPTION_MODE', 'cloud')
    secrets += ['private-custom-credential', 'private-deepgram-credential']
    before_env = {key: os.environ.get(key) for key in _KEYS}
    client = app.test_client()
    response = client.get('/api/provider-profiles')
    assert response.status_code == 200
    data = response.json
    profiles = {profile['provider']: profile for profile in data['profiles']}
    assert len(profiles) == 10
    assert set(profiles) == {*PROVIDERS, 'custom', 'deepgram', 'whisper'}
    for provider, config in PROVIDERS.items():
        profile = profiles[provider]
        assert profile['effectiveBaseUrl'] == profile['baseUrl'] == config['base_url']
        assert profile['effectiveModel'] == profile['model'] == (
            'qwen3:8b' if provider == 'ollama' else config['model'])
        assert profile['model'] in profile['name']
        assert profile['hasApiKey'] is (provider != 'ollama')
    assert profiles['deepgram']['effectiveModel'] == DEEPGRAM_MODEL == 'nova-2'
    assert profiles['deepgram']['effectiveBaseUrl'] == DEEPGRAM_ENDPOINT
    assert profiles['whisper']['model'] == 'large-v3'
    assert profiles['whisper']['effectiveBaseUrl'] == ''
    assert profiles['whisper']['hasApiKey'] is False
    assert profiles['custom']['model'] == 'studio-analysis'
    assert profiles['custom']['baseUrl'] == 'https://gateway.example/v1'
    assert data['active'] == {'llm': None, 'transcription': None}
    assert data['fallback'] == {'llm': profiles['custom']['id'], 'transcription': profiles['deepgram']['id']}
    assert not any(profile['active'] for profile in data['profiles'])
    assert all('apiKey' not in profile and 'api_key' not in profile for profile in data['profiles'])
    assert all(secret not in response.text for secret in secrets)
    assert all(secret not in client.get('/api/settings').text for secret in secrets)
    assert all(secret not in client.get('/api/status').text for secret in secrets)
    assert before_env == {key: os.environ.get(key) for key in _KEYS}
    # Secrets are really copied to persistence, not represented by pseudo rows.
    with app.app_context():
        row = get_db().execute("SELECT * FROM provider_profiles WHERE provider='custom'").fetchone()
        assert row['api_key'] == 'private-custom-credential'


def test_repeated_concurrent_imports_are_idempotent_and_preserve_existing_selection(app, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'legacy-openai-key')
    client = app.test_client()
    original = create(client, name='My existing connection', provider='openai',
                      apiKey='other-key', model='gpt-private')
    assert client.put('/api/provider-profiles/active', json={
        'kind': 'llm', 'profileId': original['id'],
    }).status_code == 200

    def read():
        return listing(app.test_client())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: read(), range(8)))
    assert all(result == results[0] for result in results)
    assert len(results[0]['profiles']) == 2
    existing = next(profile for profile in results[0]['profiles'] if profile['id'] == original['id'])
    assert existing == {**original, 'active': True}
    assert results[0]['active']['llm'] == original['id']
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT COUNT(*) FROM provider_legacy_imports').fetchone()[0] == 1
        assert db.execute('SELECT api_key FROM provider_profiles WHERE id=?',
                          (original['id'],)).fetchone()[0] == 'other-key'


def test_import_adopts_an_exact_existing_profile_without_renaming_or_overwriting_it(app, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'same-key')
    client = app.test_client()
    original = create(client, name='Keep my name', apiKey='same-key')
    data = listing(client)
    assert data['profiles'] == [original]
    assert data['fallback']['llm'] == original['id']


def test_edit_and_delete_of_imported_snapshot_survive_reload_and_reinitialization(app, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'legacy-secret')
    client = app.test_client()
    original = listing(client)['profiles'][0]
    edited = client.put(f"/api/provider-profiles/{original['id']}", json={
        'name': 'Renamed snapshot', 'apiKey': 'replacement-secret', 'model': 'different-model',
    })
    assert edited.status_code == 200
    assert listing(client)['profiles'] == [edited.json]
    assert listing(client)['fallback']['llm'] is None
    # Flask applications cannot register teardown hooks again after serving a
    # request. Simulate a restart with a fresh app bound to the same storage.
    reloaded = Flask(__name__)
    reloaded.config.update(TESTING=True, DATA_DIR=app.config['DATA_DIR'])
    init_db(reloaded)
    reloaded.register_blueprint(system_bp, url_prefix='/api')
    reloaded_client = reloaded.test_client()
    assert listing(reloaded_client)['profiles'] == [edited.json]
    assert reloaded_client.delete(f"/api/provider-profiles/{original['id']}").status_code == 204
    restarted = Flask(__name__)
    restarted.config.update(TESTING=True, DATA_DIR=app.config['DATA_DIR'])
    init_db(restarted)
    restarted.register_blueprint(system_bp, url_prefix='/api')
    assert listing(restarted.test_client())['profiles'] == []
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM provider_legacy_imports').fetchone()[0] == 1


def test_changed_legacy_connection_adds_a_new_snapshot_preserving_prior_models_and_keys(app, monkeypatch):
    monkeypatch.setenv('CUSTOM_API_KEY', 'original-secret')
    monkeypatch.setenv('CUSTOM_BASE_URL', 'https://first.example/v1')
    monkeypatch.setenv('CUSTOM_MODEL', 'first-model')
    monkeypatch.setenv('LLM_PROVIDER', 'custom')
    client = app.test_client()
    original = listing(client)['profiles'][0]
    assert client.put('/api/provider-profiles/active', json={
        'kind': 'llm', 'profileId': original['id'],
    }).status_code == 200
    assert client.put('/api/settings', json={
        'CUSTOM_API_KEY': 'next-secret', 'CUSTOM_BASE_URL': 'https://second.example/v1',
        'CUSTOM_MODEL': 'next-model',
    }).status_code == 200
    data = listing(client)
    assert len(data['profiles']) == 2
    previous = next(profile for profile in data['profiles'] if profile['id'] == original['id'])
    assert previous == {**original, 'active': True}
    latest = next(profile for profile in data['profiles'] if profile['id'] != original['id'])
    assert latest['model'] == 'next-model'
    assert latest['baseUrl'] == 'https://second.example/v1'
    assert data['fallback']['llm'] == latest['id']
    assert data['active']['llm'] == original['id']
    with app.app_context():
        assert get_db().execute('SELECT api_key FROM provider_profiles WHERE id=?',
                                (original['id'],)).fetchone()[0] == 'original-secret'


def test_incomplete_legacy_custom_connection_is_visible_and_can_be_repaired(app, monkeypatch):
    monkeypatch.setenv('CUSTOM_API_KEY', 'saved-but-incomplete-key')
    client = app.test_client()
    profile = listing(client)['profiles'][0]
    assert profile['provider'] == 'custom' and profile['hasApiKey'] is True
    assert profile['effectiveBaseUrl'] == profile['effectiveModel'] == ''
    response = client.put(f"/api/provider-profiles/{profile['id']}", json={
        'baseUrl': 'https://repaired.example/v1', 'model': 'repaired', 'apiKey': '',
    })
    assert response.status_code == 200
    assert response.json['hasApiKey'] is True
    assert len(listing(client)['profiles']) == 1


@pytest.mark.parametrize('blank', ['', '   ', None])
def test_blank_profile_key_preserves_saved_credentials(app, monkeypatch, blank):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'unrelated-environment-key')
    client = app.test_client()
    profile = create(client, name='Saved transcription', kind='transcription',
                     provider='deepgram', apiKey='saved-profile-key')
    response = client.put(f"/api/provider-profiles/{profile['id']}", json={
        'name': 'Renamed transcription', 'apiKey': blank,
    })
    assert response.status_code == 200
    assert response.json['hasApiKey'] is True
    assert 'saved-profile-key' not in response.text
    with app.app_context():
        assert get_db().execute('SELECT api_key FROM provider_profiles WHERE id=?',
                                (profile['id'],)).fetchone()[0] == 'saved-profile-key'


@pytest.mark.parametrize('provider,model,endpoint', [
    ('deepseek', 'deepseek-chat', 'https://api.deepseek.com/v1'),
    ('claude', 'claude-sonnet-4-20250514', 'https://api.anthropic.com/v1/messages'),
    ('ollama', 'saved-ollama-default', 'http://localhost:11434/v1'),
    ('whisper', 'small.en', ''),
    ('deepgram', 'nova-2', 'https://api.deepgram.com/v1/listen'),
])
def test_effective_defaults_are_accurate_without_overwriting_raw_editor_fields(app, monkeypatch, provider, model, endpoint):
    monkeypatch.setenv('OLLAMA_MODEL', 'saved-ollama-default')
    monkeypatch.setenv('WHISPER_MODEL', 'small.en')
    profile = create(app.test_client(), provider=provider, apiKey='key',
                     kind='transcription' if provider in ('deepgram', 'whisper') else 'llm')
    assert profile['effectiveModel'] == model
    assert profile['effectiveBaseUrl'] == endpoint
    if provider != 'deepgram':
        assert profile['model'] == profile['baseUrl'] == ''


def test_deepgram_reports_the_server_model_even_for_old_unused_overrides(app):
    client = app.test_client()
    profile = create(client, provider='deepgram', kind='transcription', apiKey='key')
    with app.app_context():
        db = get_db()
        db.execute('UPDATE provider_profiles SET model=?, base_url=? WHERE id=?',
                   ('old-ignored-model', 'https://unused.example/v1', profile['id']))
        db.commit()
    saved = listing(client)['profiles'][0]
    assert saved['effectiveModel'] == 'nova-2'
    assert saved['effectiveBaseUrl'] == DEEPGRAM_ENDPOINT
    assert client.post('/api/provider-profiles', json={
        'name': 'Misleading', 'kind': 'transcription', 'provider': 'deepgram',
        'apiKey': 'key', 'model': 'nova-3',
    }).status_code == 400


def test_activation_and_test_use_the_exact_saved_snapshot_after_legacy_settings_change(app, monkeypatch):
    monkeypatch.setenv('CUSTOM_API_KEY', 'snapshot-key')
    monkeypatch.setenv('CUSTOM_BASE_URL', 'https://snapshot.example/v1')
    monkeypatch.setenv('CUSTOM_MODEL', 'snapshot-model')
    client = app.test_client()
    profile = listing(client)['profiles'][0]
    monkeypatch.setenv('CUSTOM_API_KEY', 'different-environment-key')
    monkeypatch.setenv('CUSTOM_BASE_URL', 'https://other.example/v1')
    monkeypatch.setenv('CUSTOM_MODEL', 'different-environment-model')
    assert client.put('/api/provider-profiles/active', json={
        'kind': 'llm', 'profileId': profile['id'],
    }).status_code == 200
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"choices":[{"message":{"content":"{}"}}]}'
    post = Mock(return_value=response)
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    # Empty request tests the active profile, not a supplied provider/env path.
    result = client.post('/api/test-connection', json={})
    assert result.json['ok'] is True
    assert post.call_args.args[0] == 'https://snapshot.example/v1/chat/completions'
    assert post.call_args.kwargs['json']['model'] == 'snapshot-model'
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer snapshot-key'


def test_secret_echo_in_connection_errors_is_redacted(app, monkeypatch):
    client = app.test_client()
    profile = create(client, apiKey='private-profile-secret')
    monkeypatch.setattr('backend.services.llm_service.check_connection', lambda *args: {
        'ok': False, 'message': 'Rejected private-profile-secret', 'latencyMs': None,
    })
    response = client.post('/api/test-connection', json={'profileId': profile['id']})
    assert 'private-profile-secret' not in response.text
    assert response.json['message'] == 'Rejected [redacted]'


def test_unsaved_or_invalid_drafts_cannot_replace_persisted_profile_or_selection(app):
    client = app.test_client()
    profile = create(client, provider='custom', apiKey='saved-secret',
                     baseUrl='https://saved.example/v1', model='saved-model')
    client.put('/api/provider-profiles/active', json={'kind': 'llm', 'profileId': profile['id']})
    # Building/editing a draft alone changes nothing. A rejected save also leaves
    # the saved connection and its active selection authoritative.
    draft = {**profile, 'name': 'Unsaved name', 'model': 'unsaved-model'}
    assert listing(client)['profiles'] == [{**profile, 'active': True}]
    assert draft['model'] != profile['model']
    response = client.put(f"/api/provider-profiles/{profile['id']}", json={
        'name': draft['name'], 'model': draft['model'], 'baseUrl': 'not a URL',
    })
    assert response.status_code == 400
    data = listing(client)
    assert data['profiles'] == [{**profile, 'active': True}]
    assert data['active']['llm'] == profile['id']
    assert client.put(f"/api/provider-profiles/{profile['id']}", json={
        'provider': 'openai',
    }).status_code == 400
    with app.app_context():
        assert get_db().execute('SELECT api_key FROM provider_profiles WHERE id=?',
                                (profile['id'],)).fetchone()[0] == 'saved-secret'


def test_import_failure_rolls_back_new_profiles_and_history_together(app, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'legacy-key')
    with app.app_context():
        db = get_db()
        db.execute('''CREATE TABLE provider_legacy_imports (
            fingerprint TEXT PRIMARY KEY, profile_id TEXT NOT NULL, created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TRIGGER reject_import BEFORE INSERT ON provider_legacy_imports
            BEGIN SELECT RAISE(ABORT, 'test import failure'); END''')
        db.commit()
        with pytest.raises(sqlite3.IntegrityError, match='test import failure'):
            import_legacy_provider_profiles(db)
        assert db.execute('SELECT COUNT(*) FROM provider_profiles').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM provider_legacy_imports').fetchone()[0] == 0


def test_blank_new_profile_key_does_not_borrow_a_connected_environment_key(app, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'connected-legacy-key')
    client = app.test_client()
    profile = create(client, apiKey='   ')
    assert profile['hasApiKey'] is False
    network = Mock(side_effect=AssertionError('A keyless profile must not borrow an environment key'))
    monkeypatch.setattr('backend.services.llm_service.requests.post', network)
    response = client.post('/api/test-connection', json={'profileId': profile['id']})
    assert response.status_code == 200
    assert response.json['ok'] is False
    network.assert_not_called()


def test_deepgram_activation_rejects_missing_profile_key_even_with_a_connected_environment(app, monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'unrelated-environment-key')
    client = app.test_client()
    profile = create(client, kind='transcription', provider='deepgram', apiKey='key')
    with app.app_context():
        db = get_db()
        db.execute('UPDATE provider_profiles SET api_key=NULL WHERE id=?', (profile['id'],))
        db.commit()
    response = client.put('/api/provider-profiles/active', json={
        'kind': 'transcription', 'profileId': profile['id'],
    })
    assert response.status_code == 400
    assert listing(client)['active']['transcription'] is None


def test_local_imported_profiles_keep_saved_models_when_processing_defaults_change(app, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    monkeypatch.setenv('OLLAMA_MODEL', 'saved-llama')
    monkeypatch.setenv('TRANSCRIPTION_MODE', 'local')
    monkeypatch.setenv('WHISPER_MODEL', 'small.en')
    client = app.test_client()
    original = listing(client)['profiles']
    assert len(original) == 2
    monkeypatch.setenv('OLLAMA_MODEL', 'next-llama')
    monkeypatch.setenv('WHISPER_MODEL', 'large-v3')
    data = listing(client)
    assert len(data['profiles']) == 4
    for profile in original:
        saved = next(item for item in data['profiles'] if item['id'] == profile['id'])
        assert saved == profile


@pytest.mark.parametrize('kind,provider', [
    ('llm', 'custom'), ('transcription', 'deepgram'), ('transcription', 'whisper'),
])
def test_pipeline_dispatch_uses_active_saved_credentials_and_model(app, monkeypatch, kind, provider):
    client = app.test_client()
    fields = ({'baseUrl': 'https://saved.example/v1', 'model': 'saved-model'} if kind == 'llm'
              else {'model': 'small.en'} if provider == 'whisper' else {})
    profile = create(client, kind=kind, provider=provider,
                     apiKey=None if provider == 'whisper' else 'saved-profile-secret', **fields)
    client.put('/api/provider-profiles/active', json={'kind': kind, 'profileId': profile['id']})
    monkeypatch.setenv('CUSTOM_API_KEY', 'unrelated-secret')
    monkeypatch.setenv('CUSTOM_BASE_URL', 'https://different.example/v1')
    monkeypatch.setenv('CUSTOM_MODEL', 'different-model')
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'unrelated-deepgram-secret')
    monkeypatch.setenv('WHISPER_MODEL', 'large-v3')
    source = Path(app.config['DATA_DIR']) / 'source.wav'
    source.write_bytes(b'fixture media')
    with app.app_context():
        db = get_db()
        db.execute('''INSERT INTO projects
            (id, source_path, source_duration, status, transcription_mode, created_at, updated_at)
            VALUES ('project', ?, 30, 'transcribed', 'whisper', 'now', 'now')''', (str(source),))
        db.execute('''INSERT INTO transcripts (id, project_id, engine, raw_json, created_at)
            VALUES ('transcript', 'project', 'test', ?, 'now')''',
                   (json.dumps({'segments': [{'text': 'An idea', 'start': 0, 'end': 30}]}),))
        db.commit()
    dispatched = []

    def thread(target, args, **kwargs):
        return SimpleNamespace(start=lambda: dispatched.append(args))

    module = 'analysis' if kind == 'llm' else 'transcription'
    monkeypatch.setattr(f'backend.routes.{module}.threading', SimpleNamespace(Thread=thread))
    endpoint = 'analyze' if kind == 'llm' else 'transcribe'
    response = client.post(f'/api/projects/project/{endpoint}', json={})
    assert response.status_code == 202, response.json
    assert len(dispatched) == 1
    if kind == 'llm':
        bound = inspect.signature(_run_analysis).bind(*dispatched[0])
        bound.apply_defaults()
        assert bound.arguments['mode'] == 'transcript'
        runtime = bound.arguments['profile']
        assert runtime['id'] == profile['id']
        assert runtime['api_key'] == 'saved-profile-secret'
        assert runtime['model'] == 'saved-model'
        assert runtime['base_url'] == 'https://saved.example/v1'
    elif provider == 'deepgram':
        assert dispatched[0][3] == 'cloud'
        assert dispatched[0][4] == 'saved-profile-secret'
    else:
        assert dispatched[0][3] == 'whisper'
        assert dispatched[0][-1] == 'small.en'
