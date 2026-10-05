"""Manual cuts use real SQLite claims and can render speech-free media locally."""

import io
import json
import shutil
import subprocess
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.app import create_app
from backend.database import get_db
from backend.routes import analysis
from backend.services import media_service
from tests.test_pipeline_routes import app, client, jobs, execute, rows, seed_project


MANUAL_URL = '/api/projects/project/candidates/manual'
EMPTY_TRANSCRIPT = {'duration': 60, 'words': [], 'segments': []}
NO_SPEECH_ERROR = 'No speech to analyze. Use Video visuals with an image-capable model, or create a manual cut.'


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    analyze = Mock(side_effect=AssertionError('Manual cuts must not call analysis'))
    post = Mock(side_effect=AssertionError('These tests must not make network calls'))
    monkeypatch.setattr(analysis, 'analyze_transcript', analyze)
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    yield
    analyze.assert_not_called()
    post.assert_not_called()


def set_transcript(app, transcript):
    execute(app, 'UPDATE transcripts SET raw_json=?', (json.dumps(transcript),))


@pytest.mark.parametrize('transcript', [None, EMPTY_TRANSCRIPT, {'words': 'malformed'}])
def test_manual_candidate_needs_no_usable_transcript(app, client, jobs, transcript):
    seed_project(app, count=0, status='imported')
    if transcript is None:
        execute(app, 'DELETE FROM transcripts')
    else:
        set_transcript(app, transcript)
    response = client.post(MANUAL_URL, json={'startSec': 0, 'endSec': 60})
    assert response.status_code == 201
    candidate = response.json
    assert candidate == {
        'id': candidate['id'], 'project_id': 'project', 'start_sec': 0, 'end_sec': 60,
        'score': 0, 'hook': 'Manual cut',
        'rationale': 'Manually selected time range; no AI analysis.', 'rank': 1, 'selected': 1,
    }
    assert rows(app, 'SELECT * FROM candidates') == [candidate]
    assert rows(app, 'SELECT status, last_job_stage FROM projects') == [
        {'status': 'imported', 'last_job_stage': None},
    ]
    assert jobs.tasks == []


def test_manual_cut_appends_and_preserves_selections_outputs_and_job_errors(app, client, jobs):
    seed_project(app, count=2, status='error')
    seed_project(app, 'other', count=1)
    execute(app, 'UPDATE candidates SET rank=7, selected=0 WHERE id=?', ('candidate-project-1',))
    execute(app, '''UPDATE projects SET last_job_stage='analysis', analysis_error='No speech',
                   transcription_error='Prior transcription failed' WHERE id='project' ''')
    output = Path(app.config['DATA_DIR']) / 'projects' / 'project' / 'clips' / 'old.mp4'
    output.parent.mkdir(parents=True)
    output.write_bytes(b'completed output')
    execute(app, 'INSERT INTO clips (id, candidate_id, status, output_path) VALUES (?,?,?,?)',
            ('old', 'candidate-project-0', 'done', str(output)))
    candidates_before = rows(app, 'SELECT * FROM candidates ORDER BY rowid')
    clips_before = rows(app, 'SELECT * FROM clips')
    project_before = rows(app, 'SELECT * FROM projects WHERE id=?', ('project',))[0]

    response = client.post(MANUAL_URL, json={
        'startSec': 1.25, 'endSec': 3.5, 'title': "  It's 100%: a manual choice!  ",
    })
    assert response.status_code == 201
    assert response.json['hook'] == "It's 100%: a manual choice!"
    assert response.json['rank'] == 8
    assert rows(app, 'SELECT * FROM candidates ORDER BY rowid') == [*candidates_before, response.json]
    assert rows(app, 'SELECT * FROM clips') == clips_before
    assert output.read_bytes() == b'completed output'
    project_after = rows(app, 'SELECT * FROM projects WHERE id=?', ('project',))[0]
    assert project_after.pop('updated_at') != project_before.pop('updated_at')
    assert project_after == project_before
    reopened = create_app().test_client().get('/api/projects/project').json
    assert reopened['candidates'][-1] == response.json
    assert reopened['clips'] == clips_before
    assert jobs.tasks == []


