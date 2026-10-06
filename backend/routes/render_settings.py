import json
import uuid
import tempfile
import math
import os
from subprocess import TimeoutExpired
from threading import BoundedSemaphore
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from backend.config import Config
from backend.database import get_db
from backend.routes.transcription import _RouteError, _get_project, _json_object
from backend.routes.file_responses import send_temporary_file
from backend.services.render_settings import (
    render_settings_contract, validate_video_filters, validate_caption_settings, resolve_project_settings,
    validate_output_settings,
)
from backend.services.provider_service import get_active_profile, runtime_profile, sanitize_provider_error
from backend.services import suggestion_service, llm_service
from backend.services.clip_selection_service import validate_audience_brief


render_settings_bp = Blueprint('render_settings', __name__)
_PREVIEW_SLOTS = BoundedSemaphore(2)
_SUGGESTION_SLOTS = BoundedSemaphore(2)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _suggestion_public(row):
    return {
        'id': row['id'],
        'projectId': row['project_id'],
        'settings': json.loads(row['settings_json']),
        'captionSettings': json.loads(row['caption_settings_json']) if row['caption_settings_json'] else None,
        'outputSettings': json.loads(row['output_settings_json']) if row['output_settings_json'] else None,
        'basis': row['basis'],
        'contextNotice': suggestion_service.CONTEXT_NOTICE if row['basis'] == 'transcript' else
                         'User-provided suggestion metadata; no frames were analyzed.',
        'source': row['source'],
        'model': row['model'],
        'rationale': row['rationale'],
        'applied': False,
        'createdAt': row['created_at'],
    }


@render_settings_bp.route('/render-settings', methods=['GET'])
def get_render_settings():
    """Return the versioned, secret-free render settings contract."""
    return jsonify(render_settings_contract())


@render_settings_bp.route('/projects/<project_id>/render-settings', methods=['GET', 'PUT'])
def save_project_render_settings(project_id):
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        project = _get_project(db, project_id)
        if request.method == 'GET':
            settings = resolve_project_settings(project)
            db.commit()
            return jsonify(settings)
        data = _json_object()
        if set(data) - {'captionSettings', 'videoFilters', 'outputSettings'}:
            raise _RouteError('Only captionSettings, videoFilters and outputSettings are accepted')
        settings = resolve_project_settings(project, data)
        db.execute('''UPDATE projects SET caption_style=?, caption_settings_json=?,
                      video_filters_json=?, render_settings_json=?, updated_at=? WHERE id=?''', (
            settings['captionSettings']['preset'], json.dumps(settings['captionSettings']),
            json.dumps(settings['videoFilters']), json.dumps({'outputSettings': settings['outputSettings']}),
            _now(), project_id))
        db.commit()
        return jsonify(settings)
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), getattr(exc, 'status_code', 400)


@render_settings_bp.route('/projects/<project_id>/preview-frame', methods=['POST'])
def preview_frame(project_id):
    try:
        db = get_db()
        project = _get_project(db, project_id)
        data = _json_object()
        if set(data) - {'captionSettings', 'videoFilters', 'outputSettings', 'time'}:
            raise _RouteError('Only captionSettings, videoFilters, outputSettings and time are accepted')
        settings = resolve_project_settings(project, data)
        time_sec = data.get('time', 0)
        if (isinstance(time_sec, bool) or not isinstance(time_sec, (int, float))
                or not math.isfinite(time_sec) or time_sec < 0):
            raise _RouteError('time must be a finite non-negative number of seconds')
        if project['source_duration'] and time_sec >= project['source_duration']:
            raise _RouteError('time must be before the source duration')
        if not os.path.isfile(project['source_path']):
            raise _RouteError('Source file not found on disk', 404)
        row = db.execute('''SELECT raw_json FROM transcripts WHERE project_id=?
                            ORDER BY created_at DESC, rowid DESC LIMIT 1''', (project_id,)).fetchone()
        try:
            words = json.loads(row['raw_json']).get('words', []) if row else []
            if not isinstance(words, list):
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as exc:
            raise _RouteError('Project transcript is invalid — transcribe again') from exc
        if not _PREVIEW_SLOTS.acquire(blocking=False):
            raise _RouteError('Preview is busy. Please retry.', 429)
        output = None
        try:
            from backend.services.media_service import render_preview_frame
            output = tempfile.TemporaryFile(mode='w+b')
            render_preview_frame(project['source_path'], output, time_sec,
                                 caption_words=words, caption_settings=settings['captionSettings'],
                                 video_filters=settings['videoFilters'], output_settings=settings['outputSettings'])
            return send_temporary_file(output, 'image/jpeg', 'preview.jpg')
        except Exception:
            if output is not None:
                output.close()
            raise
        finally:
            _PREVIEW_SLOTS.release()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), getattr(exc, 'status_code', 400)
    except (RuntimeError, OSError, TimeoutExpired):
        return jsonify({'error': 'Preview failed or timed out. Check the source and FFmpeg installation.'}), 502


@render_settings_bp.route('/projects/<project_id>/filter-suggestions', methods=['GET'])
def list_filter_suggestions(project_id):
    db = get_db()
    if not db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
        return jsonify({'error': 'Project not found'}), 404
    rows = db.execute(
        'SELECT * FROM filter_suggestions WHERE project_id=? ORDER BY created_at DESC',
        (project_id,),
    ).fetchall()
    return jsonify([_suggestion_public(row) for row in rows])


