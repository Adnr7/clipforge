import json
import math
import uuid
import threading

from flask import Blueprint, jsonify, current_app

from backend.config import Config
from backend.database import get_db
from backend.services.provider_service import get_active_profile, get_profile, runtime_profile
from backend.routes.transcription import (
    _RouteError, _check_project_available, _error_message, _fail_project_job,
    _get_project, _json_object, _now, _project_job_status,
)
from backend.services.llm_service import PROVIDERS, analyze_transcript, parse_candidates
from backend.services.llm_service import provider_model
from backend.services.visual_analysis_service import analyze_video
from backend.services.editing_brief import validate_editing_brief

analysis_bp = Blueprint('analysis', __name__)


def analysis_connection(db, data):
    """One selection contract for speech, visuals, recommendations and chat."""
    provider = data.get('provider')
    profile = None
    if data.get('profileId') is not None:
        try:
            profile = get_profile(db, data['profileId'], kind='llm')
        except ValueError as exc:
            raise _RouteError(str(exc)) from exc
        if provider is not None and (not isinstance(provider, str)
                or provider.strip().lower() != profile['provider']):
            raise _RouteError('provider does not match profileId')
    elif provider is None:
        profile = get_active_profile(db, 'llm')
    if provider is None:
        provider = profile['provider'] if profile else Config.get_llm_provider()
    if not isinstance(provider, str) or provider.strip().lower() not in (*PROVIDERS, 'custom'):
        raise _RouteError('Unknown LLM provider')
    return provider.strip().lower(), runtime_profile(profile)


def transcript_context(db, project_id, required=False):
    """Optional speech never prevents a visual job or project chat."""
    row = db.execute('SELECT raw_json FROM transcripts WHERE project_id=? '
                     'ORDER BY created_at DESC, rowid DESC LIMIT 1', (project_id,)).fetchone()
    if row is None:
        if required:
            raise _RouteError('Project has no transcript — transcribe first')
        return '', {}
    try:
        value = json.loads(row['raw_json'])
        if not isinstance(value, dict) or not {'segments', 'words'} & value.keys():
            raise ValueError('Missing transcript text arrays')
        texts = []
        for key in ('segments', 'words'):
            parts = value.get(key, [])
            if (not isinstance(parts, list) or any(not isinstance(part, dict)
                    or not isinstance(part.get('text'), str) for part in parts)):
                raise ValueError('Invalid transcript text array')
            texts.append(' '.join(part['text'] for part in parts).strip())
        return texts[0] or texts[1], value
    except (ValueError, TypeError, AttributeError) as exc:
        if required:
            raise _RouteError('Project transcript is invalid — transcribe again') from exc
        return '', {}


def _run_analysis(app, project_id, transcript_text, duration, provider, profile=None,
                  mode='transcript', source_path=None, brief=''):
    """Validate the complete replacement before changing any existing candidates."""
    with app.app_context():
        try:
            visual = None
            if mode == 'visual':
                visual = analyze_video(source_path, provider, profile=profile,
                                       brief=brief, transcript_text=transcript_text)
                visual = {**visual, 'model': provider_model(provider, profile), 'provider': provider}
                candidates = visual['candidates']
            else:
                options = {'profile': profile}
                if brief:
                    options['brief'] = brief
                candidates = analyze_transcript(transcript_text, duration, provider, **options)
            if not isinstance(candidates, list) or not candidates:
                raise ValueError('Analysis returned no usable candidates')
            # Reuse the service's schema validation at the persistence boundary.
            candidates = parse_candidates(json.dumps(candidates, allow_nan=False))
            if duration > 0 and any(candidate['end'] > duration for candidate in candidates):
                raise ValueError('Candidate end exceeds the source duration')
            candidates.sort(key=lambda candidate: candidate['score'], reverse=True)

            db = get_db()
            db.execute('BEGIN IMMEDIATE')
            _get_project(db, project_id)
            db.execute('DELETE FROM candidates WHERE project_id=?', (project_id,))
            for rank, candidate in enumerate(candidates, start=1):
                db.execute(
                    'INSERT INTO candidates (id, project_id, start_sec, end_sec, score, hook, rationale, rank, selected) VALUES (?,?,?,?,?,?,?,?,?)',
                    (str(uuid.uuid4()), project_id, candidate['start'], candidate['end'],
                     candidate['score'], candidate['hook'], candidate['rationale'], rank, 0),
                )
            db.execute('''UPDATE projects SET status=?, last_job_stage='analysis',
                          analysis_error=NULL, visual_analysis_json=?, updated_at=? WHERE id=?''',
                       ('analyzed', json.dumps(visual, allow_nan=False) if visual else None, _now(), project_id))
            db.commit()
        except Exception as exc:
            _fail_project_job(project_id, 'analyzing', exc)