@pytest.mark.parametrize('body', [
    {}, [], None, {'startSec': 0}, {'endSec': 2},
    {'startSec': True, 'endSec': 2}, {'startSec': 0, 'endSec': False},
    {'startSec': '0', 'endSec': 2}, {'startSec': 0, 'endSec': '2'},
    {'startSec': [], 'endSec': 2}, {'startSec': 0, 'endSec': {}},
    {'startSec': float('nan'), 'endSec': 2}, {'startSec': 0, 'endSec': float('nan')},
    {'startSec': float('-inf'), 'endSec': 2}, {'startSec': 0, 'endSec': float('inf')},
    {'startSec': 10 ** 400, 'endSec': 2}, {'startSec': 0, 'endSec': 10 ** 400},
    {'startSec': -0.1, 'endSec': 2}, {'startSec': 2, 'endSec': 2},
    {'startSec': 3, 'endSec': 2}, {'startSec': 60, 'endSec': 61},
    {'startSec': 0, 'endSec': 60.001},
    {'startSec': 0, 'endSec': 2, 'title': None},
    {'startSec': 0, 'endSec': 2, 'title': 42},
    {'startSec': 0, 'endSec': 2, 'title': 'x' * 201},
    {'startSec': 0, 'endSec': 2, 'selected': False},
])
def test_invalid_manual_request_is_atomic(app, client, body):
    seed_project(app)
    before = rows(app, 'SELECT * FROM candidates')
    project_before = rows(app, 'SELECT * FROM projects')
    response = client.post(MANUAL_URL, json=body)
    assert response.status_code == 400
    assert isinstance(response.json['error'], str)
    assert rows(app, 'SELECT * FROM candidates') == before
    assert rows(app, 'SELECT * FROM projects') == project_before
    assert rows(app, 'SELECT * FROM clips') == []


def test_broken_manual_json_and_missing_project_are_readable(client, app):
    seed_project(app)
    response = client.post(MANUAL_URL, data='{', content_type='application/json')
    assert response.status_code == 400
    assert response.json['error'] == 'Request body must be a JSON object'
    response = client.post('/api/projects/missing/candidates/manual', json={'startSec': 0, 'endSec': 1})
    assert response.status_code == 404
    assert response.json == {'error': 'Project not found'}


@pytest.mark.parametrize('duration', [None, 0, -1, float('nan'), float('inf'), 'unknown'])
def test_manual_cut_requires_known_finite_source_duration(app, client, duration):
    seed_project(app, count=0)
    execute(app, 'UPDATE projects SET source_duration=?', (duration,))
    response = client.post(MANUAL_URL, json={'startSec': 0, 'endSec': 1})
    assert response.status_code == 400
    assert 'Source duration is unavailable' in response.json['error']
    assert rows(app, 'SELECT * FROM candidates') == []


@pytest.mark.parametrize('state', ['transcribing', 'analyzing', 'processing'])
def test_manual_cut_cannot_overlap_a_project_job(app, client, state):
    seed_project(app, status=state)
    before = rows(app, 'SELECT * FROM candidates')
    response = client.post(MANUAL_URL, json={'startSec': 0, 'endSec': 1})
    assert response.status_code == 409
    assert response.json['error'] == 'Project is already processing'
    assert rows(app, 'SELECT * FROM candidates') == before


@pytest.mark.parametrize('state', ['pending', 'rendering'])
def test_manual_cut_cannot_overlap_active_renders(app, client, state):
    seed_project(app)
    execute(app, 'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
            ('active', 'candidate-project-0', state))
    before = rows(app, 'SELECT * FROM candidates')
    response = client.post(MANUAL_URL, json={'startSec': 0, 'endSec': 1})
    assert response.status_code == 409
    assert response.json['error'] == 'Project has active renders'
    assert rows(app, 'SELECT * FROM candidates') == before


def test_concurrent_manual_cuts_have_distinct_serialized_ranks(app):
    seed_project(app, count=0, status='imported')
    ready = threading.Barrier(2)

    def create(index):
        with app.test_client() as client:
            ready.wait(timeout=5)
            response = client.post(MANUAL_URL, json={'startSec': index, 'endSec': index + 1})
            assert response.status_code == 201
            return response.json

    with ThreadPoolExecutor(max_workers=2) as requests:
        candidates = list(requests.map(create, [0, 1]))
    assert len({candidate['id'] for candidate in candidates}) == 2
    assert sorted(candidate['rank'] for candidate in candidates) == [1, 2]
    assert rows(app, 'SELECT rank, selected FROM candidates ORDER BY rank') == [
        {'rank': 1, 'selected': 1}, {'rank': 2, 'selected': 1},
    ]


