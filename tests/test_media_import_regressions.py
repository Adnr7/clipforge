"""Import/transcription regressions using isolated SQLite and mocked I/O boundaries."""

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from backend.database import get_db, init_db
from backend.routes import analysis, media, projects, rendering, transcription
from backend.services import transcription_service
from backend.services.project_jobs import recover_project_jobs


TRANSCRIPT = {
    'language': 'en', 'duration': 10, 'speakers': ['Speaker 1'],
    'words': [{'text': 'Original', 'start': 0, 'end': 1}],
    'segments': [{'text': 'Original', 'start': 0, 'end': 1}],
}
SILENCE = {'metadata': {'duration': 10}, 'results': {'channels': [
    {'alternatives': [{'transcript': '', 'confidence': 0, 'words': []}]},
]}}


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Do not use create_app(): these tests must never load a user's .env or DB.
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('TRANSCRIPTION_MODE', 'cloud')
    monkeypatch.setenv('CAPTION_STYLE', 'classic')
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'regression-test-key')
    monkeypatch.setattr(transcription_service.requests, 'post',
                        Mock(side_effect=AssertionError('Unexpected network request')))
    app = Flask(__name__)
    app.config.update(TESTING=True, DATA_DIR=str(tmp_path / 'data'))
    init_db(app)
    for blueprint in (projects.projects_bp, media.media_bp, transcription.transcription_bp,
                      analysis.analysis_bp, rendering.rendering_bp):
        app.register_blueprint(blueprint, url_prefix='/api')
    return app


@pytest.fixture
def jobs(monkeypatch):
    pending = []

    def thread(target, args=(), **kwargs):
        return SimpleNamespace(start=lambda: pending.append((target, args)))

    monkeypatch.setattr(transcription, 'threading', SimpleNamespace(Thread=thread))
    return pending


def seed_project(app, tmp_path, cache=None, status='transcribed'):
    source = tmp_path / 'external.mp4'
    source.write_bytes(b'original external source')
    directory = Path(app.config['DATA_DIR']) / 'projects' / 'project'
    directory.mkdir(parents=True)
    audio = directory / 'audio.wav'
    if cache is not None:
        audio.write_bytes(cache)
    with app.app_context():
        db = get_db()
        db.execute('''INSERT INTO projects
                      (id, name, source_path, source_duration, status, created_at, updated_at)
                      VALUES ('project', 'Project', ?, 10, ?, '2026-01-01', '2026-01-01')''',
                   (str(source), status))
        db.execute('''INSERT INTO transcripts (id, project_id, engine, raw_json, created_at)
                      VALUES ('original', 'project', 'test', ?, '2026-01-01')''',
                   (json.dumps(TRANSCRIPT),))
        db.execute('''INSERT INTO candidates
                      (id, project_id, start_sec, end_sec, score, hook, rationale, rank, selected)
                      VALUES ('candidate', 'project', 0, 5, 80, 'Hook', 'Reason', 1, 1)''')
        db.commit()
    return source, audio


def complete_jobs(jobs):
    while jobs:
        target, args = jobs.pop(0)
        target(*args)


@pytest.mark.parametrize('cache', [None, b'previous complete audio'])
def test_failed_explicit_extraction_cannot_poison_transcription_retry(
        app, tmp_path, monkeypatch, jobs, cache):
    _, audio = seed_project(app, tmp_path, cache=cache)

    def fail(source, output):
        assert not get_db().in_transaction
        assert Path(output) != audio
        Path(output).write_bytes(b'partial WAV')
        raise RuntimeError('FFmpeg failed')

    monkeypatch.setattr(media, 'extract_audio', fail)
    client = app.test_client()
    response = client.post('/api/projects/project/extract-audio')
    assert response.status_code == 500
    assert audio.read_bytes() == cache if cache is not None else not audio.exists()
    assert list(audio.parent.glob('.audio-*.wav')) == []
    failed = client.get('/api/projects/project').json
    assert failed['project']['status'] == 'error'
    assert failed['transcript']['id'] == 'original'
    assert failed['jobs']['transcription']['status'] == 'error'
    assert 'FFmpeg failed' in failed['jobs']['transcription']['error']
    assert failed['jobs']['analysis'] == {'status': 'done', 'error': None}

    monkeypatch.setattr(media, 'extract_audio', lambda source, output: Path(output).write_bytes(b'complete WAV'))
    assert client.post('/api/projects/project/extract-audio').status_code == 200
    assert audio.read_bytes() == b'complete WAV'
    assert list(audio.parent.glob('.audio-*.wav')) == []
    submitted_audio = []

    def respond(*args, **kwargs):
        submitted_audio.append(kwargs['data'].read())
        return Mock(json=Mock(return_value=SILENCE))

    monkeypatch.setattr(transcription_service.requests, 'post', respond)
    assert client.post('/api/projects/project/transcribe').status_code == 202
    complete_jobs(jobs)
    assert submitted_audio == [b'complete WAV']
    assert client.get('/api/projects/project/transcribe/status').json == {'status': 'done', 'error': None}


