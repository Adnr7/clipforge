"""
Media service — wraps FFmpeg/FFprobe for:
- Probing media metadata (duration, resolution, codecs)
- Extracting audio from video (WAV, 16kHz mono for Whisper)
- Rendering source-ratio or fixed-ratio clips with fit/center-crop
- Burning in captions via drawtext filter
"""

import subprocess
import json
import os
import math


def probe_media(file_path: str) -> dict:
    """Get media file metadata using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format', '-show_streams',
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not result.stdout.strip():
        raise FileNotFoundError(f"ffprobe failed for '{file_path}': {result.stderr.strip()[:200]}")
    data = json.loads(result.stdout)

    video_stream = None
    audio_stream = None
    for s in data.get('streams', []):
        if (s.get('codec_type') == 'video' and video_stream is None
                and not s.get('disposition', {}).get('attached_pic')):
            video_stream = s
        elif s.get('codec_type') == 'audio' and audio_stream is None:
            audio_stream = s

    duration = _duration(data.get('format', {}).get('duration'))
    if not duration:
        duration = max((_duration(stream.get('duration')) for stream in data.get('streams', [])), default=0)
    width = int(video_stream.get('width', 0)) if video_stream else 0
    height = int(video_stream.get('height', 0)) if video_stream else 0
    sar = 1.0
    rotation = 0.0
    if video_stream:
        try:
            numerator, denominator = video_stream.get('sample_aspect_ratio', '1:1').split(':')
            sar = float(numerator) / float(denominator)
            if not math.isfinite(sar) or sar <= 0:
                sar = 1.0
        except (ValueError, TypeError, ZeroDivisionError, AttributeError):
            sar = 1.0
        raw_rotation = next((item['rotation'] for item in video_stream.get('side_data_list', [])
                             if 'rotation' in item), video_stream.get('tags', {}).get('rotate', 0))
        try:
            rotation = float(raw_rotation)
            if not math.isfinite(rotation):
                rotation = 0.0
        except (ValueError, TypeError):
            rotation = 0.0
    display_width, display_height = width * sar, float(height)
    if any(math.isclose(rotation % 360, angle, abs_tol=1) for angle in (90, 270)):
        display_width, display_height = display_height, display_width

    return {
        'duration_sec': duration,
        'has_video': video_stream is not None,
        'has_audio': audio_stream is not None,
        'width': width,
        'height': height,
        'display_width': display_width,
        'display_height': display_height,
        'sample_aspect_ratio': sar,
        'rotation': rotation,
        'video_codec': video_stream.get('codec_name', '') if video_stream else '',
        'audio_codec': audio_stream.get('codec_name', '') if audio_stream else '',
    }


def _duration(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def extract_audio(video_path: str, output_path: str) -> str:
    """Extract audio as 16kHz mono WAV (ideal for Whisper)."""
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    cmd = [
        'ffmpeg', '-nostdin', '-i', video_path,
        '-map', '0:a:0', '-vn', '-acodec', 'pcm_s16le',
        '-ar', '16000', '-ac', '1',
        '-y', output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def render_portrait_clip(
    source_path: str,
    output_path: str,
    start_sec: float,
    end_sec: float,
    width: int = 1080,
    height: int = 1920,
    caption_words: list = None,
    caption_style: str = 'classic',
    caption_settings: dict = None,
    video_filters: dict = None,
    output_settings: dict = None,
    output_size: dict = None,
) -> str:
    """
    Render H.264/yuv420p with the shared output geometry and burned captions.

    Odd requested dimensions are rounded down to even values. Audio-only inputs
    get a black background. The caller supplies a unique output_path per render.
    """
    start_sec, end_sec = float(start_sec), float(end_sec)
    duration = end_sec - start_sec
    if not math.isfinite(start_sec) or not math.isfinite(end_sec) or start_sec < 0 or duration <= 0:
        raise ValueError(f"Invalid duration: {duration}")
    if (isinstance(width, bool) or isinstance(height, bool)
            or not isinstance(width, int) or not isinstance(height, int)
            or width < 2 or height < 2):
        raise ValueError('Output width and height must be integers of at least 2')
    width, height = width // 2 * 2, height // 2 * 2

    metadata = probe_media(source_path)
    if output_settings is not None:
        from backend.services.render_settings import validate_output_settings, output_dimensions
        output_settings = validate_output_settings(output_settings)
        size = output_size or output_dimensions(output_settings, metadata)
        width, height = _output_size(size)
        if max(width, height) > output_settings['maxDimension']:
            raise ValueError('Output dimensions exceed maxDimension')
    if not metadata['has_video'] and not metadata['has_audio']:
        raise ValueError('Source contains no playable video or audio')
    if metadata['duration_sec']:
        if start_sec >= metadata['duration_sec']:
            raise ValueError('Clip starts beyond the source duration')
        end_sec = min(end_sec, metadata['duration_sec'])
        duration = end_sec - start_sec

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    vf = ','.join(portrait_filters(width, height, caption_words, caption_style,
                                   caption_settings, video_filters, start_sec, end_sec,
                                   fit=output_settings['fit'] if output_settings else 'crop',
                                   normalize_sar=output_settings is not None))

    cmd = [
        'ffmpeg', '-nostdin',
        '-ss', str(start_sec),
        '-i', source_path,
    ]
    if metadata['has_video']:
        # Uppercase V excludes cover art/attached pictures.
        cmd += ['-map', '0:V:0', '-map', '0:a:0?']
    else:
        cmd += ['-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:r=30',
                '-map', '1:v:0', '-map', '0:a:0', '-shortest']
    cmd += [
        '-t', str(duration),
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        '-y', output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b'').decode(errors='replace')[-2000:]
        raise RuntimeError(f'FFmpeg render failed: {detail}') from exc
    return output_path


def portrait_filters(width=1080, height=1920, caption_words=None, caption_style='classic',
                     caption_settings=None, video_filters=None, start_sec=0,
                     end_sec=None, sample_time=None, fit='crop', normalize_sar=False):
    """The same final-look filter graph is used by exports and static previews."""
    from backend.services.render_settings import build_video_filter_chain, validate_caption_settings
    from backend.services.caption_service import build_drawtext_filters, _escape_drawtext

    caption = validate_caption_settings(caption_settings, caption_style)
    width, height = _output_size({'width': width, 'height': height})
    if fit not in ('contain', 'crop'):
        raise ValueError('fit must be contain or crop')
    filters = build_video_filter_chain(video_filters)
    if normalize_sar:
        # Convert non-square pixels before fitting. FFmpeg auto-rotates inputs.
        filters += ["scale=w='max(2,trunc(iw*sar/2)*2)':h=ih", 'setsar=1']
    filters += [
        f'scale={width}:{height}:force_original_aspect_ratio={"decrease" if fit == "contain" else "increase"}:force_divisible_by=2',
        (f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black' if fit == 'contain'
         else f'crop={width}:{height}'), 'setsar=1',
    ]
    captions = build_drawtext_filters(
        caption_words or [], caption_style, start_sec, end_sec, caption,
        width=width, height=height, sample_time=sample_time)
    if captions and os.name == 'nt':
        # Windows static FFmpeg builds may crash in fontconfig even with a
        # config file. An installed fontfile bypasses that lookup entirely.
        font_file = _windows_font_file(caption['fontFamily'])
        if font_file is None:
            raise RuntimeError('No usable Windows caption font found in the system or user Fonts directory')
        # Forward slashes plus the existing two-level option/filter escaping
        # handle drive colons and spaces without changing literal caption %.
        font_option = ':fontfile=' + _escape_drawtext(font_file.replace('\\', '/'))
        captions = [item + font_option for item in captions]
    filters.extend(captions)
    return filters


def _output_size(size):
    width, height = size.get('width'), size.get('height')
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 2 or value > 3840
           for value in (width, height)):
        raise ValueError('Output dimensions must be integers between 2 and 3840')
    return width // 2 * 2, height // 2 * 2


def _windows_font_file(family):
    """Resolve supported families to installed fonts, never a request-supplied path."""
    names = {
        'default': ('arial.ttf', 'segoeui.ttf', 'calibri.ttf'),
        'sans': ('DejaVuSans.ttf', 'arial.ttf', 'segoeui.ttf'),
        'serif': ('DejaVuSerif.ttf', 'times.ttf', 'georgia.ttf'),
        'mono': ('DejaVuSansMono.ttf', 'consola.ttf', 'cour.ttf'),
        'display': ('Ubuntu-Regular.ttf', 'Ubuntu-R.ttf', 'impact.ttf', 'arialbd.ttf'),
    }[family]
    root = os.environ.get('WINDIR') or os.environ.get('SystemRoot') or 'C:\\Windows'
    directories = [os.path.join(root, 'Fonts')]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        directories.append(os.path.join(local, 'Microsoft', 'Windows', 'Fonts'))
    for name in (*names, 'arial.ttf', 'segoeui.ttf', 'calibri.ttf', 'tahoma.ttf'):
        for directory in directories:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                return path
    return None


def render_preview_frame(source_path, output_file, time_sec, caption_words=None,
                         caption_settings=None, video_filters=None, output_settings=None):
    """Compose at export dimensions, then downscale a JPEG without stretching.

    output_file is an owned disk temporary file, so even failed encodes leave no
    artifacts. Preview captions use source-time chunks, with no invented text.
    """
    if isinstance(time_sec, bool) or not isinstance(time_sec, (int, float)):
        raise ValueError('time must be a finite number of seconds')
    if not math.isfinite(time_sec) or time_sec < 0:
        raise ValueError('time must be a finite non-negative number of seconds')
    metadata = probe_media(source_path)
    if metadata['duration_sec'] and time_sec >= metadata['duration_sec']:
        raise ValueError('time must be before the source duration')
    if not metadata['has_video'] and not metadata['has_audio']:
        raise ValueError('Source contains no playable video or audio')
    from backend.services.render_settings import validate_output_settings, output_dimensions, LEGACY_OUTPUT_SETTINGS
    output_settings = validate_output_settings(LEGACY_OUTPUT_SETTINGS if output_settings is None else output_settings)
    size = output_dimensions(output_settings, metadata)
    width, height = size['width'], size['height']
    preview_size = output_dimensions({'mode': 'source', 'maxDimension': 640},
                                     {'has_video': True, 'width': width, 'height': height})
    filters = portrait_filters(width=width, height=height,
        caption_words=caption_words, caption_settings=caption_settings,
        video_filters=video_filters, sample_time=time_sec,
        fit=output_settings['fit'], normalize_sar=True)
    filters.append(f"scale={preview_size['width']}:{preview_size['height']}")
    cmd = ['ffmpeg', '-nostdin', '-v', 'error']
    if metadata['has_video']:
        cmd += ['-ss', str(time_sec), '-i', source_path, '-map', '0:V:0']
    else:
        cmd += ['-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:r=1', '-map', '0:v:0']
    cmd += ['-vf', ','.join(filters), '-frames:v', '1', '-an', '-c:v', 'mjpeg',
            '-q:v', '3', '-f', 'image2pipe', 'pipe:1']
    try:
        subprocess.run(cmd, check=True, stdout=output_file, stderr=subprocess.PIPE, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Preview timed out after 30 seconds') from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError('FFmpeg could not generate the preview frame') from exc
    output_file.seek(0)
    if output_file.read(2) != b'\xff\xd8':
        raise RuntimeError('Preview did not produce a JPEG frame')
    output_file.seek(0)
