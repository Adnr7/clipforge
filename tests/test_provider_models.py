"""Contract tests for explicit, secret-safe provider model discovery."""

import json
from unittest.mock import Mock

import pytest
import requests
from flask import Flask

from backend.database import init_db
from backend.routes.system import _KEYS, system_bp


@pytest.fixture
def app(tmp_path, monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    app = Flask(__name__)
    app.config.update(TESTING=True, DATA_DIR=str(tmp_path / 'data'))
    init_db(app)
    app.register_blueprint(system_bp, url_prefix='/api')
    return app


def provider_response(payload, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode('utf-8')
    response.headers['Content-Type'] = 'application/json'
    return response


def test_openai_compatible_catalog_is_normalized_and_credentials_stay_server_side(app, monkeypatch):
    get = Mock(return_value=provider_response({
        'data': [
            {'id': 'gpt-4o', 'owned_by': 'openai'},
            {'id': 'gpt-4o', 'owned_by': 'duplicate'},
            {'id': 'o3-mini'},
        ],
    }))
    monkeypatch.setattr('backend.services.model_service.requests.get', get)

    response = app.test_client().post('/api/provider-models', json={
        'kind': 'llm', 'provider': 'openai', 'apiKey': 'private-openai-key',
    })

    assert response.status_code == 200, response.json
    assert response.json['source'] == 'remote'
    assert [model['id'] for model in response.json['models']] == ['gpt-4o', 'o3-mini']
    assert 'private-openai-key' not in response.text
    assert get.call_args.args[0] == 'https://api.openai.com/v1/models'
    assert get.call_args.kwargs['headers']['Authorization'] == 'Bearer private-openai-key'
    assert get.call_args.kwargs['allow_redirects'] is False


@pytest.mark.parametrize(('provider', 'base_url', 'expected_url', 'payload'), [
    ('claude', None, 'https://api.anthropic.com/v1/models', {'data': [{'id': 'claude-sonnet-4', 'display_name': 'Claude Sonnet'}]}),
    ('gemini', None, 'https://generativelanguage.googleapis.com/v1beta/models', {'models': [{'name': 'models/gemini-2.5-flash', 'displayName': 'Gemini Flash', 'supportedGenerationMethods': ['generateContent']}]}),
    ('ollama', None, 'http://localhost:11434/api/tags', {'models': [{'name': 'qwen3:8b', 'size': 1}]}),
    ('custom', 'https://gateway.example/v1', 'https://gateway.example/v1/models', {'data': [{'id': 'local-model'}]}),
])
def test_provider_specific_catalog_endpoints(app, monkeypatch, provider, base_url, expected_url, payload):
    get = Mock(return_value=provider_response(payload))
    monkeypatch.setattr('backend.services.model_service.requests.get', get)
    body = {'kind': 'llm', 'provider': provider}
    if base_url:
        body.update(baseUrl=base_url, apiKey='gateway-key')
    elif provider != 'ollama':
        body['apiKey'] = f'{provider}-key'

    response = app.test_client().post('/api/provider-models', json=body)

    assert response.status_code == 200, response.json
    assert response.json['models'][0]['id'] in {'claude-sonnet-4', 'gemini-2.5-flash', 'qwen3:8b', 'local-model'}
    assert get.call_args.args[0] == expected_url
    if provider == 'claude':
        assert get.call_args.kwargs['headers']['x-api-key'] == 'claude-key'
    if provider == 'gemini':
        assert get.call_args.kwargs['headers']['x-goog-api-key'] == 'gemini-key'


def test_static_transcription_catalogs_do_not_make_network_requests(app, monkeypatch):
    get = Mock(side_effect=AssertionError('Static transcription catalogs must not call a provider'))
    monkeypatch.setattr('backend.services.model_service.requests.get', get)
    client = app.test_client()

    whisper = client.post('/api/provider-models', json={'kind': 'transcription', 'provider': 'whisper'})
    deepgram = client.post('/api/provider-models', json={'kind': 'transcription', 'provider': 'deepgram'})

    assert whisper.status_code == 200
    assert whisper.json['source'] == 'local'
    assert 'large-v3' in [model['id'] for model in whisper.json['models']]
    assert deepgram.status_code == 200
    assert deepgram.json['models'] == [{'id': 'nova-2', 'label': 'nova-2', 'ownedBy': 'Deepgram'}]
    get.assert_not_called()


def test_saved_profile_supplies_its_secret_without_returning_or_borrowing_it(app, monkeypatch):
    client = app.test_client()
    created = client.post('/api/provider-profiles', json={
        'name': 'Saved connection', 'kind': 'llm', 'provider': 'custom',
        'apiKey': 'saved-profile-secret', 'baseUrl': 'https://saved.example/v1',
        'model': 'old-model',
    })
    assert created.status_code == 201
    get = Mock(return_value=provider_response({'data': [{'id': 'new-model'}]}))
    monkeypatch.setattr('backend.services.model_service.requests.get', get)

    response = client.post('/api/provider-models', json={
        'kind': 'llm', 'profileId': created.json['id'],
    })

    assert response.status_code == 200
    assert get.call_args.kwargs['headers']['Authorization'] == 'Bearer saved-profile-secret'
    assert 'saved-profile-secret' not in response.text


def test_keyless_saved_profile_does_not_borrow_environment_credentials(app, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-environment-key')
    client = app.test_client()
    created = client.post('/api/provider-profiles', json={
        'name': 'Keyless connection', 'kind': 'llm', 'provider': 'openai',
    })
    assert created.status_code == 201
    get = Mock(side_effect=AssertionError('A keyless saved profile must not use an environment key'))
    monkeypatch.setattr('backend.services.model_service.requests.get', get)

    response = client.post('/api/provider-models', json={'profileId': created.json['id'], 'kind': 'llm'})

    assert response.status_code == 400
    assert 'OPENAI_API_KEY' in response.json['error']
    get.assert_not_called()


def test_invalid_profile_provider_and_remote_error_are_safe(app, monkeypatch):
    client = app.test_client()
    profile = client.post('/api/provider-profiles', json={
        'name': 'DeepSeek', 'kind': 'llm', 'provider': 'deepseek', 'apiKey': 'private-key',
    }).json
    mismatch = client.post('/api/provider-models', json={
        'kind': 'llm', 'provider': 'openai', 'profileId': profile['id'],
    })
    assert mismatch.status_code == 400

    response = provider_response({'error': {'message': 'Rejected private-key'}}, status=401)
    monkeypatch.setattr('backend.services.model_service.requests.get', Mock(return_value=response))
    failed = client.post('/api/provider-models', json={
        'kind': 'llm', 'provider': 'deepseek', 'apiKey': 'private-key',
    })
    assert failed.status_code == 502
    assert 'private-key' not in failed.text
    assert '[redacted]' in failed.json['error']
