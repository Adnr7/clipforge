import json
import hashlib
import os
import uuid
import tempfile
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from backend.config import Config
from backend.database import get_db
from backend.routes.file_responses import send_temporary_file
from backend.routes.transcription import (
    _RouteError, _check_project_available, _error_message, _get_project, _json_object,
)
from backend.services.render_settings import (
    resolve_project_settings, validate_caption_settings, validate_video_filters,
    validate_output_settings, output_dimensions, LEGACY_OUTPUT_SETTINGS,
)

rendering_bp = Blueprint('rendering', __name__)

# Single and batch requests (including different app instances) share this cap.
_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='clipforge-render')


def _caption_digest(words, candidate, caption_settings, width=1080, height=1920):
    """Hash only the caption text/chunks/timing actually emitted inside this cut.

    Use the renderer's caption builder so clipping, ordering, speaker splits,
    casing, text fitting and half-open timing cannot drift from the identity.
    Transcript IDs, metadata and words outside the cut do not affect exports.
    """
    from backend.services.caption_service import build_drawtext_filters

    filters = build_drawtext_filters(
        words or [], caption_settings['preset'], candidate['start_sec'], candidate['end_sec'],
        caption_settings, width=width, height=height)
    content = json.dumps({'width': width, 'height': height, 'filters': filters},
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(content).hexdigest()


def _saved_render_identity(serialized):
    """Normalize known settings; unrecorded caption content cannot prove reuse."""
    try:
        saved = json.loads(serialized)
        if (not isinstance(saved, dict) or not isinstance(saved.get('caption'), dict)
                or not isinstance(saved.get('videoFilters'), dict)):
            return None
        identity = {
            'caption': validate_caption_settings(saved['caption']),
            'videoFilters': validate_video_filters(saved['videoFilters']),
            'outputSettings': validate_output_settings(saved.get('outputSettings', LEGACY_OUTPUT_SETTINGS)),
        }
        size = saved.get('outputDimensions')
        if size is None:
            if 'outputSettings' in saved:
                return None
            size = output_dimensions(LEGACY_OUTPUT_SETTINGS)
        if (not isinstance(size, dict) or set(size) != {'width', 'height'}
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 2 or value > identity['outputSettings']['maxDimension'] or value % 2
                       for value in size.values())):
            return None
        identity['outputDimensions'] = size
        if identity['caption']['enabled']:
            digest = saved.get('captionContentDigest')
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(character not in '0123456789abcdef' for character in digest)):
                return None
            identity['captionContentDigest'] = digest
        return identity
    except (ValueError, TypeError, KeyError):
        return None


