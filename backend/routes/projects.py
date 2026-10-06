from flask import Blueprint, jsonify, request
from backend.database import get_db
import json
import uuid
from datetime import datetime
import os
import shutil
from pathlib import Path
from subprocess import TimeoutExpired
from werkzeug.utils import secure_filename
from backend.config import Config
from backend.services.media_service import probe_media
from backend.services.project_jobs import AUDIO_EXTRACTION_STATE, project_job_statuses
from backend.services.render_settings import (
    resolve_project_settings, validate_caption_settings, validate_video_filters, validate_output_settings,
)
from backend.routes.transcription import _RouteError, _check_project_available
from backend.services.clip_selection_service import validate_selection

projects_bp = Blueprint('projects', __name__)
_MEDIA_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac', '.m4v'}


def _probe_source(path):
    if Path(path).suffix.lower() not in _MEDIA_EXTENSIONS or not os.path.isfile(path):
        raise ValueError('Choose an existing video or audio file')
    try:
        info = probe_media(path)
    except TimeoutExpired as exc:
        raise ValueError('Media probe timed out') from exc
    if not info.get('has_video') and not info.get('has_audio') and not info.get('audio_codec'):
        raise ValueError('No video or audio stream found')
    return info


def _insert_project(project_id, path, data, info):
    mode = data.get('transcriptionMode') or os.environ.get('TRANSCRIPTION_MODE', 'cloud')
    mode = 'whisper' if mode == 'local' else mode
    style = data.get('captionStyle') or os.environ.get('CAPTION_STYLE', 'classic')
    caption_settings = data.get('captionSettings')
    try:
        caption_settings = validate_caption_settings(
            caption_settings, style_name=style)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    style = caption_settings['preset']
    video_filters = validate_video_filters(data.get('videoFilters'))
    output_settings = validate_output_settings(data.get('outputSettings'))
    if mode not in ('cloud', 'whisper'):
        raise ValueError('Invalid transcription mode or caption style')
    name = data.get('name') or Path(path).stem
    if not isinstance(name, str):
        raise ValueError('Project name must be text')
    now = datetime.now().isoformat()
    db = get_db()
    db.execute(
        'INSERT INTO projects (id, name, source_path, source_duration, status, transcription_mode, caption_style, caption_settings_json, video_filters_json, render_settings_json, source_metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (project_id, name.strip() or 'Untitled Project', path, info['duration_sec'], 'imported', mode,
         style, json.dumps(caption_settings, sort_keys=True, separators=(',', ':')),
         json.dumps(video_filters, sort_keys=True, separators=(',', ':')),
         json.dumps({'outputSettings': output_settings}, sort_keys=True), json.dumps(info), now, now),
    )
    db.commit()


@projects_bp.route('/projects', methods=['GET'])
def list_projects():
    db = get_db()
    rows = db.execute('SELECT * FROM projects ORDER BY updated_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])


@projects_bp.route('/projects', methods=['POST'])
def create_project():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('sourcePath'), str) or not data['sourcePath'].strip():
        return jsonify({'error': 'sourcePath is required'}), 400
    project_id = str(uuid.uuid4())
    try:
        path = os.path.abspath(os.path.expanduser(data['sourcePath'].strip()))
        managed_root = Path(Config.get_data_dir()) / 'projects'
        # Both lexical and resolved paths matter: a symlink inside a project can
        # disappear with its owner even when its target is an external file.
        if (Path(path).is_relative_to(managed_root.absolute())
                or Path(path).resolve().is_relative_to(managed_root.resolve())):
            raise ValueError('This file is project-managed. Use Local file to upload an independent copy.')
        _insert_project(project_id, path, data, _probe_source(path))
    except (ValueError, OSError, RuntimeError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'id': project_id}), 201


