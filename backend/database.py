import os
import sqlite3
import json
from flask import g, current_app
from backend.config import Config
from backend.services.project_jobs import recover_project_jobs
from backend.services.render_settings import LEGACY_OUTPUT_SETTINGS

DB_PATH = None

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'], timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db(app):
    global DB_PATH
    data_dir = app.config.setdefault('DATA_DIR', Config.get_data_dir())
    os.makedirs(data_dir, exist_ok=True)
    DB_PATH = os.path.join(data_dir, 'clipforge.db')
    app.config['DATABASE'] = DB_PATH

    app.teardown_appcontext(close_db)

    with app.app_context():
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT,
                source_path TEXT NOT NULL,
                source_duration REAL,
                status TEXT NOT NULL DEFAULT 'imported',
                transcription_mode TEXT NOT NULL DEFAULT 'cloud',
                caption_style TEXT DEFAULT 'classic',
                caption_settings_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transcripts (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                engine TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                language TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidates (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                start_sec REAL NOT NULL,
                end_sec REAL NOT NULL,
                score REAL NOT NULL,
                hook TEXT NOT NULL,
                rationale TEXT NOT NULL,
                rank INTEGER NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                output_path TEXT,
                face_track_json TEXT,
                caption_ass_path TEXT,
                render_log TEXT,
                render_settings_json TEXT
            );
            CREATE TABLE IF NOT EXISTS clip_copies (
                id TEXT PRIMARY KEY,
                clip_id TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                hook_text TEXT,
                caption_text TEXT,
                hashtags TEXT
            );
            CREATE TABLE IF NOT EXISTS provider_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                provider TEXT NOT NULL,
                api_key TEXT,
                base_url TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_active_profiles (
                kind TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL REFERENCES provider_profiles(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS filter_suggestions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                settings_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'ai',
                model TEXT,
                rationale TEXT,
                created_at TEXT NOT NULL
            );
        """)
        # Lightweight migrations for databases created by the original repair
        # release.  SQLite has no portable IF NOT EXISTS for table columns.
        for table, column, definition in (
            ('projects', 'caption_settings_json', 'TEXT'),
            ('projects', 'video_filters_json', 'TEXT'),
            ('projects', 'render_settings_json', 'TEXT'),
            ('projects', 'source_metadata_json', 'TEXT'),
            ('projects', 'last_job_stage', 'TEXT'),
            ('projects', 'transcription_error', 'TEXT'),
            ('projects', 'analysis_error', 'TEXT'),
            ('projects', 'visual_analysis_json', 'TEXT'),
            ('clips', 'render_settings_json', 'TEXT'),
            ('filter_suggestions', 'caption_settings_json', 'TEXT'),
            ('filter_suggestions', 'output_settings_json', 'TEXT'),
            ('filter_suggestions', 'basis', "TEXT NOT NULL DEFAULT 'user-provided'"),
        ):
            columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
            if column not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        # Existing projects explicitly retain their original portrait center crop.
        # New imports write source/contain at creation time.
        db.execute('UPDATE projects SET render_settings_json=? WHERE render_settings_json IS NULL',
                   (json.dumps({'outputSettings': LEGACY_OUTPUT_SETTINGS}, sort_keys=True),))
        # Daemon/executor work cannot survive a process restart. Preserve outputs
        # and expose a retryable error rather than leaving endless progress bars.
        recover_project_jobs(db)
        db.execute("UPDATE clips SET status='error', render_log='Render interrupted. Please retry.' WHERE status IN ('pending','rendering')")
        db.commit()
        db.close()
