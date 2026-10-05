"""Output geometry is validated, persisted and shared by previews and exports."""

import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import re
from unittest.mock import Mock

import pytest

from backend.app import create_app
from backend.services import media_service
from backend.services.caption_service import build_drawtext_filters
from backend.services.render_settings import (
    ASPECT_RATIOS, LEGACY_OUTPUT_SETTINGS, output_dimensions,
    resolve_project_settings, validate_output_settings,
)
from tests.test_pipeline_routes import app, client, jobs, services, execute, rows, seed_project
from tests.test_render_workflow import SUGGESTION, suggestion_response


SOURCE = {'mode': 'source', 'aspectRatio': 'source', 'fit': 'contain', 'maxDimension': 1920}
METADATA = {'has_video': True, 'has_audio': True, 'duration_sec': 60,
            'width': 1920, 'height': 1080, 'display_width': 1920, 'display_height': 1080}
URL = '/api/projects/project/render/candidate-project-0'


@pytest.mark.parametrize('ratio', ASPECT_RATIOS)
def test_ratios_and_source_aliases_are_canonical(ratio):
    result = validate_output_settings({'aspectRatio': ratio})
    assert result['aspectRatio'] == ratio
    assert result['fit'] == 'contain'
    assert result['mode'] == ('source' if ratio == 'source' else 'manual')
    assert validate_output_settings({'mode': 'default', 'aspectRatio': 'original'}) == SOURCE
    assert validate_output_settings({'mode': 'ai', 'aspectRatio': ratio})['mode'] == 'ai'


@pytest.mark.parametrize('settings', [
    [], '9:16', {'aspectRatio': None}, {'aspectRatio': '2:1'},
    {'aspectRatio': '1:1,crop=1:1'}, {'mode': 'unsafe'}, {'mode': []},
    {'mode': 'source', 'aspectRatio': '16:9'}, {'mode': 'manual'}, {'mode': 'ai'},
    {'fit': 'stretch'}, {'fit': True}, {'maxDimension': True}, {'maxDimension': 1920.0},
    {'maxDimension': float('nan')}, {'maxDimension': 63}, {'maxDimension': 3841}, {'width': 100},
])
def test_invalid_output_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        validate_output_settings(settings)


@pytest.mark.parametrize('width,height,expected', [
    (1920, 1080, (1920, 1080)), (1080, 1920, (1080, 1920)),
    (3840, 2160, (1920, 1080)), (720, 480, (720, 480)),
    (161, 241, (160, 240)), (8192, 512, (1920, 120)),
    (1, 1, (2, 2)), (101, 10001, (18, 1920)),
])
def test_source_dimensions_are_even_bounded_and_do_not_upscale(width, height, expected):
    result = output_dimensions(SOURCE, {'has_video': True, 'width': width, 'height': height})
    assert (result['width'], result['height']) == expected


@pytest.mark.parametrize('ratio,expected', [
    ('16:9', (1920, 1080)), ('9:16', (1080, 1920)), ('1:1', (1920, 1920)),
    ('4:5', (1536, 1920)), ('4:3', (1920, 1440)), ('3:2', (1920, 1280)),
])
def test_fixed_ratio_dimensions_remain_exact_and_even(ratio, expected):
    size = output_dimensions({'aspectRatio': ratio, 'maxDimension': 1921}, METADATA)
    assert (size['width'], size['height']) == expected
    assert output_dimensions(LEGACY_OUTPUT_SETTINGS) == {'width': 1080, 'height': 1920}
    assert output_dimensions(SOURCE, {'has_video': False}) == {'width': 1080, 'height': 1920}


@pytest.mark.parametrize('metadata', [
    {'has_video': True}, {'has_video': True, 'width': 0, 'height': 1080},
    {'has_video': True, 'width': float('inf'), 'height': 1080},
])
def test_unknown_video_dimensions_do_not_silently_become_portrait(metadata):
    with pytest.raises(ValueError):
        output_dimensions(SOURCE, metadata)


