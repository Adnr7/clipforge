import io
import os
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

from backend.app import create_app
from backend.database import get_db
from backend.routes.system import _KEYS


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    for key in _KEYS:
        # Track initially absent keys too: settings writes directly to os.environ.
        monkeypatch.setenv(key, '')
        monkeypatch.delenv(key)
    app = create_app()
    app.config['TESTING'] = True
    return app


def test_settings_persist_reload_and_mask(app, monkeypatch):
    client = app.test_client()
    secret = "test-secret's #value"
    assert client.put('/api/settings', json={
        'DEEPSEEK_API_KEY': secret, 'LLM_PROVIDER': 'ollama',
        'TRANSCRIPTION_MODE': 'local',
    }).status_code == 200
    env_path = Path(app.config['DATA_DIR']) / '.env'
    saved = dotenv_values(env_path)
    assert saved['DEEPSEEK_API_KEY'] == secret
    assert saved['TRANSCRIPTION_MODE'] == 'whisper'
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'stale-key')
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('TRANSCRIPTION_MODE', 'cloud')
    restarted = create_app().test_client()
    status = restarted.get('/api/status')
    settings = restarted.get('/api/settings')
    assert os.environ['DEEPSEEK_API_KEY'] == secret
    assert status.json['hasDeepseekKey'] is True
    assert status.json['hasOpenaiKey'] is False
    assert all(isinstance(value, bool) for key, value in status.json.items() if key.endswith('Key'))
    assert settings.json['transcriptionMode'] == 'local'
    assert settings.json['llmProvider'] == 'ollama'
    for response in (status, settings):
        assert 'test-secret' not in response.text and 'stale-key' not in response.text
        assert not set(response.json) & _KEYS


def test_settings_reject_arbitrary_environment(app):
    response = app.test_client().put('/api/settings', json={
        'DEEPSEEK_API_KEY': 'ignored', 'PATH': '/broken',
    })
    assert response.status_code == 400
    assert 'DEEPSEEK_API_KEY' not in os.environ
    assert not (Path(app.config['DATA_DIR']) / '.env').exists()


@pytest.mark.parametrize('preset', [
    'classic', 'minimal', 'bold', 'neon', 'typewriter', 'sunset', 'mono', 'bubble',
])
def test_settings_accept_every_caption_preset(app, preset):
    client = app.test_client()
    assert client.put('/api/settings', json={'CAPTION_STYLE': preset}).status_code == 200
    assert client.get('/api/settings').json['captionStyle'] == preset
    assert dotenv_values(Path(app.config['DATA_DIR']) / '.env')['CAPTION_STYLE'] == preset
    assert create_app().test_client().get('/api/settings').json['captionStyle'] == preset


@pytest.mark.parametrize('key,value', [('CAPTION_STYLE', 'unknown'), ('WHISPER_MODEL', 'custom.pt')])
def test_invalid_caption_or_whisper_setting_is_not_persisted(app, key, value):
    response = app.test_client().put('/api/settings', json={key: value})
    assert response.status_code == 400
    assert key not in os.environ
    assert not (Path(app.config['DATA_DIR']) / '.env').exists()


def test_api_errors_are_json_and_reject_untrusted_origins(app):
    client = app.test_client()
    response = client.get('/api/missing')
    assert response.status_code == 404 and response.is_json
    response = client.post('/api/status')
    assert response.status_code == 405 and response.is_json
    assert 'GET' in response.headers['Allow']
    response = client.put('/api/settings', json={}, headers={'Origin': 'https://example.org'})
    assert response.status_code == 403
    response = client.options('/api/settings', headers={
        'Origin': 'http://localhost:5173', 'Sec-Fetch-Site': 'cross-site',
        'Access-Control-Request-Method': 'PUT',
    })
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == 'http://localhost:5173'
    assert client.get('/api/settings', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_upload_creates_probed_project_and_deletes_managed_files(app, monkeypatch):
    monkeypatch.setattr('backend.routes.projects.probe_media', lambda path: {
        'duration_sec': 20, 'has_video': True, 'has_audio': True,
    })
    client = app.test_client()
    response = client.post('/api/projects/upload', data={
        'file': (io.BytesIO(b'media'), '../../sample.mp4'), 'name': 'Sample',
    })
    assert response.status_code == 201
    pid = response.json['id']
    project = client.get(f'/api/projects/{pid}').json['project']
    path = Path(project['source_path'])
    assert path.is_relative_to(Path(app.config['DATA_DIR']) / 'projects' / pid)
    assert path.read_bytes() == b'media'
    assert project['source_duration'] == 20
    response = client.get(f'/api/projects/{pid}/file', headers={'Range': 'bytes=0-1'})
    assert response.status_code == 206 and response.data == b'me'
    response.close()
    with app.app_context():
        db = get_db()
        db.execute("UPDATE projects SET status='processing' WHERE id=?", (pid,))
        db.commit()
    assert client.delete(f'/api/projects/{pid}').status_code == 409
    assert path.exists()
    with app.app_context():
        db = get_db()
        db.execute("UPDATE projects SET status='imported' WHERE id=?", (pid,))
        db.commit()
    assert client.delete(f'/api/projects/{pid}').status_code == 204
    assert not path.exists()


@pytest.mark.parametrize('error', [ValueError('Invalid media'), subprocess.TimeoutExpired('ffprobe', 30)])
def test_invalid_upload_leaves_no_project_or_file(app, monkeypatch, error):
    def fail(path):
        raise error
    monkeypatch.setattr('backend.routes.projects.probe_media', fail)
    client = app.test_client()
    response = client.post('/api/projects/upload', data={'file': (io.BytesIO(b'bad'), 'bad.mp4')})
    assert response.status_code == 400
    assert client.get('/api/projects').json == []
    assert not list(Path(app.config['DATA_DIR']).glob('projects/*/*'))


def test_database_and_storage_are_app_scoped(app, tmp_path, monkeypatch):
    original_path = app.config['DATA_DIR']
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'other'))
    with app.app_context():
        other = create_app()
        get_db().execute("INSERT INTO projects (id, source_path, created_at, updated_at) VALUES ('p','x','now','now')")
        get_db().commit()
    assert other.config['DATA_DIR'] == str(tmp_path / 'other')
    assert other.config['DATABASE'] != app.config['DATABASE']
    assert len(app.test_client().get('/api/projects').json) == 1
    assert other.test_client().get('/api/projects').json == []
    assert app.test_client().get('/api/status').json['dataDir'] == original_path


def test_restart_marks_interrupted_jobs(app):
    with app.app_context():
        get_db().execute("INSERT INTO projects (id, source_path, status, created_at, updated_at) VALUES ('p','x','transcribing','now','now')")
        get_db().commit()
    assert create_app().test_client().get('/api/projects/p/transcribe/status').json['status'] == 'error'


def test_missing_or_nonmedia_path_cannot_be_served(app, tmp_path):
    client = app.test_client()
    assert client.post('/api/projects', json={'sourcePath': '/nonexistent.mp4'}).status_code == 400
    secret = tmp_path / '.env'
    secret.write_text('KEY=secret')
    assert client.post('/api/projects', json={'sourcePath': str(secret)}).status_code == 400
    assert client.get('/api/projects').json == []
    # Existing databases may contain paths imported before validation was added.
    with app.app_context():
        db = get_db()
        db.execute('INSERT INTO projects (id, source_path, created_at, updated_at) VALUES (?,?,?,?)',
                   ('legacy', str(secret), 'now', 'now'))
        db.commit()
    response = client.get('/api/projects/legacy/file')
    assert response.status_code == 404 and response.is_json
    assert 'KEY=secret' not in response.text