@analysis_bp.route('/projects/<project_id>/analyze', methods=['POST'])
def analyze_project(project_id):
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        project = _get_project(db, project_id)
        _check_project_available(db, project)
        data = _json_object()
        if set(data) - {'mode', 'brief', 'provider', 'profileId'}:
            raise _RouteError('Only mode, brief, provider and profileId are accepted')
        mode = data.get('mode', 'transcript')
        if not isinstance(mode, str) or mode not in ('transcript', 'visual'):
            raise _RouteError('mode must be transcript or visual')
        try:
            brief = validate_editing_brief(data.get('brief', ''))
        except ValueError as exc:
            raise _RouteError(str(exc)) from exc
        transcript_text, transcript_data = transcript_context(db, project_id, required=mode == 'transcript')
        try:
            raw_duration = project['source_duration'] or transcript_data.get('duration', 0)
            duration = float(raw_duration)
            if isinstance(raw_duration, bool) or not math.isfinite(duration) or duration < 0:
                raise ValueError('Invalid duration')
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            raise _RouteError('Project transcript is invalid — transcribe again') from exc
        if mode == 'transcript' and not transcript_text:
            raise _RouteError('No speech to analyze. Use Video visuals with an image-capable model, or create a manual cut.')
        provider, profile = analysis_connection(db, data)
        db.execute('''UPDATE projects SET status=?, last_job_stage='analysis',
                      analysis_error=NULL, updated_at=? WHERE id=?''',
                   ('analyzing', _now(), project_id))
        db.commit()
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        db.rollback()
        current_app.logger.exception('Could not queue analysis')
        return jsonify({'error': _error_message(exc)}), 500

    try:
        threading.Thread(
            target=_run_analysis,
            args=(current_app._get_current_object(), project_id, transcript_text, duration, provider,
                  profile, mode, project['source_path'], brief),
            daemon=True,
        ).start()
    except Exception as exc:
        return jsonify({'error': _fail_project_job(project_id, 'analyzing', exc)}), 500
    return jsonify({'status': 'started', 'model': provider_model(provider, profile)}), 202


@analysis_bp.route('/projects/<project_id>/analyze/status', methods=['GET'])
def analysis_status(project_id):
    return _project_job_status(project_id, 'analysis')


@analysis_bp.route('/projects/<project_id>/candidates/manual', methods=['POST'])
def create_manual_candidate(project_id):
    """Append a selected time range without transcription or AI analysis."""
    db = get_db()
    try:
        # Serialize ranks and availability with other candidate and job claims.
        db.execute('BEGIN IMMEDIATE')
        project = _get_project(db, project_id)
        _check_project_available(db, project)
        data = _json_object()
        if set(data) - {'startSec', 'endSec', 'title'}:
            raise _RouteError('Only startSec, endSec and title are accepted')
        times = {}
        for key in ('startSec', 'endSec'):
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _RouteError(f'{key} must be a finite number')
            try:
                value = float(value)
            except (ValueError, TypeError, OverflowError) as exc:
                raise _RouteError(f'{key} must be a finite number') from exc
            if not math.isfinite(value):
                raise _RouteError(f'{key} must be a finite number')
            times[key] = value
        start_sec, end_sec = times['startSec'], times['endSec']
        if start_sec < 0 or end_sec <= start_sec:
            raise _RouteError('Manual cut must satisfy 0 <= startSec < endSec')
        try:
            duration = float(project['source_duration'])
        except (ValueError, TypeError, OverflowError) as exc:
            raise _RouteError('Source duration is unavailable — probe media before creating a manual cut') from exc
        if not math.isfinite(duration) or duration <= 0:
            raise _RouteError('Source duration is unavailable — probe media before creating a manual cut')
        if end_sec > duration:
            raise _RouteError('endSec must not exceed the source duration')
        title = data.get('title', '')
        if not isinstance(title, str) or len(title.strip()) > 200:
            raise _RouteError('title must be text of at most 200 characters')
        rank = db.execute('SELECT COALESCE(MAX(rank), 0) + 1 FROM candidates WHERE project_id=?',
                          (project_id,)).fetchone()[0]
        candidate = {
            'id': str(uuid.uuid4()), 'project_id': project_id,
            'start_sec': start_sec, 'end_sec': end_sec, 'score': 0,
            'hook': title.strip() or 'Manual cut',
            'rationale': 'Manually selected time range; no AI analysis.',
            'rank': rank, 'selected': 1,
        }
        db.execute('''INSERT INTO candidates
                      (id, project_id, start_sec, end_sec, score, hook, rationale, rank, selected)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   (candidate['id'], project_id, start_sec, end_sec, candidate['score'],
                    candidate['hook'], candidate['rationale'], rank, candidate['selected']))
        # A manual choice is not a completed analysis/transcription job.
        db.execute('UPDATE projects SET updated_at=? WHERE id=?', (_now(), project_id))
        db.commit()
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        db.rollback()
        current_app.logger.exception('Could not create manual cut')
        return jsonify({'error': _error_message(exc)}), 500
    return jsonify(candidate), 201


@analysis_bp.route('/projects/<project_id>/candidates', methods=['PATCH'])
def update_candidates(project_id):
    """Validate the full selection and its ownership before resetting anything."""
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        _get_project(db, project_id)
        candidate_ids = _json_object().get('selectedIds')
        if (not isinstance(candidate_ids, list)
                or any(not isinstance(value, str) or not value.strip() for value in candidate_ids)):
            raise _RouteError('selectedIds must be an array of candidate IDs')
        candidate_ids = set(candidate_ids)
        owned = {row['id'] for row in db.execute('SELECT id FROM candidates WHERE project_id=?', (project_id,))}
        if not candidate_ids <= owned:
            raise _RouteError('selectedIds contains candidates that do not belong to this project')
        db.execute('UPDATE candidates SET selected=0 WHERE project_id=?', (project_id,))
        db.executemany('UPDATE candidates SET selected=1 WHERE project_id=? AND id=?',
                       [(project_id, candidate_id) for candidate_id in candidate_ids])
        db.commit()
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        db.rollback()
        current_app.logger.exception('Could not update candidate selection')
        return jsonify({'error': _error_message(exc)}), 500
    return jsonify({'ok': True})
