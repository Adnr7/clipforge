"""Export reuse follows effective captions, not the transcript's ID or metadata."""

import copy
import json
import os
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from backend.app import create_app
from backend.services import media_service
from tests.test_pipeline_routes import app, client, jobs, services, execute, rows, seed_project


SINGLE_URL = '/api/projects/project/render/candidate-project-0'
BATCH_URL = '/api/projects/project/render-batch'
WORDS = [
    {'text': 'Hello', 'start': 1.1, 'end': 1.5, 'speaker': 'Speaker 1'},
    {'text': 'world', 'start': 1.6, 'end': 2.5, 'speaker': 'Speaker 1'},
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    post = Mock(side_effect=AssertionError('Render identity tests must not make network calls'))
    monkeypatch.setattr('requests.post', post)
    yield
    post.assert_not_called()


def set_words(app, words, **metadata):
    execute(app, 'UPDATE transcripts SET raw_json=? WHERE project_id=?',
            (json.dumps({'words': words, 'segments': [], **metadata}), 'project'))


@pytest.fixture
def project(app):
    seed_project(app, status='analyzed')
    execute(app, 'UPDATE candidates SET start_sec=1, end_sec=3 WHERE project_id=?', ('project',))
    set_words(app, WORDS)


def clip_id(response):
    return response.json.get('clipId') or response.json['clipIds'][0]


def identity(app, clip):
    return json.loads(rows(app, 'SELECT render_settings_json FROM clips WHERE id=?',
                           (clip,))[0]['render_settings_json'])


@pytest.mark.parametrize('url', [SINGLE_URL, BATCH_URL])
def test_same_effective_transcript_reuses_across_transcript_ids_and_restart(
        app, client, jobs, services, project, url):
    first = client.post(url)
    assert first.status_code == 202
    first_id = clip_id(first)
    jobs.run_all()
    saved = identity(app, first_id)
    assert len(saved['captionContentDigest']) == 64
    # A provider rerun can assign a new transcript ID/engine and alter unrelated
    # metadata or segments while producing exactly the same caption words.
    execute(app, '''INSERT INTO transcripts (id, project_id, engine, raw_json, created_at)
                   VALUES (?,?,?,?,?)''',
            ('new-transcript', 'project', 'another-provider', json.dumps({
                'segments': [{'text': 'Unrelated analysis text'}],
                'language': 'fr', 'duration': 999, 'words': WORDS,
            }), '2027-01-01'))
    retry = create_app().test_client().post(url)
    assert retry.status_code == 200
    assert clip_id(retry) == first_id
    assert services.render.call_count == 1
    assert len(rows(app, 'SELECT * FROM clips')) == 1
    assert jobs.tasks == []


@pytest.mark.parametrize('change', [
    {'text': "It's 100%: changed"}, {'start': 1.2}, {'end': 1.55}, {'speaker': 'Speaker 2'},
])
@pytest.mark.parametrize('url', [SINGLE_URL, BATCH_URL])
def test_changed_caption_text_timing_or_chunk_boundary_renders_new_clip(
        app, client, jobs, services, project, change, url):
    first = client.post(url)
    jobs.run_all()
    first_id = clip_id(first)
    changed = copy.deepcopy(WORDS)
    changed[0].update(change)
    set_words(app, changed)
    second = client.post(url)
    assert second.status_code == 202
    second_id = clip_id(second)
    assert second_id != first_id
    assert identity(app, first_id)['captionContentDigest'] != identity(app, second_id)['captionContentDigest']
    jobs.run_all()
    assert services.render.call_count == 2
    assert services.render.call_args_list[0].kwargs['caption_words'] == WORDS
    assert services.render.call_args_list[1].kwargs['caption_words'] == changed
    assert [row['status'] for row in rows(app, 'SELECT status FROM clips')] == ['done', 'done']
    retry = client.post(url)
    assert retry.status_code == 200 and clip_id(retry) == second_id
    assert services.render.call_count == 2


def test_only_effective_words_inside_the_cut_affect_identity(app, client, jobs, services, project):
    original = [
        {'text': 'crosses start', 'start': 0, 'end': 1.5},
        {'text': 'crosses end', 'start': 2, 'end': 4},
        {'text': 'outside', 'start': 4, 'end': 5},
    ]
    set_words(app, original)
    first = client.post(SINGLE_URL)
    jobs.run_all()
    changed = [
        {'text': '  crosses end  ', 'start': 2, 'end': 6, 'confidence': 0.3},
        {'text': 'crosses start', 'start': 0.5, 'end': 1.5},
        {'text': 'a totally different out-of-cut word', 'start': 7, 'end': 8},
        {'text': 'before boundary', 'start': 0, 'end': 1},
        {'text': 'after boundary', 'start': 3, 'end': 3.5},
        {'text': 'invalid time', 'start': float('nan'), 'end': 2},
        None,
    ]
    set_words(app, changed, language='es', duration=60)
    retry = client.post(SINGLE_URL)
    assert retry.status_code == 200
    assert clip_id(retry) == clip_id(first)
    assert services.render.call_count == 1


def test_batch_digest_is_candidate_specific(app, client, jobs, services):
    seed_project(app, count=2, status='analyzed')
    execute(app, 'UPDATE candidates SET start_sec=0, end_sec=2 WHERE id=?', ('candidate-project-0',))
    execute(app, 'UPDATE candidates SET start_sec=10, end_sec=12 WHERE id=?', ('candidate-project-1',))
    words = [{'text': 'First cut', 'start': 0.2, 'end': 1},
             {'text': 'Second cut', 'start': 10.2, 'end': 11}]
    set_words(app, words)
    first = client.post(BATCH_URL)
    jobs.run_all()
    words[1]['text'] = 'Changed second cut'
    set_words(app, words)
    second = client.post(BATCH_URL)
    assert second.status_code == 202
    assert second.json['clipIds'][0] == first.json['clipIds'][0]
    assert second.json['clipIds'][1] != first.json['clipIds'][1]
    assert len(jobs.tasks) == 1
    jobs.run_all()
    assert services.render.call_count == 3


@pytest.mark.parametrize('change', [{'text': 'changed'}, {'start': 1.2}])
def test_active_jobs_reuse_only_the_same_effective_caption_snapshot(
        app, client, jobs, services, project, change):
    first = client.post(SINGLE_URL)
    same = client.post(SINGLE_URL)
    assert same.status_code == 202 and clip_id(same) == clip_id(first)
    assert len(jobs.tasks) == 1
    changed = copy.deepcopy(WORDS)
    changed[0].update(change)
    set_words(app, changed)
    second = client.post(SINGLE_URL)
    assert second.status_code == 202 and clip_id(second) != clip_id(first)
    assert len(jobs.tasks) == 2
    jobs.run_all()
    assert services.render.call_count == 2
    assert services.render.call_args_list[0].kwargs['caption_words'] == WORDS
    assert services.render.call_args_list[1].kwargs['caption_words'] == changed


@pytest.mark.parametrize('complete_first', [False, True])
@pytest.mark.parametrize('raw_json', ['{', 'null', '{"words": "invalid"}', '{"words": []}'])
def test_captions_disabled_ignore_all_transcript_changes(
        app, client, jobs, services, project, complete_first, raw_json):
    body = {'captionSettings': {'enabled': False}}
    first = client.post(SINGLE_URL, json=body)
    assert first.status_code == 202
    assert 'captionContentDigest' not in identity(app, clip_id(first))
    if complete_first:
        jobs.run_all()
    execute(app, 'UPDATE transcripts SET raw_json=?', (raw_json,))
    retry = client.post(BATCH_URL, json=body)
    assert retry.status_code == (200 if complete_first else 202)
    assert clip_id(retry) == clip_id(first)
    jobs.run_all()
    assert services.render.call_count == 1
    assert services.render.call_args.kwargs['caption_words'] is None


@pytest.mark.parametrize('legacy_kind', ['settings-only', 'no-settings', 'invalid-digest', 'null-settings'])
@pytest.mark.parametrize('has_words', [True, False])
def test_unknown_legacy_caption_content_cannot_be_reused(
        app, client, jobs, services, project, legacy_kind, has_words):
    if not has_words:
        set_words(app, [])
    first = client.post(SINGLE_URL)
    jobs.run_all()
    first_id = clip_id(first)
    saved = identity(app, first_id)
    if legacy_kind == 'no-settings':
        serialized = None
    elif legacy_kind == 'null-settings':
        serialized = json.dumps({'caption': None, 'videoFilters': None, 'captionContentDigest': '0' * 64})
    else:
        saved.pop('captionContentDigest')
        if legacy_kind == 'invalid-digest':
            saved['captionContentDigest'] = 123
        serialized = json.dumps(saved)
    execute(app, 'UPDATE clips SET render_settings_json=? WHERE id=?', (serialized, first_id))
    retry = client.post(SINGLE_URL)
    assert retry.status_code == 202
    assert clip_id(retry) != first_id
    jobs.run_all()
    assert services.render.call_count == 2
    assert rows(app, 'SELECT status FROM clips WHERE id=?', (first_id,)) == [{'status': 'done'}]


def test_legacy_caption_disabled_settings_are_safely_reusable(app, client, jobs, services, project):
    body = {'captionSettings': {'enabled': False}}
    first = client.post(SINGLE_URL, json=body)
    jobs.run_all()
    saved = identity(app, clip_id(first))
    assert set(saved) == {'caption', 'videoFilters', 'outputSettings', 'outputDimensions'}
    # A genuinely pre-aspect-ratio identity still means 1080x1920 center crop.
    saved.pop('outputSettings')
    saved.pop('outputDimensions')
    execute(app, 'UPDATE clips SET render_settings_json=? WHERE id=?',
            (json.dumps(saved, indent=2), clip_id(first)))
    set_words(app, [{'text': 'completely different', 'start': 1, 'end': 2}])
    retry = client.post(SINGLE_URL, json=body)
    assert retry.status_code == 200 and clip_id(retry) == clip_id(first)
    assert services.render.call_count == 1


def test_returned_clip_is_the_matching_export_even_when_it_is_not_the_newest(
        app, client, jobs, services, project):
    original = client.post(SINGLE_URL)
    jobs.run_all()
    set_words(app, [{**WORDS[0], 'text': 'a newer export'}])
    newer = client.post(SINGLE_URL)
    jobs.run_all()
    assert clip_id(newer) != clip_id(original)
    set_words(app, WORDS)
    matching = client.post(SINGLE_URL)
    assert matching.status_code == 200 and clip_id(matching) == clip_id(original)
    assert services.render.call_count == 2
    assert len(rows(app, 'SELECT * FROM clips')) == 2


@pytest.mark.parametrize('family,filename', [
    ('default', 'arial.ttf'), ('sans', 'DejaVuSans.ttf'), ('serif', 'DejaVuSerif.ttf'),
    ('mono', 'DejaVuSansMono.ttf'), ('display', 'Ubuntu-R.ttf'),
])
def test_windows_font_resolution_prefers_family_in_user_fonts_over_system_fallback(
        tmp_path, monkeypatch, family, filename):
    system = tmp_path / 'Windows'
    system_fonts = system / 'Fonts'
    system_fonts.mkdir(parents=True)
    (system_fonts / 'tahoma.ttf').write_bytes(b'fallback')
    local = tmp_path / 'Local App Data'
    user_fonts = local / 'Microsoft' / 'Windows' / 'Fonts'
    user_fonts.mkdir(parents=True)
    preferred = user_fonts / filename
    preferred.write_bytes(b'preferred')
    monkeypatch.setenv('WINDIR', str(system))
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    assert media_service._windows_font_file(family) == str(preferred)


def test_windows_font_resolution_falls_back_and_reports_no_installed_font(tmp_path, monkeypatch):
    system = tmp_path / 'Custom Windows'
    fonts = system / 'Fonts'
    fonts.mkdir(parents=True)
    fallback = fonts / 'arial.ttf'
    fallback.write_bytes(b'fallback')
    monkeypatch.delenv('WINDIR', raising=False)
    monkeypatch.setenv('SystemRoot', str(system))
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    assert media_service._windows_font_file('serif') == str(fallback)
    fallback.unlink()
    assert media_service._windows_font_file('default') is None


def test_caption_free_filters_need_no_font_lookup(monkeypatch):
    font = Mock(side_effect=AssertionError('No font is needed without captions'))
    monkeypatch.setattr(media_service, '_windows_font_file', font)
    assert not any('drawtext' in item for item in media_service.portrait_filters(caption_words=[]))
    assert not any('drawtext' in item for item in media_service.portrait_filters(
        caption_words=WORDS, caption_settings={'enabled': False}))
    font.assert_not_called()


def real_caption_frame(filters, directory):
    if not shutil.which('ffmpeg'):
        pytest.skip('Real FFmpeg required')
    result = subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=black:s=960x240:r=1', '-vf', ','.join(filters), '-frames:v', '1',
        '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
    ], capture_output=True, timeout=30, cwd=directory)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    errors = [line for line in result.stderr.decode(errors='replace').splitlines()
              if not line.startswith('Fontconfig warning:')]
    assert not errors, '\n'.join(errors)
    assert len(result.stdout) == 960 * 240 * 3
    return result.stdout