def test_extraction_claim_excludes_pipeline_delete_and_duplicate_without_holding_db(
        app, tmp_path, monkeypatch, jobs):
    _, audio = seed_project(app, tmp_path, cache=b'old complete WAV')
    started = threading.Event()
    release = threading.Event()

    def blocked(source, output):
        assert not get_db().in_transaction
        Path(output).write_bytes(b'new complete WAV')
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(media, 'extract_audio', blocked)

    def extract():
        with app.test_client() as client:
            return client.post('/api/projects/project/extract-audio')

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(extract)
        try:
            assert started.wait(5)
            client = app.test_client()
            detail = client.get('/api/projects/project').json
            assert detail['project']['status'] == 'transcribing'
            assert detail['jobs']['transcription'] == {'status': 'processing', 'error': None}
            assert audio.read_bytes() == b'old complete WAV'
            for endpoint in ('extract-audio', 'transcribe', 'analyze', 'render-batch'):
                assert client.post(f'/api/projects/project/{endpoint}').status_code == 409
            assert client.delete('/api/projects/project').status_code == 409
            assert jobs == []
            # A rename must not wait for FFmpeg or invalidate the extraction token.
            assert client.patch('/api/projects/project', json={'name': 'Renamed'}).status_code == 204
            with app.app_context():
                db = get_db()
                db.execute('PRAGMA busy_timeout=200')
                db.execute('BEGIN IMMEDIATE')
                db.commit()
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 200
    assert audio.read_bytes() == b'new complete WAV'
    assert list(audio.parent.glob('.audio-*.wav')) == []
    assert app.test_client().get('/api/projects/project').json['project']['status'] == 'audio_extracted'
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM audio_extraction_claims').fetchone()[0] == 0


@pytest.mark.parametrize('output', [b'', None])
def test_extraction_without_usable_output_is_retryable(app, tmp_path, monkeypatch, output):
    _, audio = seed_project(app, tmp_path)

    def incomplete(source, temporary):
        if output is None:
            Path(temporary).unlink()
        else:
            Path(temporary).write_bytes(output)

    monkeypatch.setattr(media, 'extract_audio', incomplete)
    client = app.test_client()
    assert client.post('/api/projects/project/extract-audio').status_code == 500
    assert not audio.exists()
    assert list(audio.parent.glob('.audio-*.wav')) == []
    assert client.get('/api/projects/project/transcribe/status').json['status'] == 'error'


def test_restart_recovers_audio_claim_without_replacing_previous_outputs(app, tmp_path, monkeypatch):
    _, audio = seed_project(app, tmp_path, cache=b'previous audio', status='audio_extracting')
    # Startup recovery uses the same isolated DB and must work even without last_job_stage.
    restarted = Flask('restarted')
    restarted.config['DATA_DIR'] = app.config['DATA_DIR']
    init_db(restarted)
    client = app.test_client()
    detail = client.get('/api/projects/project').json
    assert detail['project']['status'] == 'error'
    assert detail['project']['last_job_stage'] == 'transcription'
    assert detail['jobs']['transcription']['status'] == 'error'
    assert 'interrupted' in detail['jobs']['transcription']['error']
    assert detail['jobs']['analysis'] == {'status': 'done', 'error': None}
    assert detail['transcript']['id'] == 'original'
    assert audio.read_bytes() == b'previous audio'
    monkeypatch.setattr(media, 'extract_audio', lambda source, output: Path(output).write_bytes(b'retried audio'))
    assert client.post('/api/projects/project/extract-audio').status_code == 200
    assert audio.read_bytes() == b'retried audio'
    assert client.get('/api/projects/project/transcribe/status').json == {'status': 'done', 'error': None}