@pytest.mark.parametrize('rotation,expected', [(0, (640, 480)), (-90, (480, 640)), (180, (640, 480))])
def test_probe_uses_display_ratio_for_anamorphic_and_rotated_media(monkeypatch, rotation, expected):
    monkeypatch.setattr(media_service.subprocess, 'run', Mock(return_value=Mock(returncode=0, stdout=json.dumps({
        'streams': [{'codec_type': 'video', 'width': 720, 'height': 480,
                     'sample_aspect_ratio': '8:9', 'side_data_list': [{'rotation': rotation}]}],
        'format': {'duration': '10'},
    }))))
    metadata = media_service.probe_media('source.mp4')
    size = output_dimensions(SOURCE, metadata)
    assert (size['width'], size['height']) == expected


@pytest.mark.parametrize('sar', ['N/A', '0:1', '1:0', '-1:1', None])
def test_probe_invalid_sar_falls_back_to_square_pixels_and_reads_rotation_tag(monkeypatch, sar):
    monkeypatch.setattr(media_service.subprocess, 'run', Mock(return_value=Mock(returncode=0, stdout=json.dumps({
        'streams': [{'codec_type': 'video', 'width': 720, 'height': 480,
                     'sample_aspect_ratio': sar, 'tags': {'rotate': '270'}}],
    }))))
    metadata = media_service.probe_media('source.mp4')
    assert metadata['sample_aspect_ratio'] == 1
    assert output_dimensions(SOURCE, metadata) == {'width': 480, 'height': 720}


def test_reprobe_persists_display_metadata_without_changing_legacy_output(app, client, monkeypatch):
    seed_project(app)
    monkeypatch.setattr('backend.routes.media.probe_media', Mock(return_value=METADATA))
    assert client.post('/api/projects/project/probe').json == METADATA
    project = client.get('/api/projects/project').json['project']
    assert json.loads(project['source_metadata_json']) == METADATA
    assert project['outputSettings'] == LEGACY_OUTPUT_SETTINGS


def test_contract_advertises_source_default_legacy_and_framing(client):
    contract = client.get('/api/render-settings').json
    assert contract['version'] == 3
    assert contract['output']['defaults'] == SOURCE
    assert contract['output']['legacyDefaults'] == LEGACY_OUTPUT_SETTINGS
    assert contract['output']['aspectRatios'] == list(ASPECT_RATIOS)
    assert 'Neither stretches' in contract['output']['fitBehavior']


@pytest.mark.parametrize('import_kind', ['local', 'youtube-path'])
def test_new_local_and_youtube_imports_persist_source_default(app, client, monkeypatch, tmp_path, import_kind):
    monkeypatch.setattr('backend.routes.projects.probe_media', Mock(return_value=METADATA))
    if import_kind == 'local':
        response = client.post('/api/projects/upload', data={'file': (io.BytesIO(b'video'), 'source.mp4')})
    else:
        source = tmp_path / 'downloaded.mp4'
        source.write_bytes(b'video')
        response = client.post('/api/projects', json={'sourcePath': str(source)})
    assert response.status_code == 201, response.json
    project = create_app().test_client().get(f"/api/projects/{response.json['id']}").json['project']
    assert project['outputSettings'] == SOURCE
    assert json.loads(project['render_settings_json']) == {'outputSettings': SOURCE}
    assert json.loads(project['source_metadata_json']) == METADATA


def test_migration_retains_old_project_settings_and_portrait_behavior(app):
    seed_project(app)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('ALTER TABLE projects DROP COLUMN render_settings_json')
        db.execute('ALTER TABLE projects DROP COLUMN source_metadata_json')
        db.execute('ALTER TABLE filter_suggestions DROP COLUMN output_settings_json')
    restarted = create_app().test_client()
    project = restarted.get('/api/projects/project').json['project']
    assert project['outputSettings'] == LEGACY_OUTPUT_SETTINGS
    assert project['caption_style'] == 'classic' and project['source_duration'] == 60
    assert json.loads(project['render_settings_json'])['outputSettings'] == LEGACY_OUTPUT_SETTINGS
    assert create_app().test_client().get('/api/projects/project').json['project']['outputSettings'] == LEGACY_OUTPUT_SETTINGS


