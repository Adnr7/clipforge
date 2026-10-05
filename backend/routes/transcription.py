import json
import os
import threading
import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from backend.config import Config
from backend.database import get_db
from backend.services.project_jobs import JOB_STATES, project_job_statuses
from backend.services.provider_service import (
    get_active_profile, get_profile, sanitize_provider_error, WHISPER_MODELS,
)
from backend.services.transcription_service import transcribe_deepgram

transcription_bp = Blueprint('transcription', __name__)


# Small shared route helpers; SQLite remains the authority for pipeline claims.
class _RouteError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _json_object():
    if not request.get_data():
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise _RouteError('Request body must be a JSON object')
    return data


def _error_message(error):
    message = sanitize_provider_error(error, max_length=2000)
    return message if message != 'Request failed' else type(error).__name__


def _now():
    return datetime.now(timezone.utc).isoformat()


def _get_project(db, project_id):
    row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise _RouteError('Project not found', 404)
    return dict(row)


def _check_project_available(db, project, check_renders=True):
    """Call while holding BEGIN IMMEDIATE, before reserving work."""
    if project['status'] in ('transcribing', 'analyzing', 'processing'):
        raise _RouteError('Project is already processing', 409)
    if check_renders and db.execute('''
        SELECT 1 FROM clips JOIN candidates ON clips.candidate_id=candidates.id
        WHERE candidates.project_id=? AND clips.status IN ('pending', 'rendering') LIMIT 1
    ''', (project['id'],)).fetchone():
        raise _RouteError('Project has active renders', 409)


def _fail_project_job(project_id, state, error):
    message = _error_message(error)
    stage = next(stage for stage, running in JOB_STATES.items() if running == state)
    current_app.logger.exception('%s failed for project %s', state, project_id)
    db = get_db()
    db.rollback()
    try:
        db.execute(f'''UPDATE projects SET status=?, last_job_stage=?, {stage}_error=?, updated_at=?
                       WHERE id=? AND status=?''',
                   ('error', stage, message, _now(), project_id, state))
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Could not persist project failure for %s', project_id)
    return message


def _project_job_status(project_id, stage):
    db = get_db()
    # One SQLite snapshot, even if a worker finishes during this request.
    project = db.execute('''
        SELECT p.*,
            EXISTS(SELECT 1 FROM transcripts WHERE project_id=p.id) AS has_transcript,
            EXISTS(SELECT 1 FROM candidates WHERE project_id=p.id) AS has_candidates
        FROM projects p WHERE id=?
    ''', (project_id,)).fetchone()
    if project is None:
        return jsonify({'error': 'Project not found'}), 404
    jobs = project_job_statuses(project, project['has_transcript'], project['has_candidates'])
    return jsonify(jobs[stage])


def _run_transcription(app, project_id, audio_path, mode, api_key, whisper_model):
    """Extract/transcribe off the request thread, then commit the result atomically."""
    with app.app_context():
        try:
            db = get_db()
            project = _get_project(db, project_id)
            if not os.path.isfile(audio_path) or os.path.getsize(audio_path) == 0:
                from backend.services.media_service import extract_audio
                try:
                    extract_audio(project['source_path'], audio_path)
                except Exception:
                    # FFmpeg can leave a nonempty partial WAV after failure.
                    # It must not be mistaken for extracted audio on a retry.
                    try:
                        os.remove(audio_path)
                    except OSError:
                        pass
                    raise
            if not os.path.isfile(audio_path) or os.path.getsize(audio_path) == 0:
                raise ValueError('Audio extraction did not produce a usable file')

            if mode == 'whisper':
                from backend.services.transcription_service import transcribe_whisper
                transcript = transcribe_whisper(audio_path, whisper_model)
                engine = f'whisper:{whisper_model}'
            else:
                transcript = transcribe_deepgram(audio_path, api_key)
                engine = 'deepgram:nova-2'
            if (not isinstance(transcript, dict)
                    or not isinstance(transcript.get('words'), list)
                    or not isinstance(transcript.get('segments'), list)):
                raise ValueError('Transcription returned an invalid transcript')
            raw_json = json.dumps(transcript, allow_nan=False)

            db.execute('BEGIN IMMEDIATE')
            db.execute(
                'INSERT INTO transcripts (id, project_id, engine, raw_json, language, created_at) VALUES (?,?,?,?,?,?)',
                (str(uuid.uuid4()), project_id, engine, raw_json, transcript.get('language'), _now()),
            )
            db.execute('''UPDATE projects SET status=?, last_job_stage='transcription',
                          transcription_error=NULL, updated_at=? WHERE id=?''',
                       ('transcribed', _now(), project_id))
            db.commit()
        except Exception as exc:
            _fail_project_job(project_id, 'transcribing', exc)


@transcription_bp.route('/projects/<project_id>/transcribe', methods=['POST'])
def transcribe_project(project_id):
    db = get_db()
    try:
        # The project state and render check must be serialized with render claims.
        db.execute('BEGIN IMMEDIATE')
        project = _get_project(db, project_id)
        _check_project_available(db, project)
        data = _json_object()
        mode = data.get('mode', project['transcription_mode'])
        if mode is None:
            mode = project['transcription_mode']
        if mode == 'local':
            mode = 'whisper'
        if mode not in ('cloud', 'whisper'):
            raise _RouteError('mode must be cloud or whisper')
        profile = None
        if data.get('profileId') is not None:
            try:
                profile = get_profile(db, data['profileId'], kind='transcription')
            except ValueError as exc:
                raise _RouteError(str(exc))
        elif data.get('profileId') is None:
            profile = get_active_profile(db, 'transcription')
        if profile is not None:
            profile_provider = profile['provider']
            selected_mode = 'whisper' if profile_provider == 'whisper' else 'cloud'
            if data.get('mode') is not None and mode != selected_mode:
                raise _RouteError('Transcription profile provider does not match mode')
            mode = selected_mode
        api_key = ((profile['api_key'] if profile is not None else None)
                   or os.environ.get('DEEPGRAM_API_KEY', '')).strip()
        if mode == 'cloud' and not api_key:
            raise _RouteError('DEEPGRAM_API_KEY not configured')
        whisper_model = ((profile['model'] if profile is not None else None)
                         or os.environ.get('WHISPER_MODEL', '').strip() or 'base')
        if mode == 'whisper' and whisper_model not in WHISPER_MODELS:
            raise _RouteError('Unknown Whisper model')
        audio_path = os.path.join(Config.get_data_dir(), 'projects', project_id, 'audio.wav')
        if not os.path.isfile(audio_path) and not os.path.isfile(project['source_path']):
            raise _RouteError('Audio not extracted and source file missing')
        db.execute('''UPDATE projects SET status=?, last_job_stage='transcription',
                      transcription_error=NULL, updated_at=? WHERE id=?''',
                   ('transcribing', _now(), project_id))
        db.commit()
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        db.rollback()
        current_app.logger.exception('Could not queue transcription')
        return jsonify({'error': _error_message(exc)}), 500

    try:
        threading.Thread(
            target=_run_transcription,
            args=(current_app._get_current_object(), project_id, audio_path, mode, api_key, whisper_model),
            daemon=True,
        ).start()
    except Exception as exc:
        return jsonify({'error': _fail_project_job(project_id, 'transcribing', exc)}), 500
    return jsonify({'status': 'started'}), 202


@transcription_bp.route('/projects/<project_id>/transcribe/status', methods=['GET'])
def transcription_status(project_id):
    return _project_job_status(project_id, 'transcription')