@pytest.mark.parametrize('text', [
    "It's 100%: real, yes!", r"C:\clips\today, 'wow': 50%",
    r"%{pts} [literal]; it's \\ fine: a,b", '“Hello”—café: 100%!',
])
def test_real_export_filters_preserve_literal_percent_with_explicit_windows_font(tmp_path, text):
    filters = media_service.portrait_filters(
        width=960, height=240, caption_words=[{'text': text, 'start': 0, 'end': 1}])
    actual = real_caption_frame(filters, tmp_path)
    caption = filters[-1]
    assert ':expansion=none' in caption
    if os.name == 'nt':
        assert ':fontfile=' in caption
    # A relative textfile avoids both inline text escaping and Windows drive
    # colon parsing. Keep typography/timing/font identical for pixel comparison.
    (tmp_path / 'reference.txt').write_text(text, encoding='utf-8')
    suffix = caption.split(':expansion=none', 1)[1]
    reference = [*filters[:-1], 'drawtext=textfile=reference.txt:expansion=none' + suffix]
    assert actual == real_caption_frame(reference, tmp_path)
    assert actual != real_caption_frame(filters[:-1], tmp_path)


@pytest.mark.parametrize('family', ['default', 'sans', 'serif', 'mono', 'display'])
def test_real_supported_caption_font_families_render_without_fontconfig_crashes(tmp_path, family):
    filters = media_service.portrait_filters(
        width=960, height=240,
        caption_words=[{'text': 'Font 100%', 'start': 0, 'end': 1}],
        caption_settings={'fontFamily': family})
    if os.name == 'nt':
        assert ':fontfile=' in filters[-1]
    assert real_caption_frame(filters, tmp_path) != real_caption_frame(filters[:-1], tmp_path)
