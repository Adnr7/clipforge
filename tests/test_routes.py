import os
import sys
import json
from unittest.mock import Mock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('TRANSCRIPTION_MODE', 'cloud')
    monkeypatch.setenv('LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    from backend.app import create_app
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app, monkeypatch):
    # CRUD tests isolate ingestion; actual validation is covered in test_settings_imports.
    from backend.routes import projects
    original = projects._probe_source
    monkeypatch.setattr(projects, '_probe_source', lambda path: original(path) if os.path.isfile(path) else {
        'duration_sec': 60, 'has_video': True, 'has_audio': True,
    })
    return app.test_client()


class TestSystemRoutes:
    def test_get_status(self, client):
        response = client.get('/api/status')
        assert response.status_code == 200
        data = response.get_json()
        assert 'hasFfmpeg' in data
        assert 'llmProvider' in data
        assert 'dataDir' in data

    def test_get_settings(self, client):
        response = client.get('/api/settings')
        assert response.status_code == 200
        data = response.get_json()
        assert 'llmProvider' in data

    def test_update_settings(self, client):
        response = client.put('/api/settings', json={'LLM_PROVIDER': 'openai'})
        assert response.status_code == 200
        assert response.get_json()['ok'] is True

    @pytest.mark.parametrize('key', [None, '   '])
    def test_test_connection_missing_key(self, client, monkeypatch, key):
        if key is None:
            monkeypatch.delenv('OPENAI_API_KEY')
        else:
            monkeypatch.setenv('OPENAI_API_KEY', key)
        post = Mock(side_effect=AssertionError('Missing credentials must not reach the network'))
        monkeypatch.setattr('backend.services.llm_service.requests.post', post)
        response = client.post('/api/test-connection', json={'provider': 'openai'})
        assert response.status_code == 200
        data = response.get_json()
        assert data['ok'] is False
        assert 'OPENAI_API_KEY' in data['message']
        assert data['latencyMs'] is None
        post.assert_not_called()

    def test_test_connection_validates_provider_and_allows_local(self, client, monkeypatch):
        check = Mock(return_value={'ok': True, 'message': 'Connected', 'latencyMs': 1})
        monkeypatch.setattr('backend.services.llm_service.check_connection', check)
        for data in (['openai'], {'provider': None}, {'provider': 'unknown'}):
            response = client.post('/api/test-connection', json=data)
            assert response.status_code == 400 and response.is_json
        check.assert_not_called()
        response = client.post('/api/test-connection', json={'provider': ' Ollama '})
        assert response.status_code == 200 and response.json['ok'] is True
        check.assert_called_once_with('ollama')


