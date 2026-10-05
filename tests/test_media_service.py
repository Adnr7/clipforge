import os
import tempfile
import json
import shutil
import subprocess
import pytest
from unittest.mock import patch, MagicMock

# Ensure backend is importable
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.media_service import probe_media, extract_audio, render_portrait_clip


class TestProbeMedia:
    @patch('backend.services.media_service.subprocess.run')
    def test_cover_art_is_not_video_and_stream_duration_is_used(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({
            'format': {'duration': 'N/A'},
            'streams': [
                {'codec_type': 'video', 'width': 500, 'height': 500, 'disposition': {'attached_pic': 1}},
                {'codec_type': 'audio', 'codec_name': 'mp3', 'duration': '3.5'},
            ],
        }))
        result = probe_media('song.mp3')
        assert result['has_video'] is False
        assert result['has_audio'] is True
        assert result['duration_sec'] == 3.5

    @patch('backend.services.media_service.subprocess.run')
    def test_probe_returns_metadata(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = '''{
            "format": {"duration": "120.5"},
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080, "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"}
            ]
        }'''
        mock_run.return_value = mock_result
        mock_run.return_value.returncode = 0

        result = probe_media('/fake/video.mp4')
        assert result['duration_sec'] == 120.5
        assert result['has_video'] is True
        assert result['width'] == 1920
        assert result['height'] == 1080
        assert result['video_codec'] == 'h264'
        assert result['audio_codec'] == 'aac'

    @patch('backend.services.media_service.subprocess.run')
    def test_probe_no_video(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = '''{
            "format": {"duration": "60.0"},
            "streams": [
                {"codec_type": "audio", "codec_name": "mp3"}
            ]
        }'''
        mock_run.return_value = mock_result
        mock_run.return_value.returncode = 0

        result = probe_media('/fake/audio.mp3')
        assert result['has_video'] is False
        assert result['duration_sec'] == 60.0


class TestBuildCaptionFilters:
    def test_build_drawtext_filters_chunks_words(self):
        from backend.services.caption_service import build_drawtext_filters
        words = [
            {'text': 'Hello', 'start': 0.0, 'end': 1.0},
            {'text': 'world', 'start': 1.0, 'end': 2.0},
            {'text': 'ClipForge', 'start': 2.0, 'end': 3.0},
        ]
        filters = build_drawtext_filters(words, 'classic', 0)
        assert len(filters) == 2  # 3 words -> 2 chunks (2+1)
        assert 'Hello world' in filters[0]
        assert 'ClipForge' in filters[1]

    def test_build_drawtext_filters_offsets_clip_start(self):
        from backend.services.caption_service import build_drawtext_filters
        words = [
            {'text': 'Test', 'start': 5.0, 'end': 6.0},
        ]
        filters = build_drawtext_filters(words, 'minimal', 3.0)
        # The timestamp should be offset by clip_start=3.0
        assert 'gte(t,2.000000)*lt(t,3.000000)' in filters[0]

    def test_styles_contain_required_keys(self):
        from backend.services.caption_service import STYLES
        for style_name, style in STYLES.items():
            assert 'fontsize' in style
            assert 'fontcolor' in style


@pytest.mark.parametrize('start,end', [(-1, 1), (0, float('nan')), (0, float('inf')), (2, 1)])
def test_render_rejects_invalid_timestamps(start, end):
    with pytest.raises(ValueError):
        render_portrait_clip('unused', 'unused.mp4', start, end)


@pytest.mark.parametrize('source_size', ['320x180', '90x320', '161x241', None])
def test_real_portrait_render_supports_landscape_narrow_odd_and_audio(tmp_path, source_size):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real FFmpeg and FFprobe required')
    source = tmp_path / ('source.mkv' if source_size else 'source.wav')
    cmd = ['ffmpeg', '-v', 'error']
    if source_size:
        cmd += ['-f', 'lavfi', '-i', f'testsrc=size={source_size}:rate=10']
    cmd += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000', '-t', '1']
    if source_size:
        cmd += ['-c:v', 'ffv1', '-pix_fmt', 'bgr0']
    cmd += ['-c:a', 'pcm_s16le', '-y', str(source)]
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)
    output = tmp_path / 'nested' / 'clip.mp4'
    render_portrait_clip(str(source), str(output), 0.1, 0.8, width=181, height=321,
                         caption_words=[{'text': "It's 100%: yes, \\!", 'start': 0.2, 'end': 0.7}],
                         caption_style='minimal')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)],
                            capture_output=True, text=True, check=True, timeout=30)
    metadata = json.loads(result.stdout)
    video = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')
    assert (video['width'], video['height']) == (180, 320)
    assert video['pix_fmt'] == 'yuv420p'
    assert video['sample_aspect_ratio'] == '1:1'
    assert any(stream['codec_type'] == 'audio' for stream in metadata['streams'])
    assert 0.6 <= float(metadata['format']['duration']) <= 0.85
