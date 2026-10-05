"""Real persistence, FFmpeg previews, streaming ZIPs, and mocked LLM boundaries."""

import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from backend.app import create_app
from backend.routes import render_settings, rendering
from backend.services import media_service, suggestion_service
from tests.test_pipeline_routes import app, client, jobs, services, seed_project, execute, rows


def test_saved_settings_survive_restart_and_feed_render_defaults(app, client, jobs, services):
    seed_project(app)
    saved = client.put('/api/projects/project/render-settings', json={
        'captionSettings': {'preset': 'mono', 'fontFamily': 'mono', 'verticalPosition': 42,
                            'uppercase': True, 'enabled': True},
        'videoFilters': {'brightness': 0.12, 'blur': 0.5},
    })
    assert saved.status_code == 200
    reopened = create_app().test_client()
    project = reopened.get('/api/projects/project').json['project']
    assert project['captionSettings'] == saved.json['captionSettings']
    assert project['videoFilters'] == saved.json['videoFilters']
    assert project['caption_style'] == 'mono'
    assert json.loads(project['video_filters_json'])['brightness'] == 0.12
    # Replacing one settings group preserves the omitted group.
    assert client.put('/api/projects/project/render-settings', json={
        'videoFilters': {'contrast': 1.2},
    }).json['captionSettings'] == saved.json['captionSettings']
    response = client.post('/api/projects/project/render/candidate-project-0', json={})
    assert response.status_code == 202
    jobs.run_all()
    assert services.render.call_args.kwargs['caption_settings'] == saved.json['captionSettings']
    assert services.render.call_args.kwargs['video_filters']['contrast'] == 1.2
    # Legacy explicit preset must override persisted custom caption defaults.
    assert client.post('/api/projects/project/render/candidate-project-0', json={
        'captionStyle': 'neon',
    }).status_code == 202
    jobs.run_all()
    assert services.render.call_args.kwargs['caption_settings']['preset'] == 'neon'
    assert services.render.call_args.kwargs['caption_settings']['fontFamily'] == 'default'


@pytest.mark.parametrize('body', [
    [], {'captionSettings': None}, {'videoFilters': None}, {'unknown': 1},
    {'captionSettings': {'fontFamily': 'fontfile=/etc/passwd'}},
    {'captionSettings': {'verticalPosition': 86}},
    {'captionSettings': {'verticalPosition': '40'}},
    {'captionSettings': {'enabled': 1}}, {'captionSettings': {'uppercase': 'true'}},
    {'captionSettings': {'fontSize': 60}, 'videoFilters': {'contrast': float('nan')}},
])
def test_settings_validation_is_atomic(app, client, body):
    seed_project(app)
    before = rows(app, 'SELECT * FROM projects')
    response = client.put('/api/projects/project/render-settings', json=body)
    assert response.status_code == 400
    assert isinstance(response.json['error'], str)
    assert rows(app, 'SELECT * FROM projects') == before


