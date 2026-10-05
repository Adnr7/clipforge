"""
YouTube service — yt-dlp integration for import with copyright pre-check.

Copyright pre-check: reads video metadata (title, license, duration) via yt-dlp
and warns if the content appears to be copyright-protected (non-Creative-Commons
license or a claimed/flagged status).
"""

import subprocess
import json
import os
import threading
from collections import deque
from urllib.parse import parse_qs, urlparse


_RESULT_PREFIX = '__CLIPFORGE_RESULT__'
_DOWNLOAD_TIMEOUT = 600


def _video_url_error(url):
    """--no-playlist alone still permits bare playlist/channel URLs in yt-dlp."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        parts = parsed.path.strip('/').split('/')
        query = parse_qs(parsed.query)
    except (TypeError, ValueError):
        return 'A single YouTube video URL is required; playlists are not supported'
    if parsed.scheme in ('http', 'https'):
        if host in ('youtu.be', 'www.youtu.be') and len(parts) == 1 and parts[0]:
            return None
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
                    'youtube-nocookie.com', 'www.youtube-nocookie.com'):
            if parsed.path.rstrip('/') == '/watch' and query.get('v'):
                return None
            if (len(parts) == 2 and parts[0] in ('shorts', 'live', 'embed')
                    and parts[1] and parts[1] != 'videoseries'):
                return None
    return 'A single YouTube video URL is required; playlists are not supported'


def check_youtube_copyright(url: str) -> dict:
    """
    Inspect a YouTube URL's metadata and assess copyright risk.
    Returns { ok, isLikelyCopyrighted, title, duration, license, reason }.
    """
    error = _video_url_error(url)
    if error:
        return {'ok': False, 'error': error}
    cmd = ['yt-dlp', '--ignore-config', '--dump-single-json', '--no-warnings', '--no-playlist', '--', url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return {'ok': False, 'error': 'yt-dlp is not installed. Install it with: pip install yt-dlp'}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'YouTube metadata check timed out'}
    except OSError as exc:
        return {'ok': False, 'error': f'Could not run yt-dlp: {exc}'}

    if result.returncode != 0:
        return {'ok': False, 'error': f'yt-dlp failed: {result.stderr.strip()[:300]}'}

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {'ok': False, 'error': 'Could not parse video metadata'}

    if not isinstance(info, dict) or info.get('_type') in ('playlist', 'multi_video') or 'entries' in info:
        return {'ok': False, 'error': 'Expected one video; playlists are not supported'}
    license_name = str(info.get('license') or '').lower()
    is_cc = 'creative commons' in license_name
    # yt-dlp does not expose Content ID claims directly; treat non-CC public videos
    # as potentially copyrighted and surface the info to the user.
    return {
        'ok': True,
        'videoId': info.get('id'),
        'title': info.get('title'),
        'duration': info.get('duration'),
        'uploader': info.get('uploader'),
        'license': info.get('license'),
        'isCreativeCommons': is_cc,
        'isLikelyCopyrighted': not is_cc,
        'reason': 'Creative Commons licensed' if is_cc else 'Standard YouTube license — you should own this content or have permission before clipping it',
    }


def download_youtube_video(url: str, output_dir: str, progress_cb=None) -> dict:
    """
    Download a YouTube video with yt-dlp into output_dir.
    Returns {ok: True, path, title, duration} or {ok: False, error}.

    progress_cb(line: str) receives stripped human-readable log/progress lines,
    not dictionaries or percentages. Internal final-result records are excluded.
    The caller should provide a unique output_dir per job when isolation is needed.
    The final absolute path comes from this download's after_move event, including
    single-file downloads and files already present; no second download is run.
    """
    error = _video_url_error(url)
    if error:
        return {'ok': False, 'error': error}
    output_dir = os.path.abspath(output_dir)
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        return {'ok': False, 'error': f'Could not create download directory: {exc}'}
    output_template = os.path.join(output_dir, '%(id)s.%(ext)s')

    cmd = [
        'yt-dlp',
        '--ignore-config',
        '-f', 'bv*[height<=1080]+ba/b[height<=1080]',
        '--merge-output-format', 'mp4',
        '-o', output_template,
        '--no-playlist',
        '--newline',
        '--progress',
        '--no-simulate',
        '--print', 'after_move:' + _RESULT_PREFIX +
        '{"path": %(filepath)j, "title": %(title)j, "duration": %(duration)j}',
        '--',
        url,
    ]

    process = None
    timer = None
    timed_out = threading.Event()
    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )

        def stop_download():
            timed_out.set()
            try:
                process.kill()
            except OSError:
                pass

        # A timeout only on wait() does not protect a blocking stdout iterator.
        timer = threading.Timer(_DOWNLOAD_TIMEOUT, stop_download)
        timer.daemon = True
        timer.start()
        final_info = None
        recent_lines = deque(maxlen=20)
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith(_RESULT_PREFIX):
                try:
                    final_info = json.loads(line[len(_RESULT_PREFIX):])
                except json.JSONDecodeError:
                    final_info = None
                continue
            recent_lines.append(line)
            if progress_cb:
                progress_cb(line)
        process.wait(timeout=_DOWNLOAD_TIMEOUT)

        if timed_out.is_set():
            return {'ok': False, 'error': 'Download timed out'}

        if process.returncode != 0:
            return {'ok': False, 'error': 'yt-dlp download failed: ' + '\n'.join(recent_lines)[-1000:]}

        final_path = final_info.get('path') if isinstance(final_info, dict) else None
        if not isinstance(final_path, str) or not os.path.isfile(final_path):
            return {'ok': False, 'error': 'Download completed but output file not found'}

        return {'ok': True, 'path': os.path.abspath(final_path),
                'title': final_info.get('title') or os.path.basename(final_path),
                'duration': final_info.get('duration')}
    except FileNotFoundError:
        return {'ok': False, 'error': 'yt-dlp is not installed. Install it with: pip install yt-dlp'}
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return {'ok': False, 'error': 'Download timed out'}
    except OSError as exc:
        return {'ok': False, 'error': f'yt-dlp download failed: {exc}'}
    finally:
        if timer is not None:
            timer.cancel()
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()
