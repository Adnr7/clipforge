"""Pipeline regressions: use real SQLite and controlled service/worker boundaries."""

import json
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import create_app
from backend.config import Config
from backend.database import get_db
from backend.routes import analysis, projects, rendering, transcription, youtube
from backend.services import media_service, transcription_service, youtube_service


TRANSCRIPT = {
    'language': 'en', 'duration': 60, 'speakers': ['Speaker 1'],
    'words': [{'text': 'Hello', 'start': 0, 'end': 1}],
    'segments': [{'text': 'Hello world', 'start': 0, 'end': 30}],
}
DRAFTS = [
    {'start': 0, 'end': 20, 'score': 70, 'hook': 'First', 'rationale': 'One'},
    {'start': 25, 'end': 50, 'score': 90, 'hook': 'Second', 'rationale': 'Two'},
]
METADATA = {'has_video': True, 'has_audio': True, 'duration_sec': 60}
URL = 'https://www.youtube.com/watch?v=abcdefghijk'


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'test-key')
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def seed_project(app, project_id='project', count=1, status='transcribed'):
    with app.app_context():
        directory = Path(Config.get_data_dir()) / 'projects' / project_id
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / 'source.mp4'
        source.write_bytes(b'source')
        (directory / 'audio.wav').write_bytes(b'audio')
        db = get_db()
        db.execute(
            'INSERT INTO projects (id, source_path, source_duration, status, caption_style, '
            'created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
            (project_id, str(source), 60, status, 'classic', '2026-01-01', '2026-01-01'),
        )
        db.execute(
            'INSERT INTO transcripts (id, project_id, engine, raw_json, created_at) VALUES (?,?,?,?,?)',
            (f'transcript-{project_id}', project_id, 'test', json.dumps(TRANSCRIPT), '2026-01-01'),
        )
        for index in range(count):
            db.execute(
                'INSERT INTO candidates (id, project_id, start_sec, end_sec, score, hook, '
                'rationale, rank, selected) VALUES (?,?,?,?,?,?,?,?,?)',
                (f'candidate-{project_id}-{index}', project_id, index, index + 20, 80,
                 'Old hook', 'Old rationale', index + 1, 1),
            )
        db.commit()
    return project_id


def rows(app, query, parameters=()):
    with app.app_context():
        return [dict(row) for row in get_db().execute(query, parameters).fetchall()]


def execute(app, query, parameters=()):
    with app.app_context():
        db = get_db()
        db.execute(query, parameters)
        db.commit()


def project_jobs(client):
    """The aggregate and legacy polling endpoints must expose identical jobs."""
    detail = client.get('/api/projects/project')
    assert detail.status_code == 200
    jobs = detail.json['jobs']
    assert set(jobs) == {'transcription', 'analysis'}
    for stage, endpoint in [('transcription', 'transcribe'), ('analysis', 'analyze')]:
        response = client.get(f'/api/projects/project/{endpoint}/status')
        assert response.status_code == 200
        assert response.json == jobs[stage]
        assert set(response.json) == {'status', 'error'}
    return jobs


class DeferredJobs:
    """Hold work at dispatch, exercising the race before a worker has started."""

    def __init__(self):
        self.tasks = []

    def submit(self, function, *args, **kwargs):
        future = Future()
        self.tasks.append((function, args, kwargs, future))
        return future

    def thread(self, target, args=(), kwargs=None, **options):
        return SimpleNamespace(start=lambda: self.submit(target, *args, **(kwargs or {})))

    def run_all(self):
        while self.tasks:
            function, args, kwargs, future = self.tasks.pop(0)
            future.set_result(function(*args, **kwargs))


@pytest.fixture
def jobs(monkeypatch):
    queue = DeferredJobs()
    for module in (analysis, transcription, rendering, youtube):
        monkeypatch.setattr(module, 'threading', SimpleNamespace(Thread=queue.thread), raising=False)
    monkeypatch.setattr(rendering, '_RENDER_EXECUTOR', queue, raising=False)
    monkeypatch.setattr(youtube, '_DOWNLOAD_EXECUTOR', queue, raising=False)
    return queue


