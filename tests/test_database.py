import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import get_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    from backend.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


class TestDatabase:
    def test_init_db_creates_tables(self, app):
        with app.app_context():
            db = get_db()
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t['name'] for t in tables]
            assert 'projects' in table_names
            assert 'transcripts' in table_names
            assert 'candidates' in table_names
            assert 'clips' in table_names
            assert 'clip_copies' in table_names

    def test_create_and_read_project(self, app):
        with app.app_context():
            db = get_db()
            db.execute(
                'INSERT INTO projects (id, name, source_path, status, created_at, updated_at) VALUES (?,?,?,?,?,?)',
                ('test-id', 'Test Project', '/path/to/video.mp4', 'imported', '2026-01-01T00:00:00', '2026-01-01T00:00:00')
            )
            db.commit()
            project = db.execute('SELECT * FROM projects WHERE id=?', ('test-id',)).fetchone()
            assert project['name'] == 'Test Project'
            assert project['source_path'] == '/path/to/video.mp4'


def test_legacy_database_migrates_job_errors_and_preserves_interrupted_stage(tmp_path, monkeypatch):
    from backend.app import create_app

    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    with sqlite3.connect(tmp_path / 'clipforge.db') as db:
        db.execute('''CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT, source_path TEXT NOT NULL,
            source_duration REAL, status TEXT NOT NULL DEFAULT 'imported',
            transcription_mode TEXT NOT NULL DEFAULT 'cloud', caption_style TEXT DEFAULT 'classic',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        db.executemany('''INSERT INTO projects (id, source_path, status, created_at, updated_at)
                          VALUES (?, 'source.mp4', ?, 'now', 'now')''',
                       [('transcription', 'transcribing'), ('analysis', 'analyzing'), ('legacy', 'error')])
    app = create_app()
    client = app.test_client()
    for project_id, stage in [('transcription', 'transcription'), ('analysis', 'analysis'), ('legacy', 'transcription')]:
        detail = client.get(f'/api/projects/{project_id}').json
        assert detail['project']['status'] == 'error'
        assert detail['jobs'][stage]['status'] == 'error'
        assert 'interrupted' in detail['jobs'][stage]['error']
        assert sum(job['status'] == 'error' for job in detail['jobs'].values()) == 1
        if project_id != 'legacy':
            assert detail['project']['last_job_stage'] == stage
    before = client.get('/api/projects/analysis').json
    assert create_app().test_client().get('/api/projects/analysis').json == before