def test_manual_cut_checks_job_state_after_waiting_for_write_lock(app):
    seed_project(app, count=0)
    entered = threading.Event()

    def create():
        with app.test_client() as client:
            entered.set()
            return client.post(MANUAL_URL, json={'startSec': 0, 'endSec': 1})

    with app.app_context(), ThreadPoolExecutor(max_workers=1) as requests:
        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE projects SET status='analyzing'")
        future = requests.submit(create)
        assert entered.wait(timeout=5)
        db.commit()
        response = future.result(timeout=10)
    assert response.status_code == 409
    assert rows(app, 'SELECT * FROM candidates') == []


def test_manual_cut_persistence_failure_rolls_back_append(app, client):
    seed_project(app)
    candidates_before = rows(app, 'SELECT * FROM candidates')
    project_before = rows(app, 'SELECT * FROM projects')
    execute(app, '''CREATE TRIGGER fail_manual_touch BEFORE UPDATE ON projects
                   BEGIN SELECT RAISE(FAIL, 'write failed'); END''')
    response = client.post(MANUAL_URL, json={'startSec': 0, 'endSec': 1})
    assert response.status_code == 500
    assert 'write failed' in response.json['error']
    assert rows(app, 'SELECT * FROM candidates') == candidates_before
    assert rows(app, 'SELECT * FROM projects') == project_before


@pytest.mark.parametrize('transcript', [
    EMPTY_TRANSCRIPT, {'words': []}, {'segments': []},
    {'segments': [{'text': ' \n '}], 'words': []},
])
def test_empty_transcript_analysis_offers_manual_cut_without_dispatch(app, client, jobs, transcript):
    seed_project(app)
    set_transcript(app, transcript)
    project_before = rows(app, 'SELECT * FROM projects')
    candidates_before = rows(app, 'SELECT * FROM candidates')
    response = client.post('/api/projects/project/analyze')
    assert response.status_code == 400
    assert response.json == {'error': NO_SPEECH_ERROR}
    assert rows(app, 'SELECT * FROM projects') == project_before
    assert rows(app, 'SELECT * FROM candidates') == candidates_before
    assert jobs.tasks == []


@pytest.mark.parametrize('raw_json', [
    '{', 'null', '[]', '{}', '{"segments": null}', '{"words": "wrong"}',
    '{"words": [null]}', '{"segments": [{}]}', '{"segments": [{"text": 4}]}',
])
def test_malformed_transcript_analysis_is_distinct_from_no_speech(app, client, jobs, raw_json):
    seed_project(app)
    execute(app, 'UPDATE transcripts SET raw_json=?', (raw_json,))
    response = client.post('/api/projects/project/analyze')
    assert response.status_code == 400
    assert response.json == {'error': 'Project transcript is invalid — transcribe again'}
    assert jobs.tasks == []


@pytest.mark.parametrize('duration', [-1, float('nan'), float('inf'), True, 'unknown'])
def test_invalid_transcript_duration_is_not_reported_as_no_speech(app, client, jobs, duration):
    seed_project(app)
    execute(app, 'UPDATE projects SET source_duration=NULL')
    set_transcript(app, {**EMPTY_TRANSCRIPT, 'duration': duration})
    response = client.post('/api/projects/project/analyze')
    assert response.status_code == 400
    assert response.json == {'error': 'Project transcript is invalid — transcribe again'}
    assert jobs.tasks == []