@pytest.fixture
def services(monkeypatch):
    def render(**kwargs):
        path = Path(kwargs['output_path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'completed clip')
        return str(path)

    def download(url, directory, progress_cb):
        path = Path(directory) / 'video.mp4'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'downloaded video')
        progress_cb('[download] 100.0% of 1MiB')
        return {'ok': True, 'path': str(path), 'title': 'Video', 'duration': 60}

    mocks = SimpleNamespace(
        render=Mock(side_effect=render),
        transcribe=Mock(return_value=TRANSCRIPT),
        analyze=Mock(return_value=DRAFTS),
        download=Mock(side_effect=download),
        probe=Mock(return_value=METADATA),
    )
    monkeypatch.setattr(media_service, 'render_portrait_clip', mocks.render)
    monkeypatch.setattr(media_service, 'probe_media', mocks.probe)
    monkeypatch.setattr(transcription, 'transcribe_deepgram', mocks.transcribe)
    monkeypatch.setattr(transcription_service, 'transcribe_whisper', mocks.transcribe)
    monkeypatch.setattr(analysis, 'analyze_transcript', mocks.analyze)
    monkeypatch.setattr(youtube_service, 'download_youtube_video', mocks.download)
    return mocks


@pytest.mark.parametrize('endpoint,state', [('transcribe', 'transcribing'), ('analyze', 'analyzing')])
def test_optional_json_claims_job_before_dispatch(app, client, jobs, services, endpoint, state):
    seed_project(app)
    response = client.post(f'/api/projects/project/{endpoint}')
    assert response.status_code == 202
    assert rows(app, 'SELECT status FROM projects')[0]['status'] == state
    assert client.get(f'/api/projects/project/{endpoint}/status').json['status'] == 'processing'
    assert client.post(f'/api/projects/project/{endpoint}').status_code == 409
    assert len(jobs.tasks) == 1
    jobs.run_all()
    assert rows(app, 'SELECT status FROM projects')[0]['status'] == (
        'transcribed' if endpoint == 'transcribe' else 'analyzed'
    )
    assert client.get(f'/api/projects/project/{endpoint}/status').json['status'] == 'done'


@pytest.mark.parametrize('endpoints', [
    ('transcribe', 'transcribe'), ('analyze', 'analyze'), ('transcribe', 'analyze'),
])
def test_project_claim_is_atomic_across_requests(app, jobs, endpoints):
    seed_project(app)
    ready = threading.Barrier(2)

    def start(endpoint):
        with app.test_client() as client:
            ready.wait(timeout=5)
            return client.post(f'/api/projects/project/{endpoint}', json={}).status_code

    with ThreadPoolExecutor(max_workers=2) as requests:
        assert sorted(requests.map(start, endpoints)) == [202, 409]
    assert len(jobs.tasks) == 1


@pytest.mark.parametrize('endpoint', ['transcribe', 'analyze'])
@pytest.mark.parametrize('clip_state', ['pending', 'rendering'])
def test_processing_cannot_replace_inputs_of_active_renders(app, client, jobs, endpoint, clip_state):
    seed_project(app)
    execute(app, 'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
            ('active', 'candidate-project-0', clip_state))
    response = client.post(f'/api/projects/project/{endpoint}', json={})
    assert response.status_code == 409
    assert isinstance(response.json['error'], str)
    assert jobs.tasks == []
    assert rows(app, 'SELECT status FROM projects')[0]['status'] == 'transcribed'


@pytest.mark.parametrize('state', ['transcribing', 'analyzing'])
@pytest.mark.parametrize('endpoint', ['render/candidate-project-0', 'render-batch'])
def test_render_cannot_overlap_processing(app, client, jobs, state, endpoint):
    seed_project(app, status=state)
    assert client.post(f'/api/projects/project/{endpoint}', json={}).status_code == 409
    assert rows(app, 'SELECT * FROM clips') == []
    assert jobs.tasks == []


@pytest.mark.parametrize('method,endpoint', [
    ('post', 'transcribe'), ('get', 'transcribe/status'),
    ('post', 'analyze'), ('get', 'analyze/status'),
    ('post', 'render-batch'), ('post', 'render/no-candidate'), ('patch', 'candidates'),
])
def test_missing_projects_return_json_404(client, method, endpoint):
    response = getattr(client, method)(f'/api/projects/missing/{endpoint}', json={})
    assert response.status_code == 404
    assert isinstance(response.json['error'], str)


@pytest.mark.parametrize('endpoint,body', [
    ('transcribe', []), ('transcribe', {'mode': []}), ('transcribe', {'mode': 'invalid'}),
    ('analyze', 'string'), ('analyze', {'provider': []}),
    ('render-batch', []), ('render-batch', {'captionStyle': {}}),
    ('render/candidate-project-0', {'captionStyle': 'invalid'}),
])
def test_malformed_pipeline_json_does_not_dispatch(app, client, jobs, endpoint, body):
    seed_project(app)
    response = client.post(f'/api/projects/project/{endpoint}', json=body)
    assert response.status_code == 400
    assert isinstance(response.json['error'], str)
    assert jobs.tasks == []


@pytest.mark.parametrize('selection', ['explicit', 'active'])
@pytest.mark.parametrize('provider,mode', [('deepgram', 'whisper'), ('deepgram', 'local'), ('whisper', 'cloud')])
def test_transcription_profile_cannot_override_explicit_mismatching_mode(
        app, client, jobs, selection, provider, mode):
    seed_project(app)
    profile = client.post('/api/provider-profiles', json={
        'name': 'Transcriber', 'kind': 'transcription', 'provider': provider,
        'apiKey': 'profile-key', 'model': 'base' if provider == 'whisper' else 'nova-2',
    }).json
    body = {'mode': mode}
    if selection == 'explicit':
        body['profileId'] = profile['id']
    else:
        assert client.put('/api/provider-profiles/active', json={
            'kind': 'transcription', 'profileId': profile['id'],
        }).status_code == 200
    response = client.post('/api/projects/project/transcribe', json=body)
    assert response.status_code == 400
    assert response.json['error'] == 'Transcription profile provider does not match mode'
    assert jobs.tasks == []
    assert rows(app, 'SELECT status, last_job_stage FROM projects')[0] == {
        'status': 'transcribed', 'last_job_stage': None,
    }


@pytest.mark.parametrize('provider,mode', [
    ('deepgram', None), ('deepgram', 'cloud'),
    ('whisper', None), ('whisper', 'whisper'), ('whisper', 'local'),
])
def test_transcription_profile_uses_credentials_and_known_model(app, client, jobs, services, provider, mode):
    seed_project(app)
    profile = client.post('/api/provider-profiles', json={
        'name': 'Transcriber', 'kind': 'transcription', 'provider': provider,
        'apiKey': 'profile-key', 'model': 'small.en' if provider == 'whisper' else 'nova-2',
    }).json
    body = {'profileId': profile['id']}
    if mode is not None:
        body['mode'] = mode
    assert client.post('/api/projects/project/transcribe', json=body).status_code == 202
    jobs.run_all()
    assert project_jobs(client)['transcription'] == {'status': 'done', 'error': None}
    assert services.transcribe.call_args.args[1] == ('small.en' if provider == 'whisper' else 'profile-key')


def test_legacy_whisper_profile_with_invalid_model_does_not_dispatch(app, client, jobs):
    seed_project(app)
    profile = client.post('/api/provider-profiles', json={
        'name': 'Local', 'kind': 'transcription', 'provider': 'whisper', 'model': 'base',
    }).json
    execute(app, 'UPDATE provider_profiles SET model=? WHERE id=?', ('unknown', profile['id']))
    response = client.post('/api/projects/project/transcribe', json={'profileId': profile['id']})
    assert response.status_code == 400
    assert response.json['error'] == 'Unknown Whisper model'
    assert jobs.tasks == []


@pytest.mark.parametrize('endpoint', ['transcribe', 'analyze', 'render-batch'])
def test_broken_json_is_a_string_error(app, client, jobs, endpoint):
    seed_project(app)
    response = client.post(f'/api/projects/project/{endpoint}', data='{', content_type='application/json')
    assert response.status_code == 400
    assert isinstance(response.json['error'], str)
    assert jobs.tasks == []


@pytest.mark.parametrize('endpoint', ['transcribe', 'analyze'])
def test_worker_errors_persist_and_retry(app, client, jobs, services, endpoint):
    seed_project(app)
    stage, other_stage = ('transcription', 'analysis') if endpoint == 'transcribe' else ('analysis', 'transcription')
    service = services.transcribe if endpoint == 'transcribe' else services.analyze
    service.side_effect = RuntimeError('Provider unavailable')
    assert client.post(f'/api/projects/project/{endpoint}', json={}).status_code == 202
    jobs.run_all()
    assert rows(app, 'SELECT status FROM projects')[0]['status'] == 'error'
    status = client.get(f'/api/projects/project/{endpoint}/status').json
    assert status['status'] == 'error'
    assert 'Provider unavailable' in status['error']
    assert project_jobs(client) == {
        stage: {'status': 'error', 'error': 'Provider unavailable'},
        other_stage: {'status': 'done', 'error': None},
    }
    # A fresh app has no access to the original app's in-memory extensions.
    client = create_app().test_client()
    assert project_jobs(client)[stage] == status
    assert project_jobs(client)[other_stage] == {'status': 'done', 'error': None}
    assert rows(app, 'SELECT id FROM candidates') == [{'id': 'candidate-project-0'}]
    service.side_effect = None
    assert client.post(f'/api/projects/project/{endpoint}', json={}).status_code == 202
    assert project_jobs(client)[stage] == {'status': 'processing', 'error': None}
    assert rows(app, f'SELECT {stage}_error FROM projects')[0][f'{stage}_error'] is None
    jobs.run_all()
    assert project_jobs(client)[stage] == {'status': 'done', 'error': None}
    assert project_jobs(create_app().test_client()) == {
        stage: {'status': 'done', 'error': None}, other_stage: {'status': 'done', 'error': None},
    }


def test_custom_llm_gateway_error_only_belongs_to_analysis(app, client, jobs, services):
    seed_project(app, count=0)
    profile = client.post('/api/provider-profiles', json={
        'name': 'Custom LLM', 'kind': 'llm', 'provider': 'custom',
        'baseUrl': 'https://llm.example/v1', 'model': 'custom-model',
    }).json
    services.analyze.side_effect = RuntimeError('<html><h1>Custom LLM: HTTP 502 Bad Gateway</h1></html>')
    assert client.post('/api/projects/project/analyze', json={'profileId': profile['id']}).status_code == 202
    jobs.run_all()
    expected = {
        'transcription': {'status': 'done', 'error': None},
        'analysis': {'status': 'error', 'error': 'Custom LLM: HTTP 502 Bad Gateway'},
    }
    assert project_jobs(client) == expected
    assert project_jobs(create_app().test_client()) == expected


@pytest.mark.parametrize('failed_stage,endpoint', [('analysis', 'transcribe'), ('transcription', 'analyze')])
def test_rerun_clears_only_its_own_error(app, client, jobs, services, failed_stage, endpoint):
    seed_project(app)
    services.transcribe.side_effect = RuntimeError('Transcription provider failed')
    services.analyze.side_effect = RuntimeError('Analysis provider failed')
    for route in ('transcribe', 'analyze'):
        assert client.post(f'/api/projects/project/{route}').status_code == 202
        jobs.run_all()
    before = project_jobs(client)
    assert all(job['status'] == 'error' for job in before.values())
    services.transcribe.side_effect = None
    services.analyze.side_effect = None
    rerun_stage = 'transcription' if endpoint == 'transcribe' else 'analysis'
    assert client.post(f'/api/projects/project/{endpoint}').status_code == 202
    assert project_jobs(client) == {
        failed_stage: before[failed_stage], rerun_stage: {'status': 'processing', 'error': None},
    }
    jobs.run_all()
    expected = {failed_stage: before[failed_stage], rerun_stage: {'status': 'done', 'error': None}}
    assert project_jobs(client) == expected
    assert project_jobs(create_app().test_client()) == expected


@pytest.mark.parametrize('endpoint', ['transcribe', 'analyze'])
def test_rejected_rerun_preserves_saved_error(app, client, jobs, services, endpoint):
    seed_project(app)
    service = services.transcribe if endpoint == 'transcribe' else services.analyze
    service.side_effect = RuntimeError('Provider unavailable')
    assert client.post(f'/api/projects/project/{endpoint}').status_code == 202
    jobs.run_all()
    before = project_jobs(client)
    assert client.post(f'/api/projects/project/{endpoint}', json=[]).status_code == 400
    assert project_jobs(client) == before
    assert jobs.tasks == []


def test_failed_audio_extraction_does_not_poison_retry(app, client, jobs, services, monkeypatch):
    seed_project(app)
    with app.app_context():
        audio = Path(Config.get_data_dir()) / 'projects' / 'project' / 'audio.wav'
    audio.unlink()

    def partial_extraction(source, output):
        Path(output).write_bytes(b'partial WAV')
        raise RuntimeError('FFmpeg extraction failed')

    extract = Mock(side_effect=partial_extraction)
    monkeypatch.setattr(media_service, 'extract_audio', extract)
    assert client.post('/api/projects/project/transcribe', json={}).status_code == 202
    assert extract.call_count == 0  # Extraction is background work, after the claim.
    jobs.run_all()
    assert client.get('/api/projects/project/transcribe/status').json['status'] == 'error'
    assert not audio.exists()
    services.transcribe.assert_not_called()
    extract.side_effect = lambda source, output: Path(output).write_bytes(b'complete WAV')
    assert client.post('/api/projects/project/transcribe', json={}).status_code == 202
    jobs.run_all()
    assert extract.call_count == 2
    assert client.get('/api/projects/project/transcribe/status').json['status'] == 'done'


@pytest.mark.parametrize('endpoint,terminal', [('transcribe', 'transcribed'), ('analyze', 'analyzed')])
def test_database_failure_rolls_back_results_before_persisting_error(
        app, client, jobs, services, endpoint, terminal):
    seed_project(app)
    execute(app, 'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
            ('old-clip', 'candidate-project-0', 'done'))
    execute(app, f'''CREATE TRIGGER fail_completion BEFORE UPDATE ON projects
                    WHEN NEW.status = '{terminal}' BEGIN SELECT RAISE(FAIL, 'write failed'); END''')
    assert client.post(f'/api/projects/project/{endpoint}', json={}).status_code == 202
    jobs.run_all()
    assert rows(app, 'SELECT status FROM projects')[0]['status'] == 'error'
    assert rows(app, 'SELECT id FROM transcripts') == [{'id': 'transcript-project'}]
    assert rows(app, 'SELECT id FROM candidates') == [{'id': 'candidate-project-0'}]
    assert rows(app, 'SELECT id FROM clips') == [{'id': 'old-clip'}]


@pytest.mark.parametrize('endpoint', ['transcribe', 'analyze'])
def test_dispatch_failure_is_terminal(app, client, jobs, monkeypatch, endpoint):
    seed_project(app)
    monkeypatch.setattr(jobs, 'submit', Mock(side_effect=RuntimeError('Cannot start worker')))
    response = client.post(f'/api/projects/project/{endpoint}', json={})
    assert response.status_code == 500
    assert isinstance(response.json['error'], str)
    assert rows(app, 'SELECT status FROM projects')[0]['status'] == 'error'
    assert client.get(f'/api/projects/project/{endpoint}/status').json['status'] == 'error'
    stage = 'transcription' if endpoint == 'transcribe' else 'analysis'
    assert project_jobs(create_app().test_client())[stage] == {
        'status': 'error', 'error': 'Cannot start worker',
    }


@pytest.mark.parametrize('status,has_transcript,has_candidates,expected', [
    ('imported', False, False, ('idle', 'idle')),
    ('transcribed', True, False, ('done', 'idle')),
    ('analyzed', True, True, ('done', 'done')),
    ('error', False, False, ('error', 'idle')),
    ('error', True, False, ('done', 'error')),
    ('error', True, True, ('done', 'done')),
])
def test_legacy_status_attribution_before_and_after_reopen(
        app, client, status, has_transcript, has_candidates, expected):
    seed_project(app, count=int(has_candidates), status=status)
    if not has_transcript:
        execute(app, 'DELETE FROM transcripts')
    before = project_jobs(client)
    assert tuple(before[stage]['status'] for stage in ('transcription', 'analysis')) == expected
    assert all(bool(job['error']) == (job['status'] == 'error') for job in before.values())
    assert project_jobs(create_app().test_client()) == before
    assert project_jobs(create_app().test_client()) == before


def test_legacy_analysis_error_survives_transcription_rerun(app, jobs, services):
    seed_project(app, count=0, status='error')
    client = create_app().test_client()
    legacy_error = project_jobs(client)['analysis']
    assert legacy_error['status'] == 'error'
    assert client.post('/api/projects/project/transcribe').status_code == 202
    assert project_jobs(client)['analysis'] == legacy_error
    jobs.run_all()
    assert project_jobs(client) == {
        'transcription': {'status': 'done', 'error': None}, 'analysis': legacy_error,
    }


@pytest.mark.parametrize('state,last_stage,has_transcript,has_candidates,failed_stage', [
    ('transcribing', None, False, False, 'transcription'),
    ('transcribing', 'analysis', True, True, 'transcription'),
    ('analyzing', None, True, False, 'analysis'),
    ('analyzing', 'transcription', True, True, 'analysis'),
    ('processing', 'transcription', True, True, 'transcription'),
    ('processing', 'analysis', True, True, 'analysis'),
    ('processing', None, False, False, 'transcription'),
    ('processing', None, True, False, 'analysis'),
])
def test_interruption_preserves_stage_even_with_previous_outputs(
        app, client, state, last_stage, has_transcript, has_candidates, failed_stage):
    seed_project(app, count=int(has_candidates), status=state)
    if not has_transcript:
        execute(app, 'DELETE FROM transcripts')
    execute(app, 'UPDATE projects SET last_job_stage=?', (last_stage,))
    before = project_jobs(client)
    assert before[failed_stage] == {'status': 'processing', 'error': None}
    other_stage = 'analysis' if failed_stage == 'transcription' else 'transcription'
    recovered = project_jobs(create_app().test_client())
    assert recovered[failed_stage]['status'] == 'error'
    assert 'interrupted' in recovered[failed_stage]['error']
    assert recovered[other_stage] == before[other_stage]
    saved = rows(app, 'SELECT status, last_job_stage FROM projects')[0]
    assert saved == {'status': 'error', 'last_job_stage': failed_stage}
    assert project_jobs(create_app().test_client()) == recovered


def test_detail_aggregates_clip_states_with_jobs(app, client):
    seed_project(app, count=4, status='analyzed')
    for index, state in enumerate(('pending', 'rendering', 'done', 'error')):
        execute(app, 'INSERT INTO clips (id, candidate_id, status, render_log) VALUES (?,?,?,?)',
                (f'clip-{index}', f'candidate-project-{index}', state,
                 'Render failed' if state == 'error' else None))
    detail = client.get('/api/projects/project').json
    assert detail['jobs'] == {
        'transcription': {'status': 'done', 'error': None},
        'analysis': {'status': 'done', 'error': None},
    }
    assert [clip['status'] for clip in detail['clips']] == ['pending', 'rendering', 'done', 'error']
    assert detail['clips'][-1]['render_log'] == 'Render failed'


def test_detail_reads_jobs_and_outputs_from_one_snapshot(app, client, monkeypatch):
    seed_project(app, count=0, status='transcribing')
    execute(app, 'DELETE FROM transcripts')
    resolve_settings = projects.resolve_project_settings

    def complete_worker_during_detail(project):
        # Commit on another connection after the detail route read the project,
        # but before it reads outputs. The response must remain self-consistent.
        with app.app_context():
            db = get_db()
            db.execute('''INSERT INTO transcripts (id, project_id, engine, raw_json, created_at)
                          VALUES ('completed', 'project', 'test', ?, 'now')''', (json.dumps(TRANSCRIPT),))
            db.execute("UPDATE projects SET status='transcribed', last_job_stage='transcription'")
            db.commit()
        return resolve_settings(project)

    monkeypatch.setattr(projects, 'resolve_project_settings', complete_worker_during_detail)
    detail = client.get('/api/projects/project').json
    assert detail['project']['status'] == 'transcribing'
    assert detail['transcript'] is None
    assert detail['jobs']['transcription'] == {'status': 'processing', 'error': None}
    monkeypatch.setattr(projects, 'resolve_project_settings', resolve_settings)
    completed = client.get('/api/projects/project').json
    assert completed['project']['status'] == 'transcribed'
    assert completed['transcript']['id'] == 'completed'
    assert completed['jobs']['transcription'] == {'status': 'done', 'error': None}


def test_jobs_are_scoped_to_their_app(app, client, jobs, services, tmp_path, monkeypatch):
    seed_project(app)
    assert client.post('/api/projects/project/transcribe', json={}).status_code == 202
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'other-app'))
    other = create_app()
    seed_project(other, status='analyzed')
    assert other.test_client().get('/api/projects/project/transcribe/status').json['status'] == 'done'
    jobs.run_all()
    assert rows(app, 'SELECT COUNT(*) AS count FROM transcripts')[0]['count'] == 2
    assert rows(other, 'SELECT COUNT(*) AS count FROM transcripts')[0]['count'] == 1
    assert rows(other, 'SELECT status FROM projects')[0]['status'] == 'analyzed'