def test_output_settings_persist_and_feed_preview_and_render_snapshot(app, client, jobs, services, monkeypatch):
    seed_project(app)
    services.probe.return_value = METADATA
    saved = client.put('/api/projects/project/render-settings', json={'outputSettings': SOURCE})
    assert saved.status_code == 200
    assert client.get('/api/projects/project/render-settings').json == saved.json
    assert client.put('/api/projects/project/render-settings', json={
        'captionSettings': {'enabled': False}, 'videoFilters': {'contrast': 1.1},
    }).json['outputSettings'] == SOURCE
    restarted = create_app().test_client()
    assert restarted.get('/api/projects/project').json['project']['outputSettings'] == SOURCE
    preview = Mock(side_effect=lambda source, output, time, **kwargs: output.write(b'jpeg'))
    monkeypatch.setattr(media_service, 'render_preview_frame', preview)
    frame = restarted.post('/api/projects/project/preview-frame', json={'time': 2})
    assert frame.status_code == 200
    frame.close()
    response = restarted.post(URL)
    assert response.status_code == 202
    # Saving another ratio before dispatch cannot mutate the queued render.
    client.put('/api/projects/project/render-settings', json={'outputSettings': {'aspectRatio': '1:1'}})
    jobs.run_all()
    assert preview.call_args.kwargs['output_settings'] == SOURCE
    assert services.render.call_args.kwargs['output_settings'] == SOURCE
    assert services.render.call_args.kwargs['output_size'] == {'width': 1920, 'height': 1080}
    identity = json.loads(rows(app, 'SELECT render_settings_json FROM clips')[0]['render_settings_json'])
    assert identity['outputSettings'] == SOURCE and identity['outputDimensions'] == {'width': 1920, 'height': 1080}


@pytest.mark.parametrize('body', [
    {'outputSettings': None}, {'outputSettings': {'fit': 'stretch'}},
    {'outputSettings': {'mode': 'ai'}}, {'outputSettings': {'maxDimension': 100000}},
])
@pytest.mark.parametrize('endpoint', ['render-settings', 'preview-frame', 'render/candidate-project-0'])
def test_output_validation_is_atomic_at_each_boundary(app, client, jobs, monkeypatch, body, endpoint):
    seed_project(app)
    before = rows(app, 'SELECT * FROM projects')
    preview = Mock()
    monkeypatch.setattr(media_service, 'render_preview_frame', preview)
    method = client.put if endpoint == 'render-settings' else client.post
    response = method(f'/api/projects/project/{endpoint}', json=body)
    assert response.status_code == 400
    assert rows(app, 'SELECT * FROM projects') == before
    assert rows(app, 'SELECT * FROM clips') == [] and jobs.tasks == []
    preview.assert_not_called()


def test_nested_output_settings_inherit_but_duplicates_fail():
    project = {'render_settings_json': json.dumps({'outputSettings': SOURCE})}
    assert resolve_project_settings(project)['outputSettings'] == SOURCE
    assert resolve_project_settings(project, {'renderSettings': {'outputSettings': {'aspectRatio': '4:5'}}})['outputSettings']['aspectRatio'] == '4:5'
    with pytest.raises(ValueError, match='once'):
        resolve_project_settings(project, {'outputSettings': {}, 'renderSettings': {'outputSettings': {}}})


@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('change', [
    {'aspectRatio': '16:9'}, {'aspectRatio': '1:1'},
    {**LEGACY_OUTPUT_SETTINGS, 'fit': 'contain'}, {**LEGACY_OUTPUT_SETTINGS, 'maxDimension': 1280},
])
def test_changed_ratio_fit_or_dimensions_never_reuse_wrong_artifact(app, client, jobs, services, complete, change):
    seed_project(app)
    first = client.post(URL, json={'captionSettings': {'enabled': False}})
    if complete:
        jobs.run_all()
    second = client.post(URL, json={'captionSettings': {'enabled': False}, 'outputSettings': change})
    assert second.status_code == 202 and second.json['clipId'] != first.json['clipId']
    again = client.post(URL, json={'captionSettings': {'enabled': False}, 'outputSettings': change})
    assert again.json['clipId'] == second.json['clipId']
    jobs.run_all()
    assert services.render.call_count == 2


