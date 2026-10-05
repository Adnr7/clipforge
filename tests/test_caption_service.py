"""Caption regressions include pixel-for-pixel checks using real FFmpeg."""

import shutil
import subprocess

import pytest

from backend.services.caption_service import STYLES, build_drawtext_filters


@pytest.fixture(scope='module')
def ffmpeg():
    executable = shutil.which('ffmpeg')
    if not executable:
        pytest.skip('Real FFmpeg required for caption integration tests')
    filters = subprocess.run([executable, '-hide_banner', '-filters'], capture_output=True, text=True, check=True)
    if 'drawtext' not in filters.stdout:
        pytest.skip('FFmpeg must include drawtext')
    return executable


def frame(ffmpeg, filters):
    result = subprocess.run([
        ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=960x240:r=1',
        '-vf', ','.join(filters), '-frames:v', '1', '-pix_fmt', 'rgb24',
        '-f', 'rawvideo', '-',
    ], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    # Older static fontconfig builds warn about newer system config elements;
    # retain FFmpeg errors (including drawtext's non-fatal "Stray %" error).
    errors = [line for line in result.stderr.decode(errors='replace').splitlines()
              if not line.startswith('Fontconfig warning:')]
    assert not errors, '\n'.join(errors)
    return result.stdout


@pytest.mark.parametrize('text', [
    "It's 100%: real, yes!",
    r"C:\clips\today, 'wow': 50%",
    r"%{pts} [literal]; it's \\ fine: a,b",
    '“Hello”—café: 100%!',
])
@pytest.mark.parametrize('style_name', list(STYLES))
def test_real_ffmpeg_matches_literal_textfile(ffmpeg, tmp_path, text, style_name):
    words = [{'text': text, 'start': 0, 'end': 1}]
    filters = build_drawtext_filters(words, style_name)
    rendered = frame(ffmpeg, filters)

    # An independent textfile reference avoids inline filter escaping entirely.
    textfile = tmp_path / 'caption.txt'
    textfile.write_text(text, encoding='utf-8')
    style = STYLES[style_name]
    reference = frame(ffmpeg, [
        f"drawtext=textfile='{textfile}':expansion=none"
        f":fontsize={style['fontsize']}:fontcolor={style['fontcolor']}"
        f":borderw={style['borderw']}:bordercolor={style['bordercolor'] if style_name != 'minimal' else 'black'}"
        ':x=(w-text_w)/2:y=h-h/4',
    ])
    assert rendered == reference
    assert rendered != frame(ffmpeg, ['null']), 'Caption must actually be visible'


def test_captions_sort_trim_and_split_silences_and_speakers():
    words = [
        {'text': 'later', 'start': 9, 'end': 10, 'speaker': 'Speaker 2'},
        {'text': 'gone', 'start': 0, 'end': 1, 'speaker': 'Speaker 1'},
        {'text': 'first', 'start': 4, 'end': 6, 'speaker': 'Speaker 1'},
        {'text': 'second', 'start': 6, 'end': 7, 'speaker': 'Speaker 2'},
        {'text': 'outside', 'start': 11, 'end': 12},
    ]
    filters = build_drawtext_filters(words, clip_start=5, clip_end=9.5)
    assert len(filters) == 3
    assert 'first' in filters[0] and '0.00' in filters[0]
    assert 'second' in filters[1]
    assert 'later' in filters[2] and '4.50' in filters[2]
    assert 'gone' not in ','.join(filters) and 'outside' not in ','.join(filters)


def test_real_font_families_change_pixels_and_default_is_legacy(ffmpeg):
    words = [{'text': 'Typography', 'start': 0, 'end': 1}]
    default = build_drawtext_filters(words)
    assert ':font=' not in default[0]
    assert frame(ffmpeg, default) == frame(ffmpeg, build_drawtext_filters(words, settings={'fontFamily': 'default'}))
    rendered = []
    for family in ('sans', 'serif', 'mono', 'display'):
        filters = build_drawtext_filters(words, settings={'fontFamily': family})
        assert ':font=' in filters[0]
        rendered.append(frame(ffmpeg, filters))
    assert len(set(rendered[:3])) == 3
    # Display uses a common Ubuntu family; fontconfig may substitute on other OSes.
    assert rendered[3] != frame(ffmpeg, ['null'])


def test_uppercase_disabled_and_custom_position_are_visible(ffmpeg):
    words = [{'text': 'hello', 'start': 0, 'end': 1}]
    assert build_drawtext_filters(words, settings={'enabled': False}) == []
    custom = build_drawtext_filters(words, settings={'uppercase': True, 'verticalPosition': 30})
    assert 'text=HELLO' in custom[0]
    assert frame(ffmpeg, custom) != frame(ffmpeg, build_drawtext_filters(words))
    assert frame(ffmpeg, build_drawtext_filters(words, settings={'verticalPosition': None})) == frame(ffmpeg, build_drawtext_filters(words))


@pytest.mark.parametrize('color', ['red', '#ff000080', 'transparent'])
def test_real_background_opacity_composes_named_and_rgba_colors(ffmpeg, color):
    words = [{'text': 'Caption', 'start': 0, 'end': 1}]
    def rendered(opacity):
        return frame(ffmpeg, build_drawtext_filters(words, settings={
            'backgroundEnabled': True, 'backgroundColor': color, 'backgroundOpacity': opacity,
        }))
    baseline = frame(ffmpeg, build_drawtext_filters(words))
    assert rendered(0) == baseline
    if color == 'transparent':
        assert rendered(1) == baseline
    else:
        assert rendered(0.5) != rendered(1) and rendered(0.5) != baseline


def test_real_eight_word_chunk_wraps_inside_portrait_safe_area(ffmpeg):
    words = [{'text': 'EXTRAORDINARILY', 'start': index / 10, 'end': (index + 1) / 10}
             for index in range(8)]
    filters = build_drawtext_filters(words, settings={
        'wordsPerChunk': 8, 'fontSize': 160, 'fontFamily': 'display',
        'uppercase': True, 'verticalPosition': 85,
        'backgroundEnabled': True, 'backgroundPadding': 32,
    })
    assert len(filters) == 1 and '\n' in filters[0]
    result = subprocess.run([
        ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=1080x1920:r=1',
        '-vf', ','.join(filters), '-frames:v', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', '-',
    ], capture_output=True, check=True, timeout=30)
    pixels = result.stdout
    assert len(pixels) == 1080 * 1920
    occupied = [(i // 1080, i % 1080) for i, value in enumerate(pixels) if value > 32]
    assert occupied
    ys, xs = zip(*occupied)
    assert min(xs) >= 32 and max(xs) < 1048
    assert min(ys) >= 32 and max(ys) < 1888


def test_shared_chunk_boundary_has_one_active_caption_and_silence_has_none(ffmpeg):
    words = [{'text': 'first', 'start': 0, 'end': 1}, {'text': 'second', 'start': 1, 'end': 2}]
    settings = {'chunkMode': 'word', 'maxGapSeconds': 0}
    at_boundary = build_drawtext_filters(words, settings=settings, sample_time=1)
    assert len(at_boundary) == 1 and 'second' in at_boundary[0]
    assert build_drawtext_filters(words, settings=settings, sample_time=2) == []
    timed = build_drawtext_filters(words, settings=settings)
    # Evaluate FFmpeg at t=1; half-open intervals must match the static preview.
    assert frame(ffmpeg, ['setpts=PTS+1/TB', *timed]) == frame(ffmpeg, at_boundary)
    # A short pause within a multiword chunk also has no invented/sample text.
    paused = [{'text': 'first', 'start': 0, 'end': 0.4}, {'text': 'second', 'start': 0.8, 'end': 1}]
    assert build_drawtext_filters(paused, sample_time=0.5) == []
    assert frame(ffmpeg, ['settb=1/100', 'setpts=PTS+0.5/TB', *build_drawtext_filters(paused)]) == frame(ffmpeg, ['null'])