@pytest.mark.parametrize('drafts', [
    [], {}, [None], [{'start': 0, 'end': 20}],
    [{**DRAFTS[0], 'score': float('nan')}],
    [{**DRAFTS[0], 'start': -1}], [{**DRAFTS[0], 'end': 100}],
    [{**DRAFTS[0], 'score': 101}], [{**DRAFTS[0], 'hook': {}}],
])
def test_invalid_analysis_preserves_existing_candidates_and_clips(app, client, jobs, services, drafts):
    seed_project(app)
    execute(app, 'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
            ('old', 'candidate-project-0', 'done'))
    services.analyze.return_value = drafts
    assert client.post('/api/projects/project/analyze', json={}).status_code == 202
    assert rows(app, 'SELECT id FROM candidates') == [{'id': 'candidate-project-0'}]
    jobs.run_all()
    assert client.get('/api/projects/project/analyze/status').json['status'] == 'error'
    assert rows(app, 'SELECT id FROM candidates') == [{'id': 'candidate-project-0'}]
    assert rows(app, 'SELECT id FROM clips') == [{'id': 'old'}]


def test_successful_analysis_replaces_candidates_in_score_order(app, client, jobs, services):
    seed_project(app)
    assert client.post('/api/projects/project/analyze', json={}).status_code == 202
    assert rows(app, 'SELECT hook FROM candidates') == [{'hook': 'Old hook'}]
    jobs.run_all()
    assert rows(app, 'SELECT hook, rank, selected FROM candidates ORDER BY rank') == [
        {'hook': 'Second', 'rank': 1, 'selected': 0}, {'hook': 'First', 'rank': 2, 'selected': 0},
    ]