def _serialize_identity(identity):
    return json.dumps(identity, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _render_error(clip_id, error):
    message = _error_message(error)
    current_app.logger.exception('Render failed for clip %s', clip_id)
    db = get_db()
    db.rollback()
    try:
        db.execute('UPDATE clips SET status=?, render_log=? WHERE id=?',
                   ('error', message, clip_id))
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Could not persist render failure for %s', clip_id)
    return message


def _run_render(app, project_id, clip_id, output_path, start_sec, end_sec, caption_words,
                caption_style, caption_settings, video_filters, output_settings, output_size):
    with app.app_context():
        try:
            db = get_db()
            db.execute('BEGIN IMMEDIATE')
            project = _get_project(db, project_id)
            claimed = db.execute('UPDATE clips SET status=?, render_log=? WHERE id=? AND status=?',
                                 ('rendering', 'FFmpeg started', clip_id, 'pending')).rowcount
            db.commit()
            if not claimed:
                return

            from backend.services.media_service import render_portrait_clip
            render_portrait_clip(
                source_path=project['source_path'], output_path=output_path,
                start_sec=start_sec, end_sec=end_sec,
                caption_words=caption_words, caption_style=caption_style,
                caption_settings=caption_settings, video_filters=video_filters,
                output_settings=output_settings, output_size=output_size,
            )
            if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                raise ValueError('Render did not produce a completed clip file')
            db.execute('UPDATE clips SET status=?, render_log=? WHERE id=? AND status=?',
                       ('done', 'Render complete', clip_id, 'rendering'))
            db.commit()
        except Exception as exc:
            _render_error(clip_id, exc)


def _queue_renders(project_id, candidate_id=None):
    db = get_db()
    clip_ids = []
    new_jobs = []
    active_found = False
    try:
        # Serialize candidate deduplication with other renders and project claims.
        db.execute('BEGIN IMMEDIATE')
        project = _get_project(db, project_id)
        _check_project_available(db, project, check_renders=False)
        if candidate_id is None:
            candidates = db.execute(
                'SELECT * FROM candidates WHERE project_id=? AND selected=1 ORDER BY rank',
                (project_id,),
            ).fetchall()
            if not candidates:
                raise _RouteError('No selected candidates to render')
        else:
            candidate = db.execute('SELECT * FROM candidates WHERE id=? AND project_id=?',
                                   (candidate_id, project_id)).fetchone()
            if candidate is None:
                raise _RouteError('Candidate not found', 404)
            candidates = [candidate]
        data = _json_object()
        try:
            settings = resolve_project_settings(project, data)
            caption_settings = settings['captionSettings']
            video_filters = settings['videoFilters']
            output_settings = settings['outputSettings']
            metadata = None
            if output_settings['aspectRatio'] == 'source':
                from backend.services.media_service import probe_media
                metadata = probe_media(project['source_path'])
            size = output_dimensions(output_settings, metadata)
        except ValueError as exc:
            raise _RouteError(str(exc))
        caption_words = None
        if caption_settings['enabled']:
            transcript = db.execute(
                'SELECT raw_json FROM transcripts WHERE project_id=? ORDER BY created_at DESC LIMIT 1',
                (project_id,),
            ).fetchone()
            try:
                caption_words = json.loads(transcript['raw_json']).get('words', []) if transcript else None
                if caption_words is not None and not isinstance(caption_words, list):
                    raise ValueError('Invalid caption words')
            except (ValueError, AttributeError, TypeError) as exc:
                raise _RouteError('Project transcript is invalid — transcribe again') from exc
        output_dir = os.path.join(Config.get_data_dir(), 'projects', project_id, 'clips')
        for candidate in candidates:
            identity = {'caption': caption_settings, 'videoFilters': video_filters,
                        'outputSettings': output_settings, 'outputDimensions': size}
            if caption_settings['enabled']:
                identity['captionContentDigest'] = _caption_digest(
                    caption_words, candidate, caption_settings, size['width'], size['height'])
            serialized_settings = _serialize_identity(identity)
            active_clips = db.execute(
                "SELECT id, render_settings_json FROM clips WHERE candidate_id=? "
                "AND status IN ('pending','rendering') ORDER BY rowid DESC",
                (candidate['id'],),
            ).fetchall()
            active = None
            for pending in active_clips:
                saved = _saved_render_identity(pending['render_settings_json'])
                # A retry may share a job only when its entire settings and
                # caption snapshot match the preview/requested export.
                if saved != identity:
                    continue
                active = pending
                break
            if active:
                clip_ids.append(active['id'])
                active_found = True
                continue
            # A completed request with the same settings AND effective captions is
            # the requested export.  Returning it makes POST retries safe while
            # still allowing a deliberate render with changed settings.
            completed = db.execute(
                '''SELECT id, output_path, render_settings_json FROM clips
                   WHERE candidate_id=? AND status='done' ORDER BY rowid DESC''',
                (candidate['id'],),
            ).fetchall()
            for previous in completed:
                saved = _saved_render_identity(previous['render_settings_json'])
                if saved is None:
                    continue
                previous_settings = _serialize_identity(saved)
                if (previous_settings == serialized_settings
                        and previous['output_path']
                        and os.path.isfile(previous['output_path'])
                        and os.path.getsize(previous['output_path']) > 0):
                    clip_ids.append(previous['id'])
                    break
            else:
                clip_id = str(uuid.uuid4())
                output_path = os.path.join(output_dir, f'clip_{clip_id}.mp4')
                db.execute(
                    'INSERT INTO clips (id, candidate_id, status, output_path, render_log, render_settings_json) VALUES (?,?,?,?,?,?)',
                    (clip_id, candidate['id'], 'pending', output_path, 'Queued', serialized_settings),
                )
                clip_ids.append(clip_id)
                # The caption service clips intersecting words to the requested span.
                new_jobs.append((project_id, clip_id, output_path, candidate['start_sec'],
                                 candidate['end_sec'], caption_words, caption_settings['preset'],
                                 caption_settings, video_filters, output_settings, size))
                continue
        db.commit()
    except _RouteError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        db.rollback()
        current_app.logger.exception('Could not queue renders')
        return jsonify({'error': _error_message(exc)}), 500

    app = current_app._get_current_object()
    error = None
    for args in new_jobs:
        try:
            _RENDER_EXECUTOR.submit(_run_render, app, *args)
        except Exception as exc:
            # A rejected submission must not leave any of the batch stuck pending.
            error = _render_error(args[1], exc)
    if error:
        return jsonify({'error': error, 'clipIds': clip_ids}), 500
    if candidate_id is not None:
        if not new_jobs and not active_found:
            return jsonify({'clipId': clip_ids[0], 'status': 'done'}), 200
        return jsonify({'clipId': clip_ids[0], 'status': 'started'}), 202
    if not new_jobs and not active_found:
        return jsonify({'clipIds': clip_ids, 'count': len(clip_ids), 'status': 'done'}), 200
    return jsonify({'clipIds': clip_ids, 'count': len(clip_ids), 'status': 'started'}), 202


@rendering_bp.route('/projects/<project_id>/render/<candidate_id>', methods=['POST'])
def render_clip(project_id, candidate_id):
    return _queue_renders(project_id, candidate_id)


@rendering_bp.route('/projects/<project_id>/render-batch', methods=['POST'])
def render_batch(project_id):
    return _queue_renders(project_id)


@rendering_bp.route('/clips/<clip_id>/status', methods=['GET'])
def clip_status(clip_id):
    row = get_db().execute('SELECT status, render_log FROM clips WHERE id=?', (clip_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Clip not found'}), 404
    return jsonify({'status': row['status'], 'log': row['render_log'],
                    'error': row['render_log'] if row['status'] == 'error' else None})


@rendering_bp.route('/clips/<clip_id>/file', methods=['GET'])
def serve_clip_file(clip_id):
    row = get_db().execute('''
        SELECT c.id, c.status, c.output_path, cand.rank
        FROM clips c JOIN candidates cand ON c.candidate_id=cand.id
        WHERE c.id=?
    ''', (clip_id,)).fetchone()
    if row is None or row['status'] != 'done' or not row['output_path']:
        return jsonify({'error': 'Completed clip not found'}), 404
    try:
        if not os.path.isfile(row['output_path']) or os.path.getsize(row['output_path']) == 0:
            return jsonify({'error': 'Clip file not found on disk'}), 404
        return send_file(
            row['output_path'], mimetype='video/mp4',
            download_name=_clip_download_name(row['rank'], row['id']), as_attachment=False,
        )
    except OSError:
        return jsonify({'error': 'Clip file not found on disk'}), 404


def _export_file(row, project_id):
    """Only completed, nonempty MP4 artifacts inside this project's clip directory."""
    if row['status'] != 'done' or not row['output_path']:
        return None
    try:
        projects_root = (Path(Config.get_data_dir()) / 'projects').resolve()
        project_root = projects_root / project_id
        clip_root = project_root / 'clips'
        if project_root.resolve() != project_root or clip_root.resolve() != clip_root:
            return None
        path = Path(row['output_path']).resolve(strict=True)
        if (clip_root.is_relative_to(projects_root) and path.is_relative_to(clip_root)
                and path.suffix.lower() == '.mp4' and path.is_file() and path.stat().st_size > 0):
            return path
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def _clip_artifact_id(clip_id):
    """Return a readable, filesystem-safe identifier for a rendered artifact."""
    value = str(clip_id)
    safe = secure_filename(value)
    if safe:
        return safe
    return f"artifact-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:10]}"


def _clip_download_name(rank, clip_id):
    """Use the same recognizable ClipForge naming for inline MP4 responses."""
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        rank = 0
    return f"clipforge-clip-{rank:03d}-{_clip_artifact_id(clip_id)}.mp4"


def _archive_clip_name(rank, clip_id, used_names):
    """Build deterministic ZIP names and disambiguate sanitized-ID collisions."""
    name = _clip_download_name(rank, clip_id)
    if name in used_names:
        digest = hashlib.sha256(str(clip_id).encode('utf-8')).hexdigest()[:10]
        name = name[:-4] + f"-{digest}.mp4"
    used_names.add(name)
    return name


def _archive_download_name(project, project_id):
    project_name = secure_filename(str(project.get('name') or '').strip())
    project_name = project_name or secure_filename(str(project_id)) or 'project'
    return f"clipforge-{project_name}-clips.zip"


@rendering_bp.route('/projects/<project_id>/export', methods=['GET'])
def export_project(project_id):
    """Download completed artifacts only; never queue a render from an export."""
    db = get_db()
    archive_file = None
    try:
        project = _get_project(db, project_id)
        if set(request.args) - {'clipIds'}:
            raise _RouteError('Only the clipIds query parameter is accepted')
        explicit = 'clipIds' in request.args
        requested = []
        if explicit:
            requested = [item.strip() for group in request.args.getlist('clipIds') for item in group.split(',')]
            if not requested or len(requested) > 500 or any(not item or len(item) > 100 for item in requested):
                raise _RouteError('clipIds must contain 1 to 500 non-empty clip IDs')
            requested = list(dict.fromkeys(requested))
            placeholders = ','.join('?' for _ in requested)
            clips = db.execute(f'''
                SELECT c.*, cand.rank FROM clips c JOIN candidates cand ON c.candidate_id=cand.id
                WHERE cand.project_id=? AND c.id IN ({placeholders}) ORDER BY cand.rank, c.rowid
            ''', [project_id, *requested]).fetchall()
            if len(clips) != len(requested):
                raise _RouteError('clipIds contains clips not found in this project', 404)
            selected_count = len(clips)
        else:
            selected_count = db.execute('SELECT COUNT(*) FROM candidates WHERE project_id=? AND selected=1',
                                        (project_id,)).fetchone()[0]
            clips = db.execute('''
                SELECT c.*, cand.rank FROM candidates cand JOIN clips c ON c.candidate_id=cand.id
                WHERE cand.project_id=? AND cand.selected=1 AND c.status='done'
                  AND c.rowid=(SELECT MAX(latest.rowid) FROM clips latest
                               WHERE latest.candidate_id=cand.id AND latest.status='done')
                ORDER BY cand.rank, c.rowid
            ''', (project_id,)).fetchall()
        available = []
        for clip in clips:
            path = _export_file(clip, project_id)
            if path is not None:
                available.append((clip, path))
            elif explicit:
                raise _RouteError('A requested clip is not completed or its file is unavailable', 409)
        if not available:
            raise _RouteError('No completed clip files are available to export', 409)
        archive_file = tempfile.TemporaryFile(mode='w+b')
        # MP4 is already compressed. ZipFile.write copies in bounded chunks to
        # a disk-backed file instead of buffering video payloads in memory.
        with zipfile.ZipFile(archive_file, mode='w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            used_names = set()
            for clip, path in available:
                name = _archive_clip_name(clip['rank'], clip['id'], used_names)
                archive.write(path, arcname=name)
        response = send_temporary_file(
            archive_file, 'application/zip', _archive_download_name(project, project_id),
            as_attachment=True,
        )
        response.headers['X-Export-Clip-Count'] = str(len(available))
        response.headers['X-Export-Skipped-Count'] = str(selected_count - len(available))
        return response
    except _RouteError as exc:
        if archive_file is not None:
            archive_file.close()
        return jsonify({'error': str(exc)}), exc.status_code
    except OSError:
        if archive_file is not None:
            archive_file.close()
        return jsonify({'error': 'Export files became unavailable. Please retry.'}), 409
    except Exception:
        if archive_file is not None:
            archive_file.close()
        raise