@pytest.mark.parametrize('captions_enabled', [False, True])
def test_explicitly_disabled_captions_need_no_valid_transcript(app, client, jobs, monkeypatch,
                                                            captions_enabled):
    seed_project(app, count=0)
    set_transcript(app, {'words': 'malformed'})
    candidate = client.post(MANUAL_URL, json={'startSec': 0, 'endSec': 1}).json

    def render(**kwargs):
        assert kwargs['caption_words'] is None
        output = Path(kwargs['output_path'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'completed caption-free clip')

    render_mock = Mock(side_effect=render)
    monkeypatch.setattr(media_service, 'render_portrait_clip', render_mock)
    response = client.post(f"/api/projects/project/render/{candidate['id']}", json={
        'captionSettings': {'enabled': captions_enabled},
    })
    assert response.status_code == (400 if captions_enabled else 202)
    jobs.run_all()
    if captions_enabled:
        assert response.json['error'] == 'Project transcript is invalid — transcribe again'
        render_mock.assert_not_called()
    else:
        render_mock.assert_called_once()
        assert client.get(f"/api/clips/{response.json['clipId']}/status").json['status'] == 'done'


@pytest.mark.parametrize('transcript', [None, EMPTY_TRANSCRIPT])
def test_real_silent_source_manual_render_download_export_and_retry(
        app, client, jobs, tmp_path, monkeypatch, transcript):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real FFmpeg and FFprobe required')
    source = tmp_path / 'silent source.mp4'
    subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=navy:s=160x90:r=4:d=2', '-an', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-y', str(source),
    ], capture_output=True, check=True, timeout=30)
    assert media_service.probe_media(str(source))['has_audio'] is False
    created = client.post('/api/projects', json={'sourcePath': str(source), 'name': 'Silent manual cut'})
    assert created.status_code == 201
    project_id = created.json['id']
    if transcript is not None:
        execute(app, '''INSERT INTO transcripts (id, project_id, engine, raw_json, created_at)
                       VALUES (?,?,?,?,?)''',
                ('empty', project_id, 'test', json.dumps({**transcript, 'duration': 2}), 'now'))
    candidate = client.post(f'/api/projects/{project_id}/candidates/manual', json={
        'startSec': 0.5, 'endSec': 1.5, 'title': "It's 100%: manually chosen",
    })
    assert candidate.status_code == 201
    real_run = subprocess.run
    run = Mock(wraps=real_run)
    monkeypatch.setattr(media_service.subprocess, 'run', run)
    response = client.post(f'/api/projects/{project_id}/render-batch')
    assert response.status_code == 202
    clip_id = response.json['clipIds'][0]
    assert client.get(f'/api/clips/{clip_id}/file').status_code == 404
    jobs.run_all()
    assert client.get(f'/api/clips/{clip_id}/status').json == {
        'status': 'done', 'log': 'Render complete', 'error': None,
    }
    render_command = next(call.args[0] for call in run.call_args_list if call.args[0][0] == 'ffmpeg')
    assert '0:a:0?' in render_command
    assert 'drawtext' not in render_command[render_command.index('-vf') + 1]
    output = rows(app, 'SELECT output_path FROM clips WHERE id=?', (clip_id,))[0]['output_path']
    metadata = media_service.probe_media(output)
    # New imports keep the source frame instead of silently cropping to portrait.
    assert (metadata['width'], metadata['height']) == (160, 90)
    assert client.get(f'/api/projects/{project_id}').json['project']['outputSettings'] == {
        'mode': 'source', 'aspectRatio': 'source', 'fit': 'contain', 'maxDimension': 1920,
    }
    assert metadata['video_codec'] == 'h264'
    assert metadata['has_audio'] is False
    assert metadata['duration_sec'] == pytest.approx(1, abs=0.15)
    real_run(['ffmpeg', '-nostdin', '-v', 'error', '-i', output, '-f', 'null', '-'],
             capture_output=True, check=True, timeout=30)

    original = Path(output).read_bytes()
    download = client.get(f'/api/clips/{clip_id}/file')
    assert download.status_code == 200 and download.mimetype == 'video/mp4'
    assert download.data == original
    download.close()
    archive_response = client.get(f'/api/projects/{project_id}/export')
    assert archive_response.status_code == 200
    assert archive_response.headers['X-Export-Clip-Count'] == '1'
    with zipfile.ZipFile(io.BytesIO(archive_response.data)) as archive:
        assert archive.read(archive.namelist()[0]) == original
    archive_response.close()
    retry = client.post(f"/api/projects/{project_id}/render/{candidate.json['id']}")
    assert retry.status_code == 200
    assert retry.json == {'clipId': clip_id, 'status': 'done'}
    assert len(rows(app, 'SELECT * FROM clips')) == 1
    assert jobs.tasks == []
    assert Path(output).read_bytes() == original