@pytest.mark.parametrize('body', [
    {}, [], {'selectedIds': None}, {'selectedIds': 'candidate-project-0'},
    {'selectedIds': {}}, {'selectedIds': [1]}, {'selectedIds': ['']},
    {'selectedIds': ['candidate-other-0']}, {'selectedIds': ['missing']},
])
def test_bad_selection_preserves_previous_selection(app, client, body):
    seed_project(app)
    seed_project(app, 'other')
    response = client.patch('/api/projects/project/candidates', json=body)
    assert response.status_code == 400
    assert isinstance(response.json['error'], str)
    assert rows(app, 'SELECT selected FROM candidates WHERE project_id=?', ('project',)) == [{'selected': 1}]


def test_selection_can_be_cleared_or_set_without_touching_other_projects(app, client):
    seed_project(app, count=2)
    seed_project(app, 'other')
    assert client.patch('/api/projects/project/candidates', json={'selectedIds': []}).status_code == 200
    assert client.patch('/api/projects/project/candidates', json={
        'selectedIds': ['candidate-project-1', 'candidate-project-1'],
    }).status_code == 200
    assert rows(app, 'SELECT selected FROM candidates WHERE project_id=? ORDER BY rank', ('project',)) == [
        {'selected': 0}, {'selected': 1},
    ]
    assert rows(app, 'SELECT selected FROM candidates WHERE project_id=?', ('other',)) == [{'selected': 1}]