def track_tempfiles(monkeypatch, module):
    original = tempfile.TemporaryFile
    opened = []

    def temporary(*args, **kwargs):
        file = original(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(module.tempfile, 'TemporaryFile', temporary)
    return opened


def test_preview_uses_saved_settings_and_cleans_up_on_close_and_failure(app, client, jobs, monkeypatch):
    seed_project(app)
    saved = client.put('/api/projects/project/render-settings', json={
        'captionSettings': {'enabled': False}, 'videoFilters': {'saturation': 0.5},
    }).json
    opened = track_tempfiles(monkeypatch, render_settings)
    before = rows(app, 'SELECT * FROM projects')

    def preview(source, output, time_sec, **kwargs):
        assert kwargs['caption_settings'] == saved['captionSettings']
        assert kwargs['video_filters'] == saved['videoFilters']
        assert kwargs['caption_words'][0]['text'] == 'Hello'
        assert time_sec == 2
        output.write(b'jpeg-fixture')

    monkeypatch.setattr(media_service, 'render_preview_frame', preview)
    response = client.post('/api/projects/project/preview-frame', json={'time': 2})
    assert response.status_code == 200 and response.mimetype == 'image/jpeg'
    assert response.data == b'jpeg-fixture'
    assert not opened[0].closed
    response.close()
    assert opened[0].closed
    monkeypatch.setattr(media_service, 'render_preview_frame', Mock(side_effect=RuntimeError('encode failed')))
    response = client.post('/api/projects/project/preview-frame', json={'time': 2})
    assert response.status_code == 502 and opened[-1].closed
    assert rows(app, 'SELECT * FROM clips') == []
    assert rows(app, 'SELECT * FROM projects') == before
    assert jobs.tasks == []


@pytest.mark.parametrize('body', [
    {'time': -1}, {'time': 60}, {'time': True}, {'time': '1'}, {'time': float('inf')},
    {'captionSettings': {'fontFamily': 'invalid'}}, {'videoFilters': {'blur': 11}},
    {'sourcePath': '/etc/passwd'},
])
def test_invalid_preview_never_runs_ffmpeg(app, client, monkeypatch, body):
    seed_project(app)
    preview = Mock()
    monkeypatch.setattr(media_service, 'render_preview_frame', preview)
    response = client.post('/api/projects/project/preview-frame', json=body)
    assert response.status_code == 400
    preview.assert_not_called()


def test_preview_ffmpeg_timeout_is_bounded_and_releases_slot(app, client, monkeypatch):
    seed_project(app)
    opened = track_tempfiles(monkeypatch, render_settings)
    monkeypatch.setattr(media_service, 'probe_media', Mock(return_value={
        'has_video': True, 'has_audio': False, 'duration_sec': 60,
    }))
    run = Mock(side_effect=subprocess.TimeoutExpired('ffmpeg', 30))
    monkeypatch.setattr(media_service.subprocess, 'run', run)
    for _ in range(3):
        assert client.post('/api/projects/project/preview-frame', json={}).status_code == 502
    assert run.call_count == 3
    assert run.call_args.kwargs['timeout'] == 30
    assert all(file.closed for file in opened)


@pytest.mark.parametrize('endpoint,slots', [
    ('preview-frame', '_PREVIEW_SLOTS'), ('filter-suggestions/generate', '_SUGGESTION_SLOTS'),
])
def test_busy_preview_or_generation_does_not_queue_more_work(app, client, monkeypatch, endpoint, slots):
    seed_project(app)
    gate = Mock()
    gate.acquire.return_value = False
    monkeypatch.setattr(render_settings, slots, gate)
    response = client.post(f'/api/projects/project/{endpoint}', json={})
    assert response.status_code == 429
    gate.acquire.assert_called_once_with(blocking=False)
    gate.release.assert_not_called()


@pytest.mark.parametrize('audio_only', [False, True])
def test_real_preview_jpeg_applies_filters_captions_fonts_and_silence(app, client, jobs, tmp_path, audio_only):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and FFprobe required')
    seed_project(app)
    source = tmp_path / ('preview.wav' if audio_only else 'preview.mp4')
    input_filter = 'sine=frequency=440:duration=3' if audio_only else 'color=c=navy:s=640x360:r=10:d=3'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', input_filter, '-y', str(source)],
                   check=True, capture_output=True, timeout=30)
    execute(app, 'UPDATE projects SET source_path=?, source_duration=3', (str(source),))
    # The stored transcript has Hello at 0..1 and silence afterwards.
    def preview(time_sec, **settings):
        response = client.post('/api/projects/project/preview-frame', json={'time': time_sec, **settings})
        assert response.status_code == 200, response.json
        assert response.mimetype == 'image/jpeg' and response.data.startswith(b'\xff\xd8')
        data = response.data
        response.close()
        return data

    baseline = preview(0.4, captionSettings={'enabled': False})
    captioned = preview(0.4, captionSettings={'fontFamily': 'serif', 'fontSize': 100,
                                            'verticalPosition': 45, 'uppercase': True,
                                            'backgroundEnabled': True, 'backgroundColor': 'blue',
                                            'backgroundOpacity': 0.5})
    assert captioned != baseline
    assert preview(2.5) == preview(2.5, captionSettings={'enabled': False})
    assert preview(0.4, captionSettings={'enabled': False}, videoFilters={
        'brightness': 0.2, 'contrast': 1.1, 'saturation': 0.8, 'blur': 0.5, 'sharpen': 0.4,
    }) != baseline
    frame = tmp_path / 'preview.jpg'
    frame.write_bytes(captioned)
    metadata = media_service.probe_media(str(frame))
    assert (metadata['width'], metadata['height']) == (360, 640)
    assert rows(app, 'SELECT * FROM clips') == [] and jobs.tasks == []