def test_source_metadata_dimensions_and_caption_layout_affect_identity(app, client, jobs, services):
    seed_project(app)
    services.probe.return_value = METADATA
    body = {'outputSettings': SOURCE, 'captionSettings': {'fontSize': 80, 'fontFamily': 'mono'}}
    execute(app, 'UPDATE transcripts SET raw_json=?', (json.dumps({'words': [
        {'text': 'A long caption ' * 30, 'start': 1, 'end': 4},
    ]}),))
    first = client.post(URL, json=body)
    jobs.run_all()
    services.probe.return_value = {**METADATA, 'display_width': 720, 'display_height': 480}
    second = client.post(URL, json=body)
    assert second.status_code == 202 and second.json['clipId'] != first.json['clipId']
    identities = [json.loads(row['render_settings_json']) for row in rows(app, 'SELECT render_settings_json FROM clips')]
    assert identities[0]['captionContentDigest'] != identities[1]['captionContentDigest']
    assert identities[1]['outputDimensions'] == {'width': 720, 'height': 480}


@pytest.mark.parametrize('complete', [False, True])
def test_height_only_change_updates_caption_layout_digest_and_export(app, client, jobs, services, complete):
    seed_project(app)
    services.probe.return_value = {**METADATA, 'display_width': 720, 'display_height': 1080}
    words = [{'text': 'A long caption ' * 8, 'start': 1, 'end': 4}]
    execute(app, 'UPDATE transcripts SET raw_json=?', (json.dumps({'words': words}),))
    body = {'outputSettings': SOURCE, 'captionSettings': {'fontSize': 80, 'fontFamily': 'mono'}}
    first = client.post(URL, json=body)
    if complete:
        jobs.run_all()
    services.probe.return_value = {**METADATA, 'display_width': 720, 'display_height': 180}
    second = client.post(URL, json=body)
    assert second.status_code == 202 and second.json['clipId'] != first.json['clipId']
    identities = [json.loads(row['render_settings_json']) for row in rows(app, 'SELECT render_settings_json FROM clips')]
    assert identities[0]['captionContentDigest'] != identities[1]['captionContentDigest']
    tall = build_drawtext_filters(words, settings=body['captionSettings'], width=720, height=1080)[0]
    short = build_drawtext_filters(words, settings=body['captionSettings'], width=720, height=180)[0]
    assert int(re.search(r':fontsize=(\d+)', short)[1]) < int(re.search(r':fontsize=(\d+)', tall)[1])
    jobs.run_all()
    assert services.render.call_args.kwargs['output_size'] == {'width': 720, 'height': 180}


@pytest.mark.parametrize('changed', [
    {'captionSettings': {'preset': 'neon'}},
    {'videoFilters': {'brightness': .2}},
])
def test_preview_and_pending_export_never_coalesce_different_looks(app, client, jobs, services, monkeypatch, changed):
    seed_project(app)
    services.probe.return_value = METADATA
    first = client.post(URL, json={'outputSettings': SOURCE})
    preview = Mock(side_effect=lambda source, output, time, **kwargs: output.write(b'jpeg'))
    monkeypatch.setattr(media_service, 'render_preview_frame', preview)
    body = {'outputSettings': SOURCE, **changed}
    frame = client.post('/api/projects/project/preview-frame', json={'time': 2, **body})
    assert frame.status_code == 200
    frame.close()
    second = client.post(URL, json=body)
    assert second.status_code == 202 and second.json['clipId'] != first.json['clipId']
    assert client.post(URL, json=body).json['clipId'] == second.json['clipId']
    jobs.run_all()
    for key in ('caption_settings', 'video_filters', 'output_settings'):
        assert services.render.call_args.kwargs[key] == preview.call_args.kwargs[key]
    assert services.render.call_count == 2