def test_render_dedupes_single_and_batch_and_uses_unique_paths(app, client, jobs, services):
    seed_project(app, count=2)
    first = client.post('/api/projects/project/render/candidate-project-0')
    assert first.status_code == 202
    batch = client.post('/api/projects/project/render-batch', json={'captionStyle': 'neon'})
    assert batch.status_code == 202
    # Different looks cannot reuse a pending render with the old caption style.
    assert first.json['clipId'] not in batch.json['clipIds']
    assert len(jobs.tasks) == 3
    clips = rows(app, 'SELECT output_path FROM clips')
    assert len({clip['output_path'] for clip in clips}) == 3
    jobs.run_all()
    assert [call.kwargs['caption_style'] for call in services.render.call_args_list] == ['classic', 'neon', 'neon']
    retry = client.post('/api/projects/project/render/candidate-project-0', json={})
    assert retry.status_code == 200
    assert retry.json == {'clipId': first.json['clipId'], 'status': 'done'}
    assert len({clip['output_path'] for clip in rows(app, 'SELECT output_path FROM clips')}) == 3


def test_render_claim_is_atomic_between_single_and_batch(app, jobs):
    seed_project(app)
    ready = threading.Barrier(2)

    def start(endpoint):
        with app.test_client() as client:
            ready.wait(timeout=5)
            response = client.post(f'/api/projects/project/{endpoint}', json={})
            assert response.status_code == 202
            return response.json

    with ThreadPoolExecutor(max_workers=2) as requests:
        single, batch = list(requests.map(start, ['render/candidate-project-0', 'render-batch']))
    assert batch['clipIds'] == [single['clipId']]
    assert len(rows(app, 'SELECT * FROM clips')) == 1
    assert len(jobs.tasks) == 1