def test_extractor_cannot_publish_after_its_claim_is_recovered(app, tmp_path, monkeypatch):
    _, audio = seed_project(app, tmp_path, cache=b'previous audio')

    def interrupted(source, output):
        Path(output).write_bytes(b'late result')
        # Simulate recovery on an independent connection while FFmpeg is outside a transaction.
        with app.app_context():
            db = get_db()
            recover_project_jobs(db)
            db.commit()

    monkeypatch.setattr(media, 'extract_audio', interrupted)
    client = app.test_client()
    assert client.post('/api/projects/project/extract-audio').status_code == 409
    assert audio.read_bytes() == b'previous audio'
    assert list(audio.parent.glob('.audio-*.wav')) == []
    assert client.get('/api/projects/project/transcribe/status').json['status'] == 'error'


@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('replacement', ['extraction', 'transcription'])
def test_late_extractor_cannot_complete_or_fail_a_replacement_claim(
        app, tmp_path, monkeypatch, jobs, failure, replacement):
    _, audio = seed_project(app, tmp_path, cache=b'previous audio')
    expected_status = 'audio_extracted' if replacement == 'extraction' else 'transcribing'

    def interrupted(source, output):
        Path(output).write_bytes(b'late old audio')
        with app.app_context():
            db = get_db()
            recover_project_jobs(db)
            db.commit()
        if replacement == 'extraction':
            monkeypatch.setattr(media, 'extract_audio',
                                lambda source, output: Path(output).write_bytes(b'new claimed audio'))
            assert app.test_client().post('/api/projects/project/extract-audio').status_code == 200
        else:
            assert app.test_client().post('/api/projects/project/transcribe').status_code == 202
        if failure:
            raise RuntimeError('Failure from the old extractor')

    monkeypatch.setattr(media, 'extract_audio', interrupted)
    client = app.test_client()
    response = client.post('/api/projects/project/extract-audio')
    assert response.status_code == (500 if failure else 409)
    detail = client.get('/api/projects/project').json
    assert detail['project']['status'] == expected_status
    assert detail['project']['transcription_error'] is None
    assert detail['transcript']['id'] == 'original'
    assert audio.read_bytes() == (b'new claimed audio' if replacement == 'extraction' else b'previous audio')
    assert list(audio.parent.glob('.audio-*.wav')) == []
    if replacement == 'transcription':
        response = Mock(json=Mock(return_value=SILENCE))
        monkeypatch.setattr(transcription_service.requests, 'post', Mock(return_value=response))
        complete_jobs(jobs)
        assert client.get('/api/projects/project/transcribe/status').json == {'status': 'done', 'error': None}


def test_old_extractor_does_not_publish_while_new_extraction_is_running(app, tmp_path, monkeypatch):
    _, audio = seed_project(app, tmp_path, cache=b'previous audio')
    old_started = threading.Event()
    new_started = threading.Event()
    release_old = threading.Event()
    release_new = threading.Event()
    calls = 0

    def extract(source, output):
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(output).write_bytes(b'old audio')
            old_started.set()
            assert release_old.wait(10)
        else:
            Path(output).write_bytes(b'new audio')
            new_started.set()
            assert release_new.wait(10)

    def request_extraction():
        with app.test_client() as client:
            return client.post('/api/projects/project/extract-audio')

    monkeypatch.setattr(media, 'extract_audio', extract)
    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(request_extraction)
        try:
            assert old_started.wait(5)
            with app.app_context():
                db = get_db()
                recover_project_jobs(db)
                db.commit()
            new = executor.submit(request_extraction)
            assert new_started.wait(5)
            release_old.set()
            assert old.result(timeout=5).status_code == 409
            assert audio.read_bytes() == b'previous audio'
            detail = app.test_client().get('/api/projects/project').json
            assert detail['project']['status'] == 'transcribing'
            assert detail['jobs']['transcription'] == {'status': 'processing', 'error': None}
        finally:
            release_old.set()
            release_new.set()
        assert new.result(timeout=5).status_code == 200
    assert audio.read_bytes() == b'new audio'
    assert list(audio.parent.glob('.audio-*.wav')) == []


def test_transcription_claim_blocks_explicit_extraction(app, tmp_path, monkeypatch, jobs):
    seed_project(app, tmp_path, cache=b'previous audio')
    extract = Mock(side_effect=AssertionError('Extraction must not start during transcription'))
    monkeypatch.setattr(media, 'extract_audio', extract)
    client = app.test_client()
    assert client.post('/api/projects/project/transcribe').status_code == 202
    assert client.post('/api/projects/project/extract-audio').status_code == 409
    extract.assert_not_called()
    assert len(jobs) == 1


