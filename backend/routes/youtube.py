import math
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from flask import Blueprint, current_app, jsonify

from backend.config import Config
from backend.routes.transcription import _RouteError, _error_message, _json_object

youtube_bp = Blueprint('youtube', __name__)

_DOWNLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='clipforge-download')
_DOWNLOAD_LOCK = Lock()
_LOG_LIMIT = 200
_PERCENT = re.compile(r'(?<![\w.])(-?\d+(?:\.\d+)?)\s*%')


def _download_jobs():
    return current_app.extensions.setdefault('youtube_download_jobs', {})


def _text_field(data, name, default=None):
    value = data.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise _RouteError(f'{name} must be a non-empty string')
    return value.strip()


def _download_progress(job_id, line):
    # The service callback supplies log lines, not yt-dlp progress dictionaries.
    line = str(line).strip()[:2000]
    with _DOWNLOAD_LOCK:
        job = _download_jobs()[job_id]
        if line:
            job['log'].append(line)
            del job['log'][:-_LOG_LIMIT]
        match = _PERCENT.search(line)
        if match:
            job['progress'] = max(0, min(100, float(match.group(1))))


def _download_error(job_id, error):
    message = _error_message(error)
    current_app.logger.exception('YouTube download failed for %s', job_id)
    with _DOWNLOAD_LOCK:
        _download_jobs()[job_id].update(status='error', path=None, error=message)
    return message


def _run_download(app, url, job_id):
    with app.app_context():
        try:
            from backend.services.media_service import probe_media
            from backend.services.youtube_service import download_youtube_video

            directory = os.path.abspath(os.path.join(Config.get_data_dir(), 'downloads', job_id))
            result = download_youtube_video(
                url, directory, progress_cb=lambda line: _download_progress(job_id, line),
            )
            if not isinstance(result, dict):
                raise ValueError('Download returned an invalid result')
            if not result.get('ok'):
                raise ValueError(result.get('error') or 'YouTube download failed')
            path = result.get('path')
            if not isinstance(path, str) or not os.path.isfile(path) or os.path.getsize(path) == 0:
                raise ValueError('Download completed without a usable file')
            path = os.path.abspath(path)
            if os.path.commonpath([os.path.realpath(path), os.path.realpath(directory)]) != os.path.realpath(directory):
                raise ValueError('Download returned a file outside its job directory')
            metadata = probe_media(path)
            duration = float(metadata.get('duration_sec') or 0)
            if (not (metadata.get('has_video') or metadata.get('has_audio'))
                    or not math.isfinite(duration) or duration <= 0):
                raise ValueError('Downloaded file contains no playable media')
            with _DOWNLOAD_LOCK:
                _download_jobs()[job_id].update(status='done', path=path, error=None, progress=100)
        except Exception as exc:
            # The job directory is managed, but its file may already have been
            # handed to a later project-creation request.  There is no durable
            # ownership record for this in-memory job, so retaining the exact
            # directory is safer than deleting a file another project uses.
            _download_error(job_id, exc)


@youtube_bp.route('/youtube/check', methods=['POST'])
def youtube_check():
    try:
        url = _text_field(_json_object(), 'url')
        from backend.services.youtube_service import check_youtube_copyright
        result = check_youtube_copyright(url)
        if not isinstance(result, dict):
            raise ValueError('YouTube check returned an invalid result')
        if not result.get('ok'):
            result['error'] = _error_message(result.get('error') or 'YouTube check failed')
        return jsonify(result), 200 if result.get('ok') else 400
    except _RouteError as exc:
        return jsonify({'error': str(exc)}), exc.status_code
    except Exception as exc:
        current_app.logger.exception('YouTube check failed')
        return jsonify({'error': _error_message(exc)}), 500


@youtube_bp.route('/youtube/download', methods=['POST'])
def youtube_download():
    try:
        url = _text_field(_json_object(), 'url')
    except _RouteError as exc:
        return jsonify({'error': str(exc)}), exc.status_code

    job_id = str(uuid.uuid4())
    with _DOWNLOAD_LOCK:
        _download_jobs()[job_id] = {
            'status': 'downloading', 'path': None, 'error': None, 'progress': 0, 'log': [],
        }
    try:
        _DOWNLOAD_EXECUTOR.submit(_run_download, current_app._get_current_object(), url, job_id)
    except Exception as exc:
        return jsonify({'jobId': job_id, 'error': _download_error(job_id, exc)}), 500
    return jsonify({'jobId': job_id, 'status': 'started'}), 202


@youtube_bp.route('/youtube/download/<job_id>', methods=['GET'])
def youtube_download_status(job_id):
    with _DOWNLOAD_LOCK:
        job = _download_jobs().get(job_id)
        if job is None:
            return jsonify({'status': 'unknown', 'path': None, 'error': 'Download job not found',
                            'progress': 0, 'log': []}), 404
        # Snapshot the mutable log while the progress callback is excluded.
        snapshot = {**job, 'log': list(job['log'])}
    return jsonify(snapshot)


@youtube_bp.route('/ollama/pull', methods=['POST'])
def ollama_pull():
    """Wait for Ollama's non-streaming result, including HTTP-200 error bodies."""
    import requests

    try:
        model = _text_field(_json_object(), 'model', 'llama3.2')
        response = requests.post(
            'http://localhost:11434/api/pull',
            json={'name': model, 'stream': False}, timeout=600,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('Ollama returned an invalid response')
        if result.get('error'):
            message = _error_message(result['error'])
            return jsonify({'ok': False, 'error': message, 'message': message}), 502
        return jsonify({'ok': True, 'message': f'Model {model} pulled'})
    except _RouteError as exc:
        return jsonify({'ok': False, 'error': str(exc), 'message': str(exc)}), exc.status_code
    except requests.exceptions.ConnectionError:
        message = 'Ollama is not running. Start it with: ollama serve'
        return jsonify({'ok': False, 'error': message, 'message': message}), 400
    except Exception as exc:
        message = _error_message(exc)
        return jsonify({'ok': False, 'error': message, 'message': message}), 500