def provider_response(payload, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


def suggestion_response(result):
    return provider_response({'choices': [{'message': {'content': json.dumps(result)}}]})


SUGGESTION = {
    'videoFilters': {'saturation': 0.8},
    'captionSettings': {'fontFamily': 'serif', 'verticalPosition': 55, 'uppercase': True},
    'rationale': 'A restrained style suits the reflective transcript topic.',
}


def test_generate_uses_active_profile_bounded_transcript_and_persists_without_applying(
        app, client, jobs, monkeypatch):
    seed_project(app)
    execute(app, 'UPDATE transcripts SET raw_json=?', (json.dumps({
        'segments': [{'text': 'context ' * 3000 + 'TAIL MUST NOT BE SENT'}],
    }),))
    profile = client.post('/api/provider-profiles', json={
        'name': 'Text recommendations', 'kind': 'llm', 'provider': 'custom',
        'baseUrl': 'https://example.test/v1', 'model': 'editor-model', 'apiKey': 'secret-profile-key',
    }).json
    assert client.put('/api/provider-profiles/active', json={'kind': 'llm', 'profileId': profile['id']}).status_code == 200
    post = Mock(return_value=suggestion_response(SUGGESTION))
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    before = rows(app, 'SELECT * FROM projects')
    response = client.post('/api/projects/project/filter-suggestions/generate', json={'brief': 'Quiet educational style'})
    assert response.status_code == 201, response.json
    result = response.json
    assert result['settings']['saturation'] == 0.8
    assert result['captionSettings']['fontFamily'] == 'serif'
    assert result['basis'] == 'transcript' and result['source'] == 'ai'
    assert result['applied'] is False and result['model'] == 'editor-model'
    assert 'No video frames were analyzed' in result['contextNotice']
    assert 'secret-profile-key' not in response.text
    request = post.call_args
    assert request.args[0] == 'https://example.test/v1/chat/completions'
    assert request.kwargs['headers']['Authorization'] == 'Bearer secret-profile-key'
    assert request.kwargs['json']['model'] == 'editor-model'
    assert request.kwargs['timeout'] == (5, 30)
    assert request.kwargs['json']['max_tokens'] == 1600
    prompt = request.kwargs['json']['messages'][0]['content']
    assert 'Quiet educational style' in prompt and '"durationSeconds": 60.0' in prompt
    assert 'TAIL MUST NOT BE SENT' not in prompt
    context = json.loads(prompt.split('\nContext: ')[1])
    assert len(context['transcript']) == suggestion_service.TRANSCRIPT_LIMIT
    assert post.call_count == 1
    assert rows(app, 'SELECT * FROM projects') == before
    assert rows(app, 'SELECT * FROM clips') == [] and jobs.tasks == []
    assert create_app().test_client().get('/api/projects/project/filter-suggestions').json == [result]


def test_generate_falls_back_to_env_ollama_model(app, client, monkeypatch):
    seed_project(app)
    monkeypatch.setenv('OLLAMA_MODEL', 'qwen2.5:7b')
    post = Mock(return_value=suggestion_response(SUGGESTION))
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    response = client.post('/api/projects/project/filter-suggestions/generate', json={})
    assert response.status_code == 201 and response.json['model'] == 'qwen2.5:7b'
    assert post.call_args.kwargs['json']['model'] == 'qwen2.5:7b'


def test_generation_does_not_resurrect_a_project_deleted_during_provider_call(app, client, monkeypatch):
    seed_project(app)

    def post(*args, **kwargs):
        assert client.delete('/api/projects/project').status_code == 204
        return suggestion_response(SUGGESTION)

    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    assert client.post('/api/projects/project/filter-suggestions/generate', json={}).status_code == 404
    assert rows(app, 'SELECT * FROM filter_suggestions') == []


def test_generate_supports_claude_profile_and_optional_caption(app, client, monkeypatch):
    seed_project(app)
    profile = client.post('/api/provider-profiles', json={
        'name': 'Claude', 'kind': 'llm', 'provider': 'claude', 'apiKey': 'claude-secret', 'model': 'claude-model',
    }).json
    client.put('/api/provider-profiles/active', json={'kind': 'llm', 'profileId': profile['id']})
    post = Mock(return_value=provider_response({'content': [{'type': 'text', 'text': json.dumps({
        'videoFilters': {}, 'rationale': 'Neutral defaults based on the conversation.',
    })}]}))
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    response = client.post('/api/projects/project/filter-suggestions/generate', json={})
    assert response.status_code == 201 and response.json['captionSettings'] is None
    assert post.call_args.kwargs['headers']['x-api-key'] == 'claude-secret'
    assert post.call_args.kwargs['timeout'] == (5, 30)


@pytest.mark.parametrize('result', [
    [], {}, {**SUGGESTION, 'videoFilters': {'brightness': 2}},
    {**SUGGESTION, 'videoFilters': {'blur': float('nan')}},
    {**SUGGESTION, 'captionSettings': {'fontFamily': 'unsafe'}},
    {**SUGGESTION, 'captionSettings': None}, {**SUGGESTION, 'source': 'vision'},
    {**SUGGESTION, 'rationale': ''}, {**SUGGESTION, 'rationale': 'x' * 1001},
])
def test_generated_invalid_settings_never_persist(app, client, monkeypatch, result):
    seed_project(app)
    monkeypatch.setattr('backend.services.llm_service.requests.post', Mock(return_value=suggestion_response(result)))
    response = client.post('/api/projects/project/filter-suggestions/generate', json={})
    assert response.status_code == 502 and isinstance(response.json['error'], str)
    assert client.get('/api/projects/project/filter-suggestions').json == []


@pytest.mark.parametrize('failure', ['html', 'timeout', 'json'])
def test_generation_provider_failures_are_bounded_useful_and_retryable(app, client, monkeypatch, failure):
    seed_project(app)
    response = provider_response({})
    if failure == 'html':
        response.status_code = 502
        response._content = b'<html><h1>502 Bad Gateway</h1><script>do not show</script></html>'
    elif failure == 'json':
        response = provider_response({'choices': [{'message': {'content': 'not json'}}]})
    post = Mock(return_value=response)
    if failure == 'timeout':
        post.side_effect = requests.Timeout('upstream timed out')
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    result = client.post('/api/projects/project/filter-suggestions/generate', json={})
    assert result.status_code == 502 and len(result.json['error']) < 300
    assert '<html>' not in result.json['error'] and 'do not show' not in result.json['error']
    assert post.call_count == 1
    assert client.get('/api/projects/project/filter-suggestions').json == []
    post.side_effect = None
    post.return_value = suggestion_response(SUGGESTION)
    assert client.post('/api/projects/project/filter-suggestions/generate', json={}).status_code == 201


@pytest.mark.parametrize('body', [[], {'brief': None}, {'brief': 12}, {'brief': 'a' * 2001}, {'source': 'vision'}])
def test_generation_invalid_request_never_contacts_provider(app, client, monkeypatch, body):
    seed_project(app)
    post = Mock()
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    assert client.post('/api/projects/project/filter-suggestions/generate', json=body).status_code == 400
    post.assert_not_called()


def test_generation_requires_transcript_and_configured_provider(app, client, monkeypatch):
    seed_project(app)
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    post = Mock()
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    assert client.post('/api/projects/project/filter-suggestions/generate', json={}).status_code == 400
    execute(app, 'DELETE FROM transcripts')
    assert client.post('/api/projects/project/filter-suggestions/generate', json={}).status_code == 400
    post.assert_not_called()
    assert client.post('/api/projects/project/filter-suggestions', json={'source': 'vision'}).status_code == 400


def add_clip(app, clip_id, candidate='candidate-project-0', state='done', content=b'clip', path=None):
    path = path or Path(app.config['DATA_DIR']) / 'projects' / 'project' / 'clips' / f'{clip_id}.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is not None:
        path.write_bytes(content)
    execute(app, 'INSERT INTO clips (id, candidate_id, status, output_path) VALUES (?,?,?,?)',
            (clip_id, candidate, state, str(path)))
    return path


def test_export_zip_latest_completed_selected_only_and_cleans_up(app, client, jobs, monkeypatch):
    seed_project(app, count=4)
    execute(app, 'UPDATE projects SET name=? WHERE id=?', ('Launch: Q&A / 2026', 'project'))
    execute(app, 'UPDATE candidates SET selected=0 WHERE id=?', ('candidate-project-2',))
    add_clip(app, 'old', content=b'old')
    add_clip(app, 'new', content=b'new')
    add_clip(app, 'active', state='rendering', content=b'partial')
    add_clip(app, 'second', candidate='candidate-project-1', content=b'second')
    add_clip(app, 'unselected', candidate='candidate-project-2')
    add_clip(app, 'unready', candidate='candidate-project-3', state='pending')
    before = rows(app, 'SELECT * FROM clips')
    opened = track_tempfiles(monkeypatch, rendering)
    response = client.get('/api/projects/project/export')
    assert response.status_code == 200 and response.mimetype == 'application/zip'
    assert response.headers['Content-Disposition'] == (
        'attachment; filename=clipforge-Launch_QA_2026-clips.zip'
    )
    assert response.headers['X-Export-Clip-Count'] == '2'
    assert response.headers['X-Export-Skipped-Count'] == '1'
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.namelist() == [
            'clipforge-clip-001-new.mp4', 'clipforge-clip-002-second.mp4',
        ]
        assert archive.read('clipforge-clip-001-new.mp4') == b'new'
        assert archive.read('clipforge-clip-002-second.mp4') == b'second'
    assert len(opened) == 1 and not opened[0].closed
    response.close()
    assert opened[0].closed
    assert rows(app, 'SELECT * FROM clips') == before and jobs.tasks == []
    # WSGI closing an interrupted download must release the disk temporary file too.
    interrupted = client.get('/api/projects/project/export', buffered=False)
    interrupted.close()
    assert opened[-1].closed


def test_export_exact_ids_accept_unselected_and_reject_foreign_unready_and_missing(app, client, jobs):
    seed_project(app, count=2)
    seed_project(app, 'other')
    execute(app, 'UPDATE candidates SET selected=0')
    add_clip(app, 'exact', content=b'exact')
    add_clip(app, 'pending', candidate='candidate-project-1', state='pending')
    add_clip(app, 'foreign', candidate='candidate-other-0')
    response = client.get('/api/projects/project/export?clipIds=exact')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.namelist() == ['clipforge-clip-001-exact.mp4']
        assert archive.read('clipforge-clip-001-exact.mp4') == b'exact'
    response.close()
    repeated = client.get('/api/projects/project/export?clipIds=exact&clipIds=exact')
    assert repeated.status_code == 200 and repeated.headers['X-Export-Clip-Count'] == '1'
    repeated.close()
    assert client.get('/api/projects/project/export?clipIds=exact,foreign').status_code == 404
    assert client.get('/api/projects/project/export?clipIds=exact,pending').status_code == 409
    assert client.get('/api/projects/project/export?clipIds=missing').status_code == 404
    assert client.get('/api/projects/project/export?clipIds=').status_code == 400
    assert client.get('/api/projects/project/export').status_code == 409
    assert jobs.tasks == []


def test_clip_file_is_inline_and_has_identifiable_clipforge_download_name(app, client):
    seed_project(app)
    add_clip(app, 'preview-artifact')

    response = client.get('/api/clips/preview-artifact/file')

    assert response.status_code == 200
    assert response.mimetype == 'video/mp4'
    assert response.headers['Content-Disposition'] == (
        'inline; filename=clipforge-clip-001-preview-artifact.mp4'
    )
    assert response.data == b'clip'
    response.close()


@pytest.mark.parametrize('kind', ['outside', 'symlink', 'empty', 'missing'])
def test_export_rejects_unavailable_or_out_of_project_artifacts(app, client, tmp_path, kind):
    seed_project(app)
    external = tmp_path / 'private.mp4'
    if kind == 'outside':
        add_clip(app, 'bad', path=external)
    elif kind == 'symlink':
        external.write_bytes(b'private')
        path = add_clip(app, 'bad', content=None)
        path.symlink_to(external)
    else:
        add_clip(app, 'bad', content=b'' if kind == 'empty' else None)
    assert client.get('/api/projects/project/export').status_code == 409
    assert client.get('/api/projects/project/export?clipIds=bad').status_code == 409


def test_export_rejects_clip_directory_redirect_to_another_project(app, client):
    seed_project(app)
    other_directory = Path(app.config['DATA_DIR']) / 'projects' / 'other' / 'clips'
    other_directory.mkdir(parents=True)
    (other_directory / 'bad.mp4').write_bytes(b'foreign')
    directory = Path(app.config['DATA_DIR']) / 'projects' / 'project' / 'clips'
    directory.symlink_to(other_directory, target_is_directory=True)
    execute(app, 'INSERT INTO clips (id,candidate_id,status,output_path) VALUES (?,?,?,?)',
            ('bad', 'candidate-project-0', 'done', str(directory / 'bad.mp4')))
    assert client.get('/api/projects/project/export?clipIds=bad').status_code == 409


def test_export_does_not_silently_substitute_old_file_when_latest_completed_is_missing(app, client):
    seed_project(app)
    add_clip(app, 'old')
    add_clip(app, 'latest', content=None)
    assert client.get('/api/projects/project/export').status_code == 409
    response = client.get('/api/projects/project/export?clipIds=old')
    assert response.status_code == 200
    response.close()


def test_export_write_failure_closes_temporary_file(app, client, monkeypatch):
    seed_project(app)
    add_clip(app, 'clip')
    opened = track_tempfiles(monkeypatch, rendering)
    monkeypatch.setattr(zipfile.ZipFile, 'write', Mock(side_effect=OSError('disk full')))
    assert client.get('/api/projects/project/export').status_code == 409
    assert opened[0].closed


def test_migration_adds_settings_and_suggestion_metadata_without_losing_rows(app):
    seed_project(app)
    # Recreate the previous release's schema by dropping only new columns.
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('ALTER TABLE projects DROP COLUMN video_filters_json')
        db.execute('ALTER TABLE filter_suggestions DROP COLUMN caption_settings_json')
        db.execute('ALTER TABLE filter_suggestions DROP COLUMN basis')
        db.execute('''INSERT INTO filter_suggestions
                      (id,project_id,settings_json,source,rationale,created_at)
                      VALUES ('legacy','project','{}','ai','Saved metadata','now')''')
    reopened = create_app().test_client()
    assert reopened.get('/api/projects/project').json['project']['videoFilters']['contrast'] == 1
    suggestion = reopened.get('/api/projects/project/filter-suggestions').json[0]
    assert suggestion['id'] == 'legacy'
    assert suggestion['basis'] == 'user-provided' and suggestion['captionSettings'] is None
    assert create_app().test_client().get('/api/projects/project').status_code == 200


@pytest.mark.parametrize('method,path', [
    ('put', 'render-settings'), ('post', 'preview-frame'),
    ('post', 'filter-suggestions/generate'), ('get', 'export'),
])
def test_missing_project_new_endpoints_are_json_404(client, method, path):
    response = getattr(client, method)(f'/api/projects/missing/{path}', json={})
    assert response.status_code == 404 and isinstance(response.json['error'], str)