def test_ai_suggested_ratio_is_validated_review_only_and_persisted(app, client, monkeypatch):
    seed_project(app)
    execute(app, 'UPDATE projects SET source_metadata_json=?', (json.dumps(METADATA),))
    result = {**SUGGESTION, 'outputSettings': {'mode': 'ai', 'aspectRatio': '4:5', 'fit': 'contain'}}
    post = Mock(return_value=suggestion_response(result))
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    before = rows(app, 'SELECT * FROM projects')
    response = client.post('/api/projects/project/filter-suggestions/generate', json={'brief': 'Instagram feed'})
    assert response.status_code == 201, response.json
    assert response.json['outputSettings'] == {'mode': 'ai', 'aspectRatio': '4:5', 'fit': 'contain', 'maxDimension': 1920}
    assert response.json['applied'] is False
    context = json.loads(post.call_args.kwargs['json']['messages'][0]['content'].split('\nContext: ')[1])
    assert context['sourceMetadata'] == METADATA
    assert rows(app, 'SELECT * FROM projects') == before
    assert create_app().test_client().get('/api/projects/project/filter-suggestions').json[0] == response.json


@pytest.mark.parametrize('output', [None, {}, {'aspectRatio': 'unsafe'}, {'aspectRatio': '16:9', 'fit': 'stretch'}])
def test_invalid_ai_ratios_are_not_stored(app, client, monkeypatch, output):
    seed_project(app)
    post = Mock(return_value=suggestion_response({**SUGGESTION, 'outputSettings': output}))
    monkeypatch.setattr('backend.services.llm_service.requests.post', post)
    assert client.post('/api/projects/project/filter-suggestions/generate', json={}).status_code == 502
    assert rows(app, 'SELECT * FROM filter_suggestions') == []


@pytest.mark.parametrize('fit,fragment', [('contain', 'pad=160:160'), ('crop', 'crop=160:160')])
def test_real_framing_preserves_image_geometry_and_letterboxes_only_fit(fit, fragment):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    filters = media_service.portrait_filters(width=160, height=160, fit=fit, normalize_sar=True)
    assert any(fragment in item for item in filters)
    process = subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=red:s=320x180:r=1,drawbox=x=140:y=70:w=40:h=40:color=blue:t=fill',
        '-vf', ','.join(filters), '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
    ], check=True, capture_output=True, timeout=30)
    image = process.stdout
    assert len(image) == 160 * 160 * 3
    def pixel(x, y):
        offset = (y * 160 + x) * 3
        return tuple(image[offset:offset + 3])
    assert pixel(80, 80)[2] > 200  # Blue square stays centered.
    if fit == 'contain':
        assert max(pixel(80, 10)) < 10 and pixel(80, 40)[0] > 200
    else:
        assert pixel(80, 10)[0] > 200
    # The original square remains square, rather than being stretched vertically.
    blue_horizontal = sum(pixel(x, 80)[2] > 200 for x in range(160))
    blue_vertical = sum(pixel(80, y)[2] > 200 for y in range(160))
    assert abs(blue_horizontal - blue_vertical) <= 2


@pytest.mark.parametrize('settings,expected', [
    ({'aspectRatio': 'source', 'maxDimension': 640}, (320, 180)),
    ({'aspectRatio': '1:1', 'fit': 'contain', 'maxDimension': 160}, (160, 160)),
    ({'aspectRatio': '4:5', 'fit': 'crop', 'maxDimension': 160}, (128, 160)),
])
def test_real_preview_and_export_use_same_geometry_and_filter_chain(tmp_path, monkeypatch, settings, expected):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and FFprobe required')
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=5:d=1',
                    '-y', str(source)], check=True, capture_output=True, timeout=30)
    calls = []
    original = media_service.portrait_filters
    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(list(result))
        return result
    monkeypatch.setattr(media_service, 'portrait_filters', capture)
    output = tmp_path / 'clip.mp4'
    media_service.render_portrait_clip(str(source), str(output), 0, .8,
                                      output_settings=settings, caption_settings={'enabled': False})
    with tempfile.TemporaryFile(mode='w+b') as file:
        media_service.render_preview_frame(str(source), file, .2,
                                           output_settings=settings, caption_settings={'enabled': False})
        preview = tmp_path / 'preview.jpg'
        preview.write_bytes(file.read())
    for path in (output, preview):
        metadata = media_service.probe_media(str(path))
        assert (metadata['width'], metadata['height']) == expected
    assert calls[0] == calls[1]
    assert media_service.probe_media(str(output))['sample_aspect_ratio'] == 1