@pytest.mark.parametrize('payload', [
    {}, {'results': {'channels': []}}, {'error': 'upstream error'},
    {'results': {'channels': [{'alternatives': [{'transcript': 'Speech', 'words': [None]}]}]}},
    {'results': {'channels': [{'alternatives': [{'transcript': 'Speech', 'words': [
        {'word': 'broken', 'start': 1, 'end': 0},
    ]}]}]}},
    ValueError('JSON decode error with private response body'),
])
def test_malformed_deepgram_success_preserves_previous_transcript(
        app, tmp_path, monkeypatch, jobs, payload):
    seed_project(app, tmp_path, cache=b'complete audio')
    response = Mock(json=Mock(side_effect=payload) if isinstance(payload, Exception)
                    else Mock(return_value=payload))
    monkeypatch.setattr(transcription_service.requests, 'post', Mock(return_value=response))
    client = app.test_client()
    assert client.post('/api/projects/project/transcribe').status_code == 202
    complete_jobs(jobs)
    detail = client.get('/api/projects/project').json
    assert detail['transcript']['id'] == 'original'
    assert json.loads(detail['transcript']['raw_json']) == TRANSCRIPT
    assert detail['jobs']['transcription']['status'] == 'error'
    assert 'invalid transcription response' in detail['jobs']['transcription']['error']
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM transcripts').fetchone()[0] == 1
    # Real silence has a valid envelope and is allowed to complete a retry.
    response.json.side_effect = None
    response.json.return_value = SILENCE
    assert client.post('/api/projects/project/transcribe').status_code == 202
    complete_jobs(jobs)
    detail = client.get('/api/projects/project').json
    assert detail['jobs']['transcription'] == {'status': 'done', 'error': None}
    assert detail['transcript']['id'] != 'original'
    assert json.loads(detail['transcript']['raw_json'])['words'] == []


def test_server_path_cannot_reference_another_projects_upload(app, monkeypatch):
    monkeypatch.setattr(projects, 'probe_media', lambda path: {
        'duration_sec': 10, 'has_video': True, 'has_audio': True,
    })
    client = app.test_client()
    uploaded = client.post('/api/projects/upload', data={
        'file': (io.BytesIO(b'uploaded media'), 'source.mp4'),
    })
    assert uploaded.status_code == 201
    owner = uploaded.json['id']
    source = Path(client.get(f'/api/projects/{owner}').json['project']['source_path'])
    response = client.post('/api/projects', json={'sourcePath': str(source)})
    assert response.status_code == 400
    assert 'project-managed' in response.json['error']
    assert 'independent copy' in response.json['error']
    assert len(client.get('/api/projects').json) == 1
    assert source.read_bytes() == b'uploaded media'
    assert client.delete(f'/api/projects/{owner}').status_code == 204
    assert not source.exists()


def test_server_path_import_and_delete_preserve_external_user_file(app, tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'probe_media', lambda path: {
        'duration_sec': 10, 'has_video': True, 'has_audio': True,
    })
    source = tmp_path / 'external.wav'
    source.write_bytes(b'users original media')
    client = app.test_client()
    imported = client.post('/api/projects', json={'sourcePath': str(source)})
    assert imported.status_code == 201
    assert client.delete(f'/api/projects/{imported.json["id"]}').status_code == 204
    assert source.read_bytes() == b'users original media'


def test_legacy_extraction_claim_cannot_be_deleted(app, tmp_path):
    _, audio = seed_project(app, tmp_path, cache=b'previous audio', status='audio_extracting')
    client = app.test_client()
    assert client.delete('/api/projects/project').status_code == 409
    assert audio.read_bytes() == b'previous audio'
    assert client.get('/api/projects/project').status_code == 200


def test_delete_keeps_youtube_download_without_durable_ownership_record(app, monkeypatch):
    monkeypatch.setattr(projects, 'probe_media', lambda path: {
        'duration_sec': 10, 'has_video': True, 'has_audio': True,
    })
    source = Path(app.config['DATA_DIR']) / 'downloads' / 'youtube-job' / 'video.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'managed YouTube media')
    client = app.test_client()
    imported = client.post('/api/projects', json={'sourcePath': str(source)})
    assert imported.status_code == 201
    assert client.delete(f'/api/projects/{imported.json["id"]}').status_code == 204
    assert source.read_bytes() == b'managed YouTube media'