@pytest.mark.parametrize('state', ['pending', 'rendering', 'error', 'done'])
def test_clip_file_only_serves_completed_renders(app, client, tmp_path, state):
    seed_project(app)
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'partial or complete')
    execute(app, 'INSERT INTO clips (id, candidate_id, status, output_path) VALUES (?,?,?,?)',
            ('clip', 'candidate-project-0', state, str(path)))
    response = client.get('/api/clips/clip/file')
    assert response.status_code == (200 if state == 'done' else 404)
    response.close()


@pytest.mark.parametrize('failure', ['exception', 'no-file', 'empty-file'])
def test_render_failure_is_terminal_and_never_served(app, client, jobs, services, failure):
    seed_project(app)

    def fail(**kwargs):
        if failure == 'exception':
            raise RuntimeError('Encoding failed')
        if failure == 'empty-file':
            path = Path(kwargs['output_path'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return kwargs['output_path']

    services.render.side_effect = fail
    response = client.post('/api/projects/project/render/candidate-project-0', json={})
    jobs.run_all()
    clip_id = response.json['clipId']
    assert client.get(f'/api/clips/{clip_id}/status').json['status'] == 'error'
    assert client.get(f'/api/clips/{clip_id}/file').status_code == 404


def test_render_dispatch_failure_does_not_leave_pending_clips(app, client, jobs, monkeypatch):
    seed_project(app, count=2)
    monkeypatch.setattr(jobs, 'submit', Mock(side_effect=RuntimeError('Executor unavailable')))
    response = client.post('/api/projects/project/render-batch', json={})
    assert response.status_code == 500
    assert isinstance(response.json['error'], str)
    assert all(clip['status'] == 'error' for clip in rows(app, 'SELECT status FROM clips'))


def wait_for(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    pytest.fail('Background jobs did not reach the expected state')


def test_real_executor_caps_combined_single_and_batch_renders_at_two(app, client, services):
    seed_project(app, count=5)
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    completed = []
    render = services.render.side_effect

    def blocked(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            assert release.wait(10)
            return render(**kwargs)
        finally:
            with lock:
                active -= 1
                completed.append(kwargs['output_path'])

    services.render.side_effect = blocked
    try:
        assert client.post('/api/projects/project/render/candidate-project-0', json={}).status_code == 202
        assert client.post('/api/projects/project/render-batch', json={}).status_code == 202
        wait_for(lambda: active >= 2)
        assert len(rows(app, 'SELECT * FROM clips')) == 5
        assert peak == 2
        assert len(rows(app, "SELECT * FROM clips WHERE status='pending'")) == 3
    finally:
        release.set()
        wait_for(lambda: len(rows(app, "SELECT * FROM clips WHERE status IN ('pending','rendering')")) == 0)
    assert len(completed) == 5
    assert peak == 2
    assert all(clip['status'] == 'done' for clip in rows(app, 'SELECT status FROM clips'))


def test_real_ffmpeg_pipeline_extracts_renders_captions_and_serves_completed_clip(
        app, client, jobs, tmp_path, monkeypatch):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real FFmpeg and FFprobe required')
    seed_project(app, count=0, status='imported')
    source = tmp_path / 'source.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=2', '-y', str(source)],
                   check=True, capture_output=True, timeout=30)
    execute(app, 'UPDATE projects SET source_path=?, source_duration=2', (str(source),))
    execute(app, 'DELETE FROM transcripts')
    with app.app_context():
        audio = Path(Config.get_data_dir()) / 'projects' / 'project' / 'audio.wav'
    audio.unlink()
    transcript = {
        **TRANSCRIPT, 'duration': 2,
        'words': [{'text': "It's 100%: yes!", 'start': 0.1, 'end': 0.8},
                  {'text': 'Caption', 'start': 0.8, 'end': 1.8}],
        'segments': [{'text': 'Captioned audio clip', 'start': 0, 'end': 2}],
    }
    monkeypatch.setattr(transcription, 'transcribe_deepgram', Mock(return_value=transcript))
    monkeypatch.setattr(analysis, 'analyze_transcript', Mock(return_value=[{
        **DRAFTS[0], 'start': 0.25, 'end': 1.25,
    }]))
    assert client.post('/api/projects/project/transcribe').status_code == 202
    jobs.run_all()
    assert client.get('/api/projects/project/transcribe/status').json['status'] == 'done'
    assert media_service.probe_media(str(audio))['has_audio'] is True
    assert client.post('/api/projects/project/analyze').status_code == 202
    jobs.run_all()
    candidate_id = rows(app, 'SELECT id FROM candidates')[0]['id']
    assert client.patch('/api/projects/project/candidates', json={'selectedIds': [candidate_id]}).status_code == 200
    response = client.post('/api/projects/project/render-batch', json={'captionStyle': 'minimal'})
    assert response.status_code == 202
    clip_id = response.json['clipIds'][0]
    assert client.get(f'/api/clips/{clip_id}/file').status_code == 404
    jobs.run_all()
    assert client.get(f'/api/clips/{clip_id}/status').json['status'] == 'done'
    output = rows(app, 'SELECT output_path FROM clips')[0]['output_path']
    metadata = media_service.probe_media(output)
    assert (metadata['width'], metadata['height']) == (1080, 1920)
    assert metadata['has_audio'] is True
    assert metadata['duration_sec'] == pytest.approx(1, abs=0.15)
    response = client.get(f'/api/clips/{clip_id}/file', buffered=True)
    assert response.status_code == 200
    assert response.mimetype == 'video/mp4'
    assert len(response.data) == Path(output).stat().st_size
    response.close()


def assert_download_contract(data):
    assert {'status', 'path', 'error', 'progress', 'log'} <= data.keys()
    assert 0 <= data['progress'] <= 100
    assert isinstance(data['log'], list)
    assert data['error'] is None or isinstance(data['error'], str)


def test_youtube_uuid_progress_log_and_service_result_contract(app, client, jobs, services):
    original = services.download.side_effect
    response = client.post('/api/youtube/download', json={'url': URL})
    assert response.status_code == 202
    job_id = response.json['jobId']
    assert str(uuid.UUID(job_id)) == job_id
    status_url = f'/api/youtube/download/{job_id}'
    assert_download_contract(client.get(status_url).json)

    def download(url, directory, progress_cb):
        for line, expected in [('[download] 12.5% of 1MiB', 12.5), ('[download] 125%', 100),
                               ('[download] -5%', 0)]:
            progress_cb(line)
            status = client.get(status_url).json
            assert_download_contract(status)
            assert status['progress'] == expected
        for index in range(1000):
            progress_cb(f'line {index}: ' + 'x' * 3000)
        return original(url, directory, progress_cb)

    services.download.side_effect = download
    jobs.run_all()
    status = client.get(status_url).json
    assert_download_contract(status)
    assert status['status'] == 'done'
    assert status['progress'] == 100
    assert status['path'] and Path(status['path']).is_file()
    assert 1 <= len(status['log']) <= 200
    assert max(map(len, status['log'])) <= 2000
    services.probe.assert_called_once_with(status['path'])


@pytest.mark.parametrize('failure', ['exception', 'service-error', 'missing', 'empty', 'probe', 'no-media', 'duration'])
def test_youtube_never_completes_failed_or_unverified_downloads(app, client, jobs, services, failure):
    original = services.download.side_effect

    def download(url, directory, progress_cb):
        progress_cb('[download] 25.0%')
        if failure == 'exception':
            raise RuntimeError('Unexpected download failure')
        if failure == 'service-error':
            return {'ok': False, 'error': 'Download rejected'}
        result = original(url, directory, progress_cb)
        if failure == 'missing':
            Path(result['path']).unlink()
        if failure == 'empty':
            Path(result['path']).write_bytes(b'')
        return result

    services.download.side_effect = download
    if failure == 'probe':
        services.probe.side_effect = RuntimeError('Invalid media')
    if failure == 'no-media':
        services.probe.return_value = {**METADATA, 'has_video': False, 'has_audio': False}
    if failure == 'duration':
        services.probe.return_value = {**METADATA, 'duration_sec': 0}
    response = client.post('/api/youtube/download', json={'url': URL})
    jobs.run_all()
    status = client.get(f'/api/youtube/download/{response.json["jobId"]}').json
    assert_download_contract(status)
    assert status['status'] == 'error'
    assert status['path'] is None
    assert status['error']
    assert status['log']


def test_youtube_uses_unique_job_directories(app, client, jobs, services):
    ids = [client.post('/api/youtube/download', json={'url': URL}).json['jobId'] for _ in range(2)]
    jobs.run_all()
    directories = [call.args[1] for call in services.download.call_args_list]
    assert len(set(directories)) == 2
    for job_id, directory in zip(ids, directories):
        assert Path(directory).name == job_id
        assert client.get(f'/api/youtube/download/{job_id}').json['status'] == 'done'


def test_youtube_unknown_job_has_status_contract(client):
    response = client.get('/api/youtube/download/missing')
    assert response.status_code == 404
    assert_download_contract(response.json)


def test_youtube_dispatch_failure_is_terminal(client, jobs, monkeypatch):
    monkeypatch.setattr(jobs, 'submit', Mock(side_effect=RuntimeError('Executor unavailable')))
    response = client.post('/api/youtube/download', json={'url': URL})
    assert response.status_code == 500
    assert isinstance(response.json['error'], str)
    assert response.json['jobId']
    status = client.get(f'/api/youtube/download/{response.json["jobId"]}').json
    assert_download_contract(status)
    assert status['status'] == 'error'


def test_real_executor_caps_downloads_at_two(client, services):
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    original = services.download.side_effect
    ids = []

    def blocked(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            assert release.wait(10)
            return original(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    services.download.side_effect = blocked
    try:
        for _ in range(5):
            response = client.post('/api/youtube/download', json={'url': URL})
            assert response.status_code == 202
            ids.append(response.json['jobId'])
        wait_for(lambda: active >= 2)
        assert peak == 2
    finally:
        release.set()
        wait_for(lambda: all(client.get(f'/api/youtube/download/{job_id}').json['status']
                            in ('done', 'error') for job_id in ids))
    assert peak == 2
    assert all(client.get(f'/api/youtube/download/{job_id}').json['status'] == 'done' for job_id in ids)


def test_pipeline_workers_use_config_data_dir_in_app_context(app, client, jobs, services, tmp_path, monkeypatch):
    seed_project(app)
    configured = tmp_path / 'configured'
    audio = configured / 'projects' / 'project' / 'audio.wav'
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b'audio')

    def get_data_dir():
        from flask import current_app
        assert current_app._get_current_object() is app
        return str(configured)

    monkeypatch.setattr(Config, 'get_data_dir', staticmethod(get_data_dir))
    assert client.post('/api/projects/project/transcribe', json={}).status_code == 202
    jobs.run_all()
    assert services.transcribe.call_args.args[0] == str(audio)
    assert client.post('/api/projects/project/render-batch', json={}).status_code == 202
    assert client.post('/api/youtube/download', json={'url': URL}).status_code == 202
    jobs.run_all()
    assert Path(services.render.call_args.kwargs['output_path']).is_relative_to(configured)
    assert Path(services.download.call_args.args[1]).is_relative_to(configured)


@pytest.mark.parametrize('endpoint,body', [
    ('youtube/download', []), ('youtube/download', {'url': 1}),
    ('youtube/check', {'url': []}), ('ollama/pull', {'model': {}}),
])
def test_invalid_youtube_or_ollama_request_is_json_error(client, jobs, endpoint, body):
    response = client.post(f'/api/{endpoint}', json=body)
    assert response.status_code == 400
    assert isinstance(response.json['error'], str)
    assert jobs.tasks == []


def test_youtube_check_unexpected_error_is_json(client, monkeypatch):
    monkeypatch.setattr(youtube_service, 'check_youtube_copyright', Mock(side_effect=RuntimeError('Check failed')))
    response = client.post('/api/youtube/check', json={'url': URL})
    assert response.status_code == 500
    assert isinstance(response.json['error'], str)


def test_ollama_pull_recognizes_http_200_error_and_disables_streaming(client, monkeypatch):
    response = Mock()
    response.json.return_value = {'error': 'model not found'}
    post = Mock(return_value=response)
    monkeypatch.setattr('requests.post', post)
    result = client.post('/api/ollama/pull', json={'model': 'missing-model'})
    assert result.status_code >= 400
    assert result.json['ok'] is False
    assert 'model not found' in result.json['error']
    assert result.json['message'] == result.json['error']
    assert post.call_args.kwargs['json'] == {'name': 'missing-model', 'stream': False}