@pytest.mark.parametrize('height', [180, 320])
def test_real_wrapped_captions_stay_inside_short_source_canvases(height):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    width = 720
    filters = media_service.portrait_filters(
        width=width, height=height,
        caption_words=[{'text': 'Readable long caption ' * 8, 'start': 0, 'end': 1}],
        caption_settings={'fontSize': 100, 'fontFamily': 'mono', 'verticalPosition': 85,
                          'backgroundEnabled': True, 'backgroundPadding': 12})
    result = subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        f'color=c=black:s={width}x{height}:r=1', '-vf', ','.join(filters),
        '-frames:v', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', '-',
    ], check=True, capture_output=True, timeout=30)
    occupied = [(i // width, i % width) for i, value in enumerate(result.stdout) if value > 32]
    assert occupied
    ys, xs = zip(*occupied)
    assert min(xs) >= 12 and max(xs) < width - 12
    assert min(ys) >= 12 and max(ys) < height - 12


def test_captioned_preview_and_export_compose_at_the_same_width_and_height(tmp_path, monkeypatch):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and FFprobe required')
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=navy:s=720x180:r=5:d=1',
                    '-y', str(source)], check=True, capture_output=True, timeout=30)
    words = [{'text': 'Caption with wrapping ' * 6, 'start': .1, 'end': .7}]
    caption = {'fontSize': 80, 'fontFamily': 'mono', 'verticalPosition': 85}
    graphs = []
    original = media_service.portrait_filters
    def capture(*args, **kwargs):
        graph = original(*args, **kwargs)
        graphs.append([re.sub(r":enable='[^']*'", '', item) for item in graph])
        return graph
    monkeypatch.setattr(media_service, 'portrait_filters', capture)
    output = tmp_path / 'clip.mp4'
    media_service.render_portrait_clip(str(source), str(output), 0, .8, caption_words=words,
                                      caption_settings=caption, output_settings=SOURCE)
    with tempfile.TemporaryFile(mode='w+b') as file:
        media_service.render_preview_frame(str(source), file, .2, caption_words=words,
                                           caption_settings=caption, output_settings=SOURCE)
    assert graphs[0] == graphs[1]
    assert '\n' in graphs[0][-1]
    assert media_service.probe_media(str(output))['height'] == 180


@pytest.mark.parametrize('rotation', [False, True])
def test_real_source_render_preserves_anamorphic_display_ratio_after_rotation(tmp_path, rotation):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and FFprobe required')
    source = tmp_path / 'anamorphic.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=180x120:r=5:d=1',
                    '-vf', 'setsar=8/9', '-y', str(source)], check=True, capture_output=True, timeout=30)
    if rotation:
        rotated = tmp_path / 'rotated.mp4'
        # FFmpeg 7+ writes display matrices via the input override; older builds
        # accept the rotate metadata tag. Verify the fixture is actually rotated.
        result = subprocess.run(['ffmpeg', '-v', 'error', '-display_rotation:v:0', '90',
                                 '-i', str(source), '-c', 'copy', '-y', str(rotated)],
                                capture_output=True, timeout=30)
        if result.returncode:
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-c', 'copy',
                            '-metadata:s:v:0', 'rotate=90', '-y', str(rotated)],
                           check=True, capture_output=True, timeout=30)
        source = rotated
        assert abs(media_service.probe_media(str(source))['rotation']) == 90
    output = tmp_path / 'clip.mp4'
    media_service.render_portrait_clip(str(source), str(output), 0, .8, output_settings=SOURCE,
                                      caption_settings={'enabled': False})
    metadata = media_service.probe_media(str(output))
    assert (metadata['width'], metadata['height']) == ((120, 160) if rotation else (160, 120))
    assert metadata['sample_aspect_ratio'] == 1