def _store_suggestion(db, project_id, filters, caption, basis, model, rationale, output=None):
    suggestion_id = str(uuid.uuid4())
    # Verify again after the network call: deleting a project cannot resurrect it.
    _get_project(db, project_id)
    db.execute('''
        INSERT INTO filter_suggestions
            (id, project_id, settings_json, caption_settings_json, basis, source, model, rationale, created_at, output_settings_json)
        VALUES (?,?,?,?,?,'ai',?,?,?,?)
    ''', (suggestion_id, project_id, json.dumps(filters, sort_keys=True),
          json.dumps(caption, sort_keys=True) if caption is not None else None,
          basis, model, rationale, _now(), json.dumps(output, sort_keys=True) if output is not None else None))
    db.commit()
    return _suggestion_public(db.execute('SELECT * FROM filter_suggestions WHERE id=?', (suggestion_id,)).fetchone())


@render_settings_bp.route('/projects/<project_id>/filter-suggestions/generate', methods=['POST'])
def generate_filter_suggestion(project_id):
    db = get_db()
    try:
        project = _get_project(db, project_id)
        data = _json_object()
        if set(data) - {'brief', 'audienceBrief'}:
            raise _RouteError('Only brief and audienceBrief are accepted; suggestions use transcript context')
        brief = data.get('brief', '')
        if not isinstance(brief, str) or len(brief) > suggestion_service.BRIEF_LIMIT or '\x00' in brief:
            raise _RouteError('brief must be text of at most 2000 characters')
        audience_brief = validate_audience_brief(data.get('audienceBrief'))
        row = db.execute('''SELECT raw_json FROM transcripts WHERE project_id=?
                            ORDER BY created_at DESC, rowid DESC LIMIT 1''', (project_id,)).fetchone()
        if row is None:
            raise _RouteError('Project has no transcript — transcribe first')
        transcript = suggestion_service.transcript_context(row['raw_json'])
        duration = float(project['source_duration'] or 0)
        if not math.isfinite(duration) or duration < 0:
            raise _RouteError('Source duration is invalid')
        profile = runtime_profile(get_active_profile(db, 'llm'))
        provider = profile['provider'] if profile else Config.get_llm_provider()
        suggestion_service.validate_generation_provider(provider, profile)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), getattr(exc, 'status_code', 400)

    if not _SUGGESTION_SLOTS.acquire(blocking=False):
        return jsonify({'error': 'Suggestion generation is busy. Please retry.'}), 429
    try:
        # No DB write transaction or project/render claim is held over network I/O.
        try:
            result = suggestion_service.generate_suggestion(
                transcript, duration, brief.strip(), provider, profile,
                source_metadata=json.loads(project.get('source_metadata_json') or '{}'),
                audience_brief=audience_brief)
        except Exception as exc:
            message = sanitize_provider_error(exc)
            # Providers sometimes echo credentials in their errors.
            key = (profile or {}).get('api_key') or os.environ.get(
                llm_service.PROVIDERS.get(provider, {}).get('env_key') or 'CUSTOM_API_KEY', '')
            if key:
                message = message.replace(key, '[redacted]')
            return jsonify({'error': f'Suggestion generation failed: {message}'}), 502
        return jsonify(_store_suggestion(db, project_id, result['videoFilters'], result['captionSettings'],
                                       'transcript', llm_service.provider_model(provider, profile),
                                        result['rationale'], result.get('outputSettings'))), 201
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    finally:
        _SUGGESTION_SLOTS.release()


@render_settings_bp.route('/projects/<project_id>/filter-suggestions', methods=['POST'])
def create_filter_suggestion(project_id):
    """Store an AI suggestion for review; it is never applied automatically."""
    db = get_db()
    try:
        _get_project(db, project_id)
        data = _json_object()
        allowed = {'settings', 'videoFilters', 'captionSettings', 'outputSettings', 'source', 'model', 'rationale'}
        unknown = set(data) - allowed
        if unknown:
            raise _RouteError(f'Unknown suggestion field: {sorted(unknown)[0]}')
        source = data.get('source', 'ai')
        if source != 'ai':
            raise _RouteError('source must be ai')
        raw_settings = data.get('videoFilters', data.get('settings'))
        settings = validate_video_filters(raw_settings)
        caption = None
        output = None
        if 'outputSettings' in data:
            if not isinstance(data['outputSettings'], dict):
                raise _RouteError('outputSettings must be a JSON object')
            output = validate_output_settings({'mode': 'ai', **data['outputSettings']})
            if 'aspectRatio' not in data['outputSettings']:
                raise _RouteError('AI suggestions require an explicit aspectRatio')
            output['mode'] = 'ai'
        if 'captionSettings' in data:
            if not isinstance(data['captionSettings'], dict):
                raise _RouteError('captionSettings must be a JSON object')
            caption = validate_caption_settings(data['captionSettings'])
        model = data.get('model')
        rationale = data.get('rationale')
        for value, name, maximum in ((model, 'model', 200), (rationale, 'rationale', 1000)):
            if value is not None and (not isinstance(value, str) or len(value.strip()) > maximum
                                      or any(char in value for char in '\r\n\x00')):
                raise _RouteError(f'{name} must be text')
        return jsonify(_store_suggestion(db, project_id, settings, caption, 'user-provided',
                                        model.strip() if isinstance(model, str) else None,
                                        rationale.strip() if isinstance(rationale, str) else None, output)), 201
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), 400
    except Exception:
        db.rollback()
        raise
