from flask import Blueprint, current_app, jsonify
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from backend.database import get_db
from backend.config import Config
from backend.routes.projects import _MEDIA_EXTENSIONS
from backend.routes.transcription import (
    _check_project_available, _error_message, _get_project, _now, _RouteError,
)
from backend.services.media_service import probe_media, extract_audio
from backend.services.project_jobs import (
    AUDIO_EXTRACTION_STATE, JOB_STATES, claim_audio_extraction,
    owns_audio_extraction, release_audio_extraction,
)

media_bp = Blueprint('media', __name__)


@media_bp.route('/projects/<project_id>/probe', methods=['POST'])
def probe_project(project_id):
    db = get_db()
    row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Project not found'}), 404
    project = dict(row)
    try:
        info = probe_media(project['source_path'])
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': f'Media probe failed: {e}'}), 500

    db.execute('UPDATE projects SET source_duration=?, source_metadata_json=?, updated_at=? WHERE id=?',
               (info['duration_sec'], json.dumps(info), datetime.now().isoformat(), project_id))
    db.commit()
    return jsonify(info)


@media_bp.route('/projects/<project_id>/extract-audio', methods=['POST'])
def extract_project_audio(project_id):
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        project = _get_project(db, project_id)
        if project['status'] == AUDIO_EXTRACTION_STATE:
            raise _RouteError('Project is already processing', 409)
        _check_project_available(db, project)
        if not os.path.isfile(project['source_path']):
            raise _RouteError('Source file not found on disk')
        claim_id = claim_audio_extraction(db, project_id)
        db.execute('''UPDATE projects SET status=?, last_job_stage='transcription',
                      transcription_error=NULL, updated_at=? WHERE id=?''',
                   (JOB_STATES['transcription'], _now(), project_id))
        db.commit()
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        db.rollback()
        current_app.logger.exception('Could not claim audio extraction for %s', project_id)
        return jsonify({'error': _error_message(exc)}), 500

    temporary_path = None
    try:
        directory = os.path.join(Config.get_data_dir(), 'projects', project_id)
        os.makedirs(directory, exist_ok=True)
        audio_path = os.path.join(directory, 'audio.wav')
        descriptor, temporary_path = tempfile.mkstemp(prefix='.audio-', suffix='.wav', dir=directory)
        os.close(descriptor)  # FFmpeg must be able to reopen the file on Windows.
        # The persisted claim, not a long-lived SQLite transaction, excludes peers.
        extract_audio(project['source_path'], temporary_path)
        if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) == 0:
            raise ValueError('Audio extraction did not produce a usable file')

        db.execute('BEGIN IMMEDIATE')
        if not owns_audio_extraction(db, project_id, claim_id):
            raise _RouteError('Audio extraction was interrupted. Please retry.', 409)
        os.replace(temporary_path, audio_path)
        db.execute('''UPDATE projects SET status='audio_extracted',
                      transcription_error=NULL, updated_at=? WHERE id=?''', (_now(), project_id))
        release_audio_extraction(db, project_id, claim_id)
        db.commit()
        return jsonify({'audioPath': audio_path})
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        db.rollback()
        message = f'Audio extraction failed: {_error_message(exc)}'
        current_app.logger.exception('Audio extraction failed for %s', project_id)
        try:
            db.rollback()
            db.execute('BEGIN IMMEDIATE')
            if owns_audio_extraction(db, project_id, claim_id):
                db.execute('''UPDATE projects SET status='error', last_job_stage='transcription',
                              transcription_error=?, updated_at=? WHERE id=?''',
                           (message, _now(), project_id))
                release_audio_extraction(db, project_id, claim_id)
            db.commit()
        except Exception:
            db.rollback()
            current_app.logger.exception('Could not persist extraction failure for %s', project_id)
        return jsonify({'error': message}), 500
    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                current_app.logger.warning('Could not remove temporary audio for %s', project_id)


@media_bp.route('/projects/<project_id>/file', methods=['GET'])
def serve_media_file(project_id):
    """Stream the source media file to the frontend video player."""
    from flask import send_file
    db = get_db()
    row = db.execute('SELECT source_path FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Project not found'}), 404
    source_path = row['source_path']
    if Path(source_path).suffix.lower() not in _MEDIA_EXTENSIONS or not os.path.isfile(source_path):
        return jsonify({'error': 'Source file not found on disk'}), 404
    return send_file(source_path)
