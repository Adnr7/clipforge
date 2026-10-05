import json
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from backend.app import create_app
from backend.database import get_db
from backend.routes import rendering
from backend.services.caption_service import build_drawtext_filters
from backend.services.llm_service import call_openai_compatible
from backend.services.render_settings import (
    validate_caption_settings, validate_video_filters,
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def project(app, tmp_path):
    project_id = 'settings-project'
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    with app.app_context():
        db = get_db()
        db.execute('''
            INSERT INTO projects
                (id, source_path, source_duration, status, caption_style, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?)
        ''', (project_id, str(source), 30, 'transcribed', 'classic', 'now', 'now'))
        db.execute('''
            INSERT INTO transcripts (id, project_id, engine, raw_json, created_at)
            VALUES (?,?,?,?,?)
        ''', ('transcript', project_id, 'test', json.dumps({
            'words': [{'text': 'Hello', 'start': 0, 'end': 1}],
            'segments': [],
        }), 'now'))
        db.execute('''
            INSERT INTO candidates
                (id, project_id, start_sec, end_sec, score, hook, rationale, rank, selected)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', ('candidate', project_id, 0, 10, 80, 'Hook', 'Reason', 1, 1))
        db.commit()
    return project_id


def test_caption_customization_validates_and_controls_filter_output():
    settings = validate_caption_settings({
        'preset': 'typewriter', 'placement': 'top', 'fontSize': 72,
        'fontColor': '#12ab34', 'outlineWidth': 5, 'outlineColor': '#000000',
        'shadowEnabled': True, 'shadowColor': 'blue', 'shadowX': 4, 'shadowY': 5,
        'backgroundEnabled': True, 'backgroundColor': '#112233',
        'backgroundOpacity': 0.75, 'backgroundPadding': 8,
        'chunkMode': 'word', 'wordsPerChunk': 1,
    })
    filters = build_drawtext_filters([
        {'text': 'one', 'start': 0, 'end': 1},
        {'text': 'two', 'start': 1, 'end': 2},
    ], settings=settings)
    assert len(filters) == 2
    assert ':fontsize=72' in filters[0]
    assert ':y=h/8' in filters[0]
    assert ':shadowx=4:shadowy=5:shadowcolor=blue' in filters[0]
    assert ':box=1:boxcolor=#112233@0.750:boxborderw=8' in filters[0]
    with pytest.raises(ValueError, match='fontSize'):
        validate_caption_settings({'fontSize': 1000})
    with pytest.raises(ValueError, match='Unknown caption setting'):
        validate_caption_settings({'font': 'unsafe'})


def test_video_filters_are_bounded_and_fixed():
    assert validate_video_filters({
        'brightness': 0.2, 'contrast': 1.4, 'saturation': 0.8,
        'blur': 2, 'sharpen': 0,
    }) == {
        'brightness': 0.2, 'contrast': 1.4, 'saturation': 0.8,
        'blur': 2, 'sharpen': 0,
    }
    with pytest.raises(ValueError, match='brightness'):
        validate_video_filters({'brightness': 2})
    with pytest.raises(ValueError, match='Unknown video filter'):
        validate_video_filters({'vf': 'drawtext=text=unsafe'})


def test_render_contract_and_ai_suggestion_are_preview_only(app, project):
    client = app.test_client()
    contract = client.get('/api/render-settings')
    assert contract.status_code == 200
    assert len(contract.json['caption']['presets']) >= 8
    assert contract.json['videoFilters']['manualOnly'] is True

    suggestion = client.post(f'/api/projects/{project}/filter-suggestions', json={
        'videoFilters': {'brightness': 0.1, 'sharpen': 1},
        'model': 'metadata-only-test', 'rationale': 'Preview suggestion',
    })
    assert suggestion.status_code == 201
    assert suggestion.json['applied'] is False
    assert suggestion.json['settings']['brightness'] == 0.1
    assert client.get(f'/api/projects/{project}/filter-suggestions').json[0]['applied'] is False
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM clips').fetchone()[0] == 0


def test_provider_profiles_persist_active_selection_and_mask_secrets(app):
    client = app.test_client()
    created = client.post('/api/provider-profiles', json={
        'name': 'Primary DeepSeek', 'kind': 'llm', 'provider': 'deepseek',
        'apiKey': 'super-secret', 'model': 'deepseek-chat',
    })
    assert created.status_code == 201
    profile = created.json
    assert profile['hasApiKey'] is True
    assert 'apiKey' not in profile and 'super-secret' not in created.text

    selected = client.put('/api/provider-profiles/active', json={
        'kind': 'llm', 'profileId': profile['id'],
    })
    assert selected.status_code == 200
    listing = client.get('/api/provider-profiles')
    assert listing.json['active']['llm'] == profile['id']
    saved = next(item for item in listing.json['profiles'] if item['id'] == profile['id'])
    assert saved == {**profile, 'active': True}
    assert 'super-secret' not in listing.text

    updated = client.put(f"/api/provider-profiles/{profile['id']}", json={'name': 'Renamed'})
    assert updated.status_code == 200
    assert updated.json['hasApiKey'] is True
    assert 'apiKey' not in updated.json


@pytest.mark.parametrize('model', [
    'tiny', 'tiny.en', 'base', 'base.en', 'small', 'small.en', 'medium', 'medium.en',
    'large', 'large-v1', 'large-v2', 'large-v3', 'large-v3-turbo', 'turbo', None, '',
])
def test_whisper_profile_accepts_known_models_and_optional_default(app, model):
    client = app.test_client()
    response = client.post('/api/provider-profiles', json={
        'name': 'Local', 'kind': 'transcription', 'provider': 'whisper', 'model': model,
    })
    assert response.status_code == 201
    assert response.json['model'] == (model or '')


@pytest.mark.parametrize('model', ['unknown', 'large.en', '/tmp/custom.pt', 7, ['base']])
def test_whisper_profile_rejects_unknown_models_on_create_and_update(app, model):
    client = app.test_client()
    profile = {'name': 'Local', 'kind': 'transcription', 'provider': 'whisper', 'model': 'base'}
    baseline = client.get('/api/provider-profiles').json['profiles']
    assert client.post('/api/provider-profiles', json={**profile, 'model': model}).status_code == 400
    assert client.get('/api/provider-profiles').json['profiles'] == baseline
    created = client.post('/api/provider-profiles', json=profile)
    assert created.status_code == 201
    response = client.put(f"/api/provider-profiles/{created.json['id']}", json={'model': model})
    assert response.status_code == 400
    saved = next(item for item in client.get('/api/provider-profiles').json['profiles']
                 if item['id'] == created.json['id'])
    assert saved['model'] == 'base'
    assert saved == created.json


def test_profile_credentials_are_used_only_inside_provider_call(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"choices":[{"message":{"content":"{}"}}]}'
    with patch('backend.services.llm_service.requests.post', return_value=response) as post:
        assert call_openai_compatible('Return JSON', 'custom', profile={
            'provider': 'custom', 'base_url': 'https://llm.example/v1',
            'model': 'private-model', 'api_key': 'profile-secret',
        }) == '{}'
    assert post.call_args.args[0] == 'https://llm.example/v1/chat/completions'
    assert post.call_args.kwargs['json']['model'] == 'private-model'
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer profile-secret'


def test_html_gateway_errors_are_short_and_useful():
    response = requests.Response()
    response.status_code = 502
    response._content = b'<html><head><title>502 Bad Gateway</title></head><body><h1>upstream down</h1><script>secret</script></body></html>'
    with patch('backend.services.llm_service.requests.post', return_value=response), \
            patch('backend.services.llm_service.time.sleep'):
        with pytest.raises(RuntimeError) as error:
            call_openai_compatible('Return JSON', 'openai')
    message = str(error.value)
    assert '502 Bad Gateway upstream down' in message
    assert '<html>' not in message and 'secret' not in message


def test_completed_render_request_reuses_clip_without_second_worker(app, project, monkeypatch):
    submitted = []

    class Executor:
        def submit(self, function, *args):
            submitted.append((function, args))

    monkeypatch.setattr(rendering, '_RENDER_EXECUTOR', Executor())
    client = app.test_client()
    first = client.post(f'/api/projects/{project}/render/candidate', json={
        'captionSettings': {'preset': 'sunset', 'placement': 'center'},
        'videoFilters': {'contrast': 1.2},
    })
    assert first.status_code == 202
    assert len(submitted) == 1
    clip_id = first.json['clipId']
    with app.app_context():
        db = get_db()
        output = Path(app.config['DATA_DIR']) / 'projects' / project / 'clips' / 'done.mp4'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'done')
        db.execute('UPDATE clips SET status=?, output_path=? WHERE id=?', ('done', str(output), clip_id))
        db.commit()
    second = client.post(f'/api/projects/{project}/render/candidate', json={
        'captionSettings': {'preset': 'sunset', 'placement': 'center'},
        'videoFilters': {'contrast': 1.2},
    })
    assert second.status_code == 200
    assert second.json == {'clipId': clip_id, 'status': 'done'}
    assert len(submitted) == 1