@projects_bp.route('/projects/upload', methods=['POST'])
def upload_project():
    media = request.files.get('file')
    if not media or not media.filename or Path(media.filename).suffix.lower() not in _MEDIA_EXTENSIONS:
        return jsonify({'error': 'Choose a supported video or audio file'}), 400
    project_id = str(uuid.uuid4())
    directory = Path(Config.get_data_dir()) / 'projects' / project_id / 'source'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (secure_filename(media.filename) or f'source{Path(media.filename).suffix.lower()}')
    try:
        media.save(path)
        info = _probe_source(str(path))
        _insert_project(project_id, str(path), request.form, info)
    except (ValueError, OSError, RuntimeError) as exc:
        get_db().rollback()
        shutil.rmtree(directory.parent, ignore_errors=True)
        return jsonify({'error': f'Import failed: {exc}'}), 400
    return jsonify({'id': project_id}), 201


@projects_bp.route('/projects/<project_id>', methods=['GET'])
def get_project_detail(project_id):
    db = get_db()
    # Keep jobs, outputs, and clip states in the same read snapshot for polling.
    db.execute('BEGIN')
    row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        db.rollback()
        return jsonify({'error': 'Project not found'}), 404
    project = dict(row)
    project.update(resolve_project_settings(project))
    try:
        project['visualAnalysis'] = json.loads(project.get('visual_analysis_json') or 'null')
    except (ValueError, TypeError):
        project['visualAnalysis'] = None
    transcript = db.execute(
        'SELECT * FROM transcripts WHERE project_id=? ORDER BY created_at DESC LIMIT 1',
        (project_id,)
    ).fetchone()
    candidates = db.execute(
        'SELECT * FROM candidates WHERE project_id=? ORDER BY rank ASC',
        (project_id,)
    ).fetchall()
    clips = db.execute('''
        SELECT c.* FROM clips c
        JOIN candidates cand ON c.candidate_id = cand.id
        WHERE cand.project_id=? ORDER BY c.rowid ASC
    ''', (project_id,)).fetchall()
    db.commit()

    public_candidates = []
    for candidate in candidates:
        item = dict(candidate)
        try:
            selection = json.loads(item.pop('selection_json', None) or 'null')
            item['selection'] = validate_selection(selection) if selection else None
        except (ValueError, TypeError):
            item['selection'] = None
        public_candidates.append(item)
    return jsonify({
        'project': project,
        'transcript': dict(transcript) if transcript else None,
        'candidates': public_candidates,
        'clips': [dict(c) for c in clips],
        'jobs': project_job_statuses(project, transcript is not None, bool(candidates)),
    })


@projects_bp.route('/projects/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        db.rollback()
        return jsonify({'error': 'Project not found'}), 404
    try:
        if row['status'] == AUDIO_EXTRACTION_STATE:
            raise _RouteError('Project is already processing', 409)
        _check_project_available(db, dict(row))
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    # Delete children explicitly (SQLite may not have FK cascade enabled per-connection)
    db.execute('DELETE FROM clip_copies WHERE clip_id IN (SELECT c.id FROM clips c JOIN candidates cand ON c.candidate_id=cand.id WHERE cand.project_id=?)', (project_id,))
    db.execute('DELETE FROM clips WHERE candidate_id IN (SELECT id FROM candidates WHERE project_id=?)', (project_id,))
    db.execute('DELETE FROM candidates WHERE project_id=?', (project_id,))
    db.execute('DELETE FROM transcripts WHERE project_id=?', (project_id,))
    db.execute('DELETE FROM projects WHERE id=?', (project_id,))
    db.commit()
    directory = Path(Config.get_data_dir()) / 'projects' / project_id
    # Only derived/managed artifacts, never the original external source.
    if directory.is_dir() and directory.resolve().parent == (Path(Config.get_data_dir()) / 'projects').resolve():
        shutil.rmtree(directory)
    return '', 204


@projects_bp.route('/projects/<project_id>', methods=['PATCH'])
def rename_project(project_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('name'), str) or not data['name'].strip():
        return jsonify({'error': 'name is required'}), 400
    db = get_db()
    if not db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
        return jsonify({'error': 'Project not found'}), 404
    db.execute('UPDATE projects SET name=?, updated_at=? WHERE id=?',
               (data['name'].strip(), datetime.now().isoformat(), project_id))
    db.commit()
    return '', 204
