import io
import json
import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest

from backend.services.youtube_service import check_youtube_copyright, download_youtube_video


def fake_process(lines, returncode=0):
    process = MagicMock()
    process.stdout = io.StringIO('\n'.join(lines) + '\n')
    process.returncode = returncode
    process.wait.return_value = returncode
    return process


@pytest.mark.parametrize('filename', ['video.mp4', 'video.webm'])
def test_download_uses_after_move_path_once_and_keeps_line_callback(tmp_path, filename):
    output = tmp_path / filename
    output.write_bytes(b'media')
    payload = {'path': str(output), 'title': 'A title: "quoted"', 'duration': 12.5}
    process = fake_process(['[download] 42.0% of 1MiB', '__CLIPFORGE_RESULT__' + json.dumps(payload)])
    lines = []
    with patch('backend.services.youtube_service.subprocess.Popen', return_value=process) as popen, patch('backend.services.youtube_service.subprocess.run') as run:
        result = download_youtube_video('https://youtube.com/watch?v=abc&list=xyz', str(tmp_path), lines.append)
        assert result == {'ok': True, **payload}
        assert lines == ['[download] 42.0% of 1MiB']
        command = popen.call_args.args[0]
        assert '--no-playlist' in command
        assert '--no-simulate' in command
        assert '--print' in command
        assert any('after_move:' in part and '%(filepath)j' in part for part in command)
        run.assert_not_called()


def test_missing_final_path_never_starts_another_download(tmp_path):
    process = fake_process(['[download] Done'])
    with patch('backend.services.youtube_service.subprocess.Popen', return_value=process), patch('backend.services.youtube_service.subprocess.run') as run:
        result = download_youtube_video('https://youtube.com/watch?v=abc', str(tmp_path))
        assert result['ok'] is False
        assert 'output file' in result['error']
        run.assert_not_called()


def test_download_surfaces_stderr_and_reaps_process(tmp_path):
    process = fake_process(['ERROR: Video unavailable'], returncode=1)
    with patch('backend.services.youtube_service.subprocess.Popen', return_value=process):
        result = download_youtube_video('https://youtube.com/watch?v=abc', str(tmp_path))
        assert result['ok'] is False
        assert 'Video unavailable' in result['error']
        process.wait.assert_called()


def test_download_timeout_kills_and_reaps_process(tmp_path):
    process = fake_process([])
    process.wait.side_effect = [subprocess.TimeoutExpired('yt-dlp', 600), 0]
    with patch('backend.services.youtube_service.subprocess.Popen', return_value=process):
        result = download_youtube_video('https://youtube.com/watch?v=abc', str(tmp_path))
        assert result['ok'] is False
        assert 'timed out' in result['error']
        process.kill.assert_called_once()
        assert process.wait.call_count == 2


def test_timeout_interrupts_a_download_that_emits_no_lines(tmp_path):
    killed = threading.Event()
    process = fake_process([], returncode=1)

    class SilentOutput:
        def __iter__(self):
            assert killed.wait(2), 'Download timeout must cover blocking stdout reads'
            return iter([])

        def close(self):
            pass

    process.stdout = SilentOutput()
    process.kill.side_effect = killed.set
    with patch('backend.services.youtube_service.subprocess.Popen', return_value=process), patch('backend.services.youtube_service._DOWNLOAD_TIMEOUT', 0.01):
        result = download_youtube_video('https://youtube.com/watch?v=abc', str(tmp_path))
        assert result == {'ok': False, 'error': 'Download timed out'}
        process.kill.assert_called_once()
        process.wait.assert_called_once()


def test_check_disables_playlists_and_handles_timeout():
    with patch('backend.services.youtube_service.subprocess.run', side_effect=subprocess.TimeoutExpired('yt-dlp', 60)) as run:
        result = check_youtube_copyright('https://youtube.com/watch?v=abc&list=xyz')
        assert result['ok'] is False
        assert 'timed out' in result['error']
        assert '--no-playlist' in run.call_args.args[0]


@pytest.mark.parametrize('payload', [[], {'_type': 'playlist', 'entries': []}])
def test_check_rejects_non_video_metadata(payload):
    with patch('backend.services.youtube_service.subprocess.run', return_value=MagicMock(returncode=0, stdout=json.dumps(payload))):
        result = check_youtube_copyright('https://youtube.com/watch?v=abc')
        assert result['ok'] is False


@pytest.mark.parametrize('url', [
    'https://youtube.com/playlist?list=xyz',
    'https://www.youtube.com/embed/videoseries?list=xyz',
    'https://youtube.com/@channel/videos',
])
def test_bare_playlist_rejected_before_download(tmp_path, url):
    with patch('backend.services.youtube_service.subprocess.Popen') as popen:
        result = download_youtube_video(url, str(tmp_path))
        assert result['ok'] is False
        assert 'playlist' in result['error'].lower()
        popen.assert_not_called()