class TestProjectRoutes:
    def test_create_project(self, client):
        response = client.post('/api/projects', json={
            'name': 'Test Project',
            'sourcePath': '/tmp/test_video.mp4',
        })
        assert response.status_code == 201
        assert 'id' in response.get_json()

    def test_create_project_requires_source(self, client):
        response = client.post('/api/projects', json={'name': 'No path'})
        assert response.status_code == 400

    def test_list_projects(self, client):
        client.post('/api/projects', json={'sourcePath': '/tmp/a.mp4', 'name': 'A'})
        client.post('/api/projects', json={'sourcePath': '/tmp/b.mp4', 'name': 'B'})
        response = client.get('/api/projects')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_get_project_detail(self, client):
        create = client.post('/api/projects', json={'sourcePath': '/tmp/c.mp4', 'name': 'C'})
        pid = create.get_json()['id']
        response = client.get(f'/api/projects/{pid}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['project']['name'] == 'C'
        assert data['transcript'] is None
        assert data['candidates'] == []

    def test_get_project_not_found(self, client):
        response = client.get('/api/projects/nonexistent')
        assert response.status_code == 404

    def test_rename_project(self, client):
        create = client.post('/api/projects', json={'sourcePath': '/tmp/d.mp4', 'name': 'D'})
        pid = create.get_json()['id']
        response = client.patch(f'/api/projects/{pid}', json={'name': 'Renamed'})
        assert response.status_code == 204
        detail = client.get(f'/api/projects/{pid}').get_json()
        assert detail['project']['name'] == 'Renamed'

    def test_delete_project(self, client, tmp_path):
        source = tmp_path / 'external.mp4'
        create = client.post('/api/projects', json={'sourcePath': str(source), 'name': 'E'})
        pid = create.get_json()['id']
        source.write_bytes(b'external source')
        response = client.delete(f'/api/projects/{pid}')
        assert response.status_code == 204
        assert source.read_bytes() == b'external source'
        detail = client.get(f'/api/projects/{pid}')
        assert detail.status_code == 404

    def test_delete_cascades(self, client):
        """Deleting a project removes its transcripts and candidates."""
        from backend.database import get_db
        create = client.post('/api/projects', json={'sourcePath': '/tmp/f.mp4', 'name': 'F'})
        pid = create.get_json()['id']
        client.post(f'/api/projects/{pid}/probe')

        # Manually insert transcript + candidate
        with client.application.app_context():
            db = get_db()
            db.execute(
                'INSERT INTO transcripts (id, project_id, engine, raw_json, language, created_at) VALUES (?,?,?,?,?,?)',
                ('t1', pid, 'test', json.dumps({'words': []}), 'en', '2026-01-01T00:00:00')
            )
            db.execute(
                'INSERT INTO candidates (id, project_id, start_sec, end_sec, score, hook, rationale, rank, selected) VALUES (?,?,?,?,?,?,?,?,?)',
                ('c1', pid, 0.0, 30.0, 90.0, 'Hook', 'Rationale', 1, 1)
            )
            db.execute(
                'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
                ('cl1', 'c1', 'rendering')
            )
            db.execute('INSERT INTO clip_copies (id, clip_id, platform) VALUES (?,?,?)',
                       ('copy1', 'cl1', 'shorts'))
            db.commit()

        assert client.delete(f'/api/projects/{pid}').status_code == 409
        assert client.get(f'/api/projects/{pid}').status_code == 200
        with client.application.app_context():
            db = get_db()
            assert db.execute('SELECT COUNT(*) FROM clip_copies').fetchone()[0] == 1
            db.execute("UPDATE clips SET status='done' WHERE id='cl1'")
            db.commit()

        response = client.delete(f'/api/projects/{pid}')
        assert response.status_code == 204

        with client.application.app_context():
            db = get_db()
            assert db.execute('SELECT COUNT(*) FROM transcripts WHERE project_id=?', (pid,)).fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM candidates WHERE project_id=?', (pid,)).fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM clips').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM clip_copies').fetchone()[0] == 0


class TestMediaRoutes:
    def test_probe_missing_file(self, client):
        create = client.post('/api/projects', json={'sourcePath': '/tmp/does_not_exist.mp4'})
        pid = create.get_json()['id']
        response = client.post(f'/api/projects/{pid}/probe')
        assert response.status_code == 404
        assert 'error' in response.get_json()

    def test_probe_real_video(self, client, tmp_path):
        """Integration: probe an actual generated test video."""
        import subprocess
        video_path = str(tmp_path / 'test.mp4')
        subprocess.run([
            'ffmpeg', '-f', 'lavfi', '-i', 'testsrc=duration=2:size=640x360:rate=30',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', '-y', video_path
        ], capture_output=True, check=True)

        create = client.post('/api/projects', json={'sourcePath': video_path, 'name': 'Real video'})
        pid = create.get_json()['id']
        response = client.post(f'/api/projects/{pid}/probe')
        assert response.status_code == 200
        data = response.get_json()
        assert data['duration_sec'] == pytest.approx(2.0, abs=0.5)
        assert data['has_video'] is True
        assert data['width'] == 640


class TestAnalysisRoutes:
    def test_analyze_without_transcript(self, client):
        create = client.post('/api/projects', json={'sourcePath': '/tmp/g.mp4'})
        pid = create.get_json()['id']
        response = client.post(f'/api/projects/{pid}/analyze')
        assert response.status_code == 400
        assert 'transcript' in response.get_json()['error'].lower()


class TestRenderingRoutes:
    def test_render_missing_candidate(self, client):
        create = client.post('/api/projects', json={'sourcePath': '/tmp/h.mp4'})
        pid = create.get_json()['id']
        response = client.post(f'/api/projects/{pid}/render/nonexistent')
        assert response.status_code == 404

    def test_render_batch_no_selected(self, client):
        create = client.post('/api/projects', json={'sourcePath': '/tmp/i.mp4'})
        pid = create.get_json()['id']
        response = client.post(f'/api/projects/{pid}/render-batch')
        assert response.status_code == 400
