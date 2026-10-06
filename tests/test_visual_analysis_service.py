"""Provider/frame fixtures exercise the public API without paid model calls."""

import base64
import copy
import json
import shutil
import subprocess
from unittest.mock import Mock

import pytest
import requests

from backend.services import llm_service, visual_analysis_service as visual


JPEG = b'\xff\xd8sampled-frame-fixture\xff\xd9'
METADATA = {'duration_sec': 120.0, 'width': 1920, 'height': 1080,
            'has_video': True, 'has_audio': True, 'video_codec': 'h264', 'audio_codec': 'aac'}
PROFILE = {'id': 'private-profile-id', 'kind': 'llm', 'provider': 'custom',
           'base_url': 'https://vision.example.test/v1', 'model': 'connected-analysis-model',
           'api_key': 'fixture-credential-do-not-expose'}
CLAUDE_PROFILE = {**PROFILE, 'provider': 'claude',
                  'base_url': 'https://anthropic.example.test/v1/messages',
                  'model': 'connected-claude-vision-model'}
RECOMMENDATION = {
    'audience': 'People learning visual storytelling',
    'aspectRatio': {'mode': 'pad', 'ratio': '9:16'},
    'videoFilters': {'brightness': 0.05, 'contrast': 1.1, 'saturation': 1, 'blur': 0, 'sharpen': 0.2},
    'captionSettings': {'enabled': False, 'preset': 'minimal', 'placement': 'bottom'},
    'candidates': [{'start': 0, 'end': 25, 'hook': 'A promising visual introduction',
                    'rationale': 'The opening samples show a subject; boundaries need review.',
                    'topic': 'Visual introduction', 'audienceReason': 'The visible composition demonstrates a clear subject.',
                    'assessment': {'audienceFit': 4, 'hook': 4, 'payoff': 4, 'clarity': 4, 'shareability': 3},
                    'evidenceFrame': 0, 'evidenceDescription': 'A subject is visible in the opening composition.'}],
    'rationale': 'Padding preserves the visible composition in the sampled frames.',
}
TIMES = [index * 119.5 / 9 for index in range(10)]


def http_response(payload, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


def completion(result=RECOMMENDATION):
    return http_response({'choices': [{'message': {'content': json.dumps(result)}}]})


def anthropic_completion(result=RECOMMENDATION):
    text = json.dumps(result)
    # Native responses can contain several text blocks; validate the combined JSON.
    return http_response({'content': [{'type': 'text', 'text': text[:20]},
                                     {'type': 'text', 'text': text[20:]}]})


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name in ('LLM_PROVIDER', 'OLLAMA_MODEL', 'CUSTOM_MODEL', 'CUSTOM_BASE_URL', 'CUSTOM_API_KEY',
                 *(config['env_key'] for config in llm_service.PROVIDERS.values() if config['env_key'])):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / 'private source video.mp4'
    source.write_bytes(b'full-video-must-not-be-sent')
    probe = Mock(return_value=copy.deepcopy(METADATA))
    monkeypatch.setattr(visual.media_service, 'probe_media', probe)
    outputs = []

    def extract(command, *, stdout, **options):
        outputs.append(stdout)
        stdout.write(JPEG)
        return subprocess.CompletedProcess(command, 0)

    ffmpeg = Mock(side_effect=extract)
    post = Mock(return_value=completion())
    monkeypatch.setattr(visual.subprocess, 'run', ffmpeg)
    monkeypatch.setattr(llm_service.requests, 'post', post)
    return {'source': source, 'probe': probe, 'ffmpeg': ffmpeg, 'post': post, 'outputs': outputs}


def analyze(pipeline, **options):
    options.setdefault('profile', PROFILE)
    return visual.analyze_video(pipeline['source'], **options)


def test_visual_model_receives_full_length_editing_brief_without_truncation(pipeline):
    brief = 'a' * 4980 + ' FINAL PREFERENCE!!!'
    analyze(pipeline, brief=brief)
    content = pipeline['post'].call_args.kwargs['json']['messages'][0]['content']
    context = json.loads(content[0]['text'].split('\nContext: ')[1])
    assert context['brief'] == brief


@pytest.mark.parametrize('has_audio', [True, False])
def test_public_api_sends_sampled_images_metadata_and_same_connected_model_without_transcript(
        pipeline, has_audio):
    pipeline['probe'].return_value['has_audio'] = has_audio
    pipeline['probe'].return_value['audio_codec'] = 'aac' if has_audio else ''
    result = analyze(pipeline, brief='Preserve both subjects in frame')
    request = pipeline['post'].call_args
    assert request.args == ('https://vision.example.test/v1/chat/completions',)
    assert request.kwargs['headers']['Authorization'] == f"Bearer {PROFILE['api_key']}"
    body = request.kwargs['json']
    assert body['model'] == PROFILE['model']
    assert request.kwargs['timeout'] == (5, 60)
    assert body['max_tokens'] == 4500
    assert body['response_format']['json_schema']['strict'] is True
    assert body['response_format']['json_schema']['schema'] == visual.recommendation_schema()
    content = body['messages'][0]['content']
    images = [part['image_url']['url'] for part in content if part['type'] == 'image_url']
    assert len(images) == 10
    assert all(base64.b64decode(url.split(',', 1)[1], validate=True) == JPEG for url in images)
    context = json.loads(content[0]['text'].split('\nContext: ')[1])
    assert context['source'] == result['source']
    assert context['source']['width'] == 1920 and context['source']['height'] == 1080
    assert context['source']['durationSeconds'] == 120
    assert context['source']['hasAudio'] is has_audio
    assert context['transcript'] == ''
    assert context['brief'] == 'Preserve both subjects in frame'
    assert context['sampledFrameTimes'] == result['sampledFrameTimes']
    assert result['sampledFrameTimes'] == pytest.approx(TIMES)
    assert result['analysisBasis'] == 'sampled-frames'
    assert result['captionSettings']['enabled'] is False
    assert result['candidates'][0]['start'] == 0
    assert 'No audio was analyzed' in result['contextNotice']
    for private in (str(pipeline['source']), 'full-video-must-not-be-sent', PROFILE['id'], PROFILE['api_key']):
        assert private not in json.dumps(body) and private not in json.dumps(result)
    assert all(output.closed for output in pipeline['outputs'])
    assert pipeline['post'].call_count == 1
    pipeline['probe'].assert_called_once_with(str(pipeline['source']))


@pytest.mark.parametrize('frame_count', [8, 10, 12])
def test_frame_sampling_is_bounded_and_uncropped(pipeline, frame_count):
    result = analyze(pipeline, frame_count=frame_count)
    assert pipeline['ffmpeg'].call_count == frame_count
    times = result['sampledFrameTimes']
    assert times == sorted(times) and times[0] == 0 and times[-1] == 119.5
    for call, timestamp in zip(pipeline['ffmpeg'].call_args_list, times):
        command = call.args[0]
        assert command[0] == 'ffmpeg' and '-nostdin' in command
        assert command[command.index('-ss') + 1] == str(timestamp)
        assert command[command.index('-map') + 1] == '0:V:0'
        assert command[command.index('-frames:v') + 1] == '1'
        assert command[command.index('-fs') + 1] == str(visual.MAX_FRAME_BYTES)
        assert '-an' in command and command[-1] == 'pipe:1'
        assert '768' in command[command.index('-vf') + 1]
        assert 'crop' not in command[command.index('-vf') + 1]
        assert 0 < call.kwargs['timeout'] <= visual.FRAME_TIMEOUT
        assert call.kwargs['stderr'] == subprocess.DEVNULL


@pytest.mark.parametrize('profile,respond', [(PROFILE, completion), (CLAUDE_PROFILE, anthropic_completion)],
                         ids=['compatible', 'claude'])
def test_optional_transcript_is_bounded_and_enables_real_caption_recommendation(pipeline, profile, respond):
    result = copy.deepcopy(RECOMMENDATION)
    result['captionSettings']['enabled'] = True
    pipeline['post'].return_value = respond(result)
    actual = analyze(pipeline, profile=profile, transcript_text='speech ' * 3000 + 'TAIL_NOT_SENT')
    context = json.loads(pipeline['post'].call_args.kwargs['json']['messages'][0]['content'][0]['text'].split('\nContext: ')[1])
    assert len(context['transcript']) == visual.TRANSCRIPT_LIMIT
    assert 'TAIL_NOT_SENT' not in context['transcript']
    assert actual['captionSettings']['enabled'] is True


@pytest.mark.parametrize('provider', ['deepseek', 'groq', 'openrouter', 'ollama'])
def test_known_incompatible_models_return_capability_error_before_sampling(pipeline, provider):
    with pytest.raises(visual.VisualCapabilityError, match='text-only'):
        analyze(pipeline, provider=provider, profile=None)
    pipeline['ffmpeg'].assert_not_called()
    pipeline['post'].assert_not_called()


@pytest.mark.parametrize('options', [
    {'provider': 'deepseek', 'profile': None}, {'supports_images': False},
    {'profile': CLAUDE_PROFILE, 'supports_images': False},
])
def test_metadata_fallback_makes_no_visual_claims_or_provider_request(pipeline, options):
    result = analyze(pipeline, capability_fallback='metadata-only', **options)
    assert result['analysisBasis'] == 'metadata-only'
    assert result['sampledFrameTimes'] == [] and result['candidates'] == []
    assert result['aspectRatio'] == {'mode': 'preserve', 'ratio': 'source'}
    assert result['videoFilters'] == {'brightness': 0, 'contrast': 1, 'saturation': 1, 'blur': 0, 'sharpen': 0}
    assert result['captionSettings']['enabled'] is False
    assert 'No video frames or audio were analyzed' in result['contextNotice']
    pipeline['post'].assert_not_called()
    pipeline['ffmpeg'].assert_not_called()


def test_audio_only_has_clear_capability_error_or_metadata_fallback(pipeline):
    pipeline['probe'].return_value.update(has_video=False, width=0, height=0, video_codec='')
    with pytest.raises(visual.VisualCapabilityError, match='no video stream'):
        analyze(pipeline)
    result = analyze(pipeline, capability_fallback='metadata-only')
    assert result['source']['hasVideo'] is False and result['source']['hasAudio'] is True
    pipeline['ffmpeg'].assert_not_called()
    pipeline['post'].assert_not_called()


@pytest.mark.parametrize('provider,model', [
    ('openai', 'gpt-4o'), ('gemini', 'gemini-2.0-flash'), ('groq', 'vision-model'),
    ('openrouter', 'vendor/vision-model'), ('ollama', 'llama3.2-vision:11b'),
])
def test_compatible_models_and_profile_overrides_use_existing_connection(pipeline, provider, model):
    profile = {**PROFILE, 'provider': provider, 'model': model, 'base_url': None}
    assert analyze(pipeline, profile=profile)['analysisBasis'] == 'sampled-frames'
    body = pipeline['post'].call_args.kwargs['json']
    assert body['model'] == model
    assert pipeline['post'].call_args.args[0] == llm_service.PROVIDERS[provider]['base_url'] + '/chat/completions'


@pytest.mark.parametrize('base_url', [None, CLAUDE_PROFILE['base_url']])
def test_native_claude_transport_preserves_timestamped_frames_and_frozen_profile(
        pipeline, monkeypatch, base_url):
    profile = {**CLAUDE_PROFILE, 'base_url': base_url}
    original = copy.deepcopy(profile)
    endpoint = base_url or llm_service.PROVIDERS['claude']['base_url']
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'environment-key-must-not-replace-profile')
    native = Mock(wraps=llm_service.call_anthropic)
    compatible = Mock()
    monkeypatch.setattr(llm_service, 'call_anthropic', native)
    monkeypatch.setattr(llm_service, 'call_openai_compatible', compatible)
    pipeline['post'].return_value = anthropic_completion()

    result = analyze(pipeline, profile=profile, brief='Preserve both subjects in frame')

    request = pipeline['post'].call_args
    assert request.args == (endpoint,)
    assert request.kwargs['headers'] == {
        'x-api-key': CLAUDE_PROFILE['api_key'], 'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }
    assert request.kwargs['timeout'] == (5, 60)
    body = request.kwargs['json']
    assert body['model'] == CLAUDE_PROFILE['model']
    assert body['max_tokens'] == 4500
    assert 'response_format' not in body
    assert len(body['messages']) == 1 and body['messages'][0]['role'] == 'user'
    content = body['messages'][0]['content']
    assert isinstance(content, list) and len(content) == 21
    assert content[0]['type'] == 'text'
    context = json.loads(content[0]['text'].split('\nContext: ')[1])
    assert context == {'source': result['source'], 'sampledFrameTimes': result['sampledFrameTimes'],
                        'brief': 'Preserve both subjects in frame', 'transcript': '',
                        'audienceBrief': {'audience': '', 'goal': '', 'notes': ''}}
    schema_text = content[0]['text'].split('no extra fields:\n')[1].split('\nContext: ')[0]
    assert json.loads(schema_text) == visual.recommendation_schema()
    for index, timestamp in enumerate(result['sampledFrameTimes']):
        assert content[1 + 2 * index] == {
            'type': 'text', 'text': f'Frame at {timestamp:.6f} source seconds'}
        image = content[2 + 2 * index]
        assert image == {'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/jpeg',
            'data': base64.b64encode(JPEG).decode('ascii'),
        }}
        assert base64.b64decode(image['source']['data'], validate=True) == JPEG
    assert native.call_args.args == (content,)
    assert native.call_args.kwargs == {
        'profile': {'provider': 'claude', 'base_url': endpoint,
                    'model': CLAUDE_PROFILE['model'], 'api_key': CLAUDE_PROFILE['api_key']},
        'retries': 1, 'timeout': (5, 60), 'max_tokens': 4500,
    }
    native.assert_called_once()
    compatible.assert_not_called()
    assert pipeline['post'].call_count == 1 and pipeline['ffmpeg'].call_count == 10
    assert result['analysisBasis'] == 'sampled-frames'
    assert result['sampledFrameTimes'] == pytest.approx(TIMES)
    assert result['captionSettings']['enabled'] is False
    assert profile == original
    for private in (str(pipeline['source']), 'full-video-must-not-be-sent',
                    CLAUDE_PROFILE['id'], CLAUDE_PROFILE['api_key'], 'data:image'):
        assert private not in json.dumps(body) and private not in json.dumps(result)
    assert all(output.closed for output in pipeline['outputs'])


def test_native_claude_environment_connection_is_frozen_before_sampling(pipeline, monkeypatch):
    config = llm_service.PROVIDERS['claude']
    endpoint, model = config['base_url'], config['model']
    monkeypatch.setenv('ANTHROPIC_API_KEY', PROFILE['api_key'])
    extract = pipeline['ffmpeg'].side_effect

    def change_connection(command, **options):
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'changed-after-sampling-started')
        monkeypatch.setitem(config, 'base_url', 'https://changed.example.test/v1/messages')
        monkeypatch.setitem(config, 'model', 'changed-model')
        return extract(command, **options)

    pipeline['ffmpeg'].side_effect = change_connection
    pipeline['post'].return_value = anthropic_completion()
    assert analyze(pipeline, provider='claude', profile=None)['analysisBasis'] == 'sampled-frames'
    request = pipeline['post'].call_args
    assert request.args == (endpoint,)
    assert request.kwargs['json']['model'] == model
    assert request.kwargs['headers']['x-api-key'] == PROFILE['api_key']
    assert pipeline['post'].call_count == 1


def test_environment_connection_and_local_keyless_model_are_supported(pipeline, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    monkeypatch.setenv('OLLAMA_MODEL', 'qwen2.5vl:7b')
    assert analyze(pipeline, profile=None)['analysisBasis'] == 'sampled-frames'
    assert pipeline['post'].call_args.kwargs['json']['model'] == 'qwen2.5vl:7b'
    assert 'Authorization' not in pipeline['post'].call_args.kwargs['headers']


def test_explicit_vision_declaration_does_not_switch_model(pipeline):
    analyze(pipeline, profile={**PROFILE, 'model': 'deepseek-chat'}, supports_images=True)
    assert pipeline['post'].call_args.kwargs['json']['model'] == 'deepseek-chat'


@pytest.mark.parametrize('fallback', ['error', 'metadata-only'])
@pytest.mark.parametrize('profile', [PROFILE, CLAUDE_PROFILE], ids=['compatible', 'claude'])
@pytest.mark.parametrize('message,status', [
    ('This model does not support image input', 400),
    ('messages[0].content must be a string', 422),
    ('Unsupported content type', 415),
])
def test_provider_rejection_of_image_input_is_an_explicit_capability_failure(
        pipeline, fallback, profile, message, status):
    pipeline['post'].return_value = http_response({'error': {'message': message}}, status)
    if fallback == 'error':
        with pytest.raises(visual.VisualCapabilityError, match='rejected image input'):
            analyze(pipeline, profile=profile, capability_fallback=fallback)
    else:
        assert analyze(pipeline, profile=profile, capability_fallback=fallback)['analysisBasis'] == 'metadata-only'
    assert pipeline['post'].call_count == 1
    assert all(output.closed for output in pipeline['outputs'])


def test_only_schema_capability_failure_retries_with_json_object_and_same_images(pipeline):
    pipeline['post'].side_effect = [
        http_response({'error': {'message': 'response_format json_schema is not supported'}}, 400), completion(),
    ]
    assert analyze(pipeline)['analysisBasis'] == 'sampled-frames'
    first, second = pipeline['post'].call_args_list
    assert pipeline['post'].call_count == 2
    first_body, second_body = first.kwargs['json'], second.kwargs['json']
    assert first_body['response_format']['type'] == 'json_schema'
    assert second_body['response_format'] == {'type': 'json_object'}
    assert first_body['messages'] == second_body['messages']
    assert first_body['model'] == second_body['model'] == PROFILE['model']
    assert first.kwargs['headers'] == second.kwargs['headers']
    assert pipeline['ffmpeg'].call_count == 10


def test_image_rejection_after_schema_retry_can_fall_back(pipeline):
    pipeline['post'].side_effect = [
        http_response({'error': {'message': 'json_schema unsupported'}}, 422),
        http_response({'error': {'message': 'vision input not supported'}}, 422),
    ]
    assert analyze(pipeline, capability_fallback='metadata-only')['analysisBasis'] == 'metadata-only'
    assert pipeline['post'].call_count == 2


@pytest.mark.parametrize('status', [400, 415, 422, 501])
def test_native_claude_format_error_is_redacted_without_compatibility_retry(pipeline, status, caplog):
    private = f"{CLAUDE_PROFILE['api_key']} {pipeline['source']} data:image/jpeg;base64,private"
    pipeline['post'].return_value = http_response({
        'error': {'message': 'response_format json_schema unsupported ' + private}}, status)
    with pytest.raises(RuntimeError, match='provider request failed') as error:
        analyze(pipeline, profile=CLAUDE_PROFILE, capability_fallback='metadata-only')
    assert not isinstance(error.value, visual.VisualCapabilityError)
    assert pipeline['post'].call_count == 1
    assert error.value.__suppress_context__ is True
    for secret in (CLAUDE_PROFILE['api_key'], str(pipeline['source']), 'data:image'):
        assert secret not in str(error.value) + caplog.text


@pytest.mark.parametrize('blocks', [None, {}, [], [{'type': 'image', 'source': {}}],
                                   [{'type': 'text', 'text': None}], [{'type': 'text', 'text': ''}]])
def test_native_claude_invalid_response_blocks_never_fall_back_or_retry(pipeline, blocks):
    pipeline['post'].return_value = http_response({'content': blocks})
    with pytest.raises(RuntimeError, match='unusable response') as error:
        analyze(pipeline, profile=CLAUDE_PROFILE, capability_fallback='metadata-only')
    assert not isinstance(error.value, visual.VisualCapabilityError)
    assert error.value.__suppress_context__ is True
    assert pipeline['post'].call_count == 1


def test_json_object_compatibility_mode_still_rejects_invalid_output(pipeline):
    pipeline['post'].side_effect = [
        http_response({'error': {'message': 'json_schema unsupported'}}, 400),
        completion({**RECOMMENDATION, 'videoFilters': {'brightness': 10}}),
    ]
    with pytest.raises(ValueError):
        analyze(pipeline, capability_fallback='metadata-only')
    assert pipeline['post'].call_count == 2


@pytest.mark.parametrize('provider', ['openai', 'claude'])
def test_missing_hosted_credentials_fail_before_frame_sampling(pipeline, provider):
    with pytest.raises(ValueError, match='API key is not configured'):
        analyze(pipeline, provider=provider, profile=None)
    pipeline['post'].assert_not_called()
    pipeline['ffmpeg'].assert_not_called()


@pytest.mark.parametrize('failure', ['401', '429', '502', 'timeout', 'invalid-envelope', 'refusal', 'bad-json'])
@pytest.mark.parametrize('profile', [PROFILE, CLAUDE_PROFILE], ids=['compatible', 'claude'])
def test_provider_failures_never_fall_back_retry_or_expose_request_data(pipeline, failure, profile, caplog):
    private = f"{PROFILE['api_key']} {pipeline['source']} data:image/jpeg;base64,{base64.b64encode(JPEG).decode()}"
    if failure.isdigit():
        pipeline['post'].return_value = http_response({'error': {'message': private}}, int(failure))
    elif failure == 'timeout':
        pipeline['post'].side_effect = requests.Timeout(private)
    elif failure == 'invalid-envelope':
        pipeline['post'].return_value = http_response({'error': {'message': private}})
    elif failure == 'refusal':
        payload = ({'content': [], 'stop_reason': 'refusal', 'refusal': private}
                   if profile['provider'] == 'claude'
                   else {'choices': [{'message': {'content': None, 'refusal': private}}]})
        pipeline['post'].return_value = http_response(payload)
    else:
        payload = ({'content': [{'type': 'text', 'text': 'not json'}]}
                   if profile['provider'] == 'claude'
                   else {'choices': [{'message': {'content': 'not json'}}]})
        pipeline['post'].return_value = http_response(payload)
    with pytest.raises((RuntimeError, ValueError)) as error:
        analyze(pipeline, profile=profile, capability_fallback='metadata-only')
    assert not isinstance(error.value, visual.VisualCapabilityError)
    assert pipeline['post'].call_count == 1
    assert PROFILE['api_key'] not in str(error.value) + caplog.text
    assert str(pipeline['source']) not in str(error.value) + caplog.text
    assert 'data:image' not in str(error.value) + caplog.text
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize('result', [
    {}, [], None, {**RECOMMENDATION, 'analysisBasis': 'sampled-frames'},
    {**RECOMMENDATION, 'aspectRatio': {'mode': 'crop', 'ratio': 'source'}},
    {**RECOMMENDATION, 'aspectRatio': {'mode': 'preserve', 'ratio': '9:16'}},
    {**RECOMMENDATION, 'aspectRatio': {'mode': 'crop', 'ratio': '3:2'}},
    {**RECOMMENDATION, 'videoFilters': {'brightness': 0}},
    {**RECOMMENDATION, 'videoFilters': {**RECOMMENDATION['videoFilters'], 'vf': 'arbitrary-filter'}},
    {**RECOMMENDATION, 'captionSettings': {'enabled': True, 'preset': 'minimal', 'placement': 'bottom'}},
    {**RECOMMENDATION, 'captionSettings': {'enabled': 'false', 'preset': 'minimal', 'placement': 'bottom'}},
    {**RECOMMENDATION, 'captionSettings': {'enabled': False, 'preset': 'Minimal', 'placement': 'bottom'}},
    {**RECOMMENDATION, 'captionSettings': {'enabled': False, 'preset': 'minimal', 'placement': 'unsafe'}},
    {**RECOMMENDATION, 'candidates': None}, {**RECOMMENDATION, 'candidates': [None]},
    {**RECOMMENDATION, 'candidates': RECOMMENDATION['candidates'] * 9},
    {**RECOMMENDATION, 'rationale': ''}, {**RECOMMENDATION, 'rationale': 'x' * 1001},
    {**RECOMMENDATION, 'rationale': 'text\x00'},
])
@pytest.mark.parametrize('profile,respond', [(PROFILE, completion), (CLAUDE_PROFILE, anthropic_completion)],
                         ids=['compatible', 'claude'])
def test_malformed_model_output_is_rejected_by_public_api(pipeline, result, profile, respond):
    pipeline['post'].return_value = respond(result)
    with pytest.raises(ValueError):
        analyze(pipeline, profile=profile, capability_fallback='metadata-only')
    assert pipeline['post'].call_count == 1


@pytest.mark.parametrize('field,value', [
    ('brightness', -1.1), ('brightness', 1.1), ('contrast', -0.1), ('contrast', 4.1),
    ('saturation', -0.1), ('saturation', 4.1), ('blur', -0.1), ('blur', 10.1),
    ('sharpen', -0.1), ('sharpen', 2.1), ('brightness', True), ('blur', '2'),
    ('contrast', float('nan')), ('saturation', float('inf')),
])
def test_filter_ranges_and_json_number_types_are_enforced(field, value):
    result = copy.deepcopy(RECOMMENDATION)
    result['videoFilters'][field] = value
    with pytest.raises(ValueError):
        visual.parse_recommendation(json.dumps(result), 120, TIMES)


@pytest.mark.parametrize('field,value', [
    ('start', -1), ('start', True), ('start', '0'), ('start', float('nan')),
    ('end', 0), ('end', 14.9), ('end', 60.1), ('end', 121), ('end', float('inf')),
    ('score', -1), ('score', 101), ('score', False), ('score', '75'),
    ('hook', ''), ('hook', 'x' * 201), ('hook', []), ('rationale', 'x' * 1001),
])
def test_candidate_bounds_and_types_are_strict(field, value):
    result = copy.deepcopy(RECOMMENDATION)
    result['candidates'][0][field] = value
    with pytest.raises(ValueError):
        visual.parse_recommendation(json.dumps(result), 120, TIMES)


def test_candidates_must_not_overlap_and_must_have_sampled_evidence():
    result = copy.deepcopy(RECOMMENDATION)
    result['candidates'].append({**result['candidates'][0], 'start': 20, 'end': 40, 'evidenceFrame': 2})
    with pytest.raises(ValueError, match='overlap'):
        visual.validate_recommendation(result, 120, TIMES)
    result['candidates'] = [{**result['candidates'][0], 'start': 20, 'end': 40}]
    with pytest.raises(ValueError, match='sampled frame'):
        visual.validate_recommendation(result, 1200, [index * 120 for index in range(10)])


def test_empty_candidates_are_valid_and_accepted_candidates_are_score_ranked():
    result = copy.deepcopy(RECOMMENDATION)
    result['candidates'] = []
    assert visual.validate_recommendation(result, 120, TIMES)['candidates'] == []
    result['candidates'] = [RECOMMENDATION['candidates'][0],
                            {**RECOMMENDATION['candidates'][0], 'start': 30, 'end': 55, 'evidenceFrame': 3,
                             'topic': 'A distinct second idea',
                             'assessment': {'audienceFit': 5, 'hook': 4, 'payoff': 5, 'clarity': 4, 'shareability': 3}}]
    assert [c['score'] for c in visual.validate_recommendation(result, 120, TIMES)['candidates']] == [89, 78]


@pytest.mark.parametrize('duration', [0.1, 10, 15])
def test_short_sources_allow_only_the_complete_short_clip_and_bounded_samples(pipeline, duration):
    pipeline['probe'].return_value['duration_sec'] = duration
    result = copy.deepcopy(RECOMMENDATION)
    result['candidates'][0]['end'] = duration
    pipeline['post'].return_value = completion(result)
    actual = analyze(pipeline)
    assert actual['candidates'][0]['end'] == duration
    assert all(0 <= timestamp < duration for timestamp in actual['sampledFrameTimes'])


@pytest.mark.parametrize('text', [
    'not json', '```json\n{}\n```', '{"rationale":"a","rationale":"b"}',
    '{"videoFilters":{"blur":1,"blur":2}}', 'NaN', '[{}]',
    '{' + '"x":[' * 1100 + '0' + ']' * 1100 + '}', 'x' * (visual.MAX_RESPONSE_CHARS + 1),
])
def test_parser_rejects_non_strict_json_without_echoing_raw_response(text):
    with pytest.raises(ValueError) as error:
        visual.parse_recommendation(text, 120, TIMES)
    assert len(str(error.value)) < 200


@pytest.mark.parametrize('escaped', [False, True])
@pytest.mark.parametrize('key', [PROFILE['api_key'], 'fixture-key-"quote\\-\u2603'])
@pytest.mark.parametrize('profile', [PROFILE, CLAUDE_PROFILE], ids=['compatible', 'claude'])
def test_credentials_echoed_in_valid_model_content_are_never_returned(pipeline, escaped, key, profile):
    result = copy.deepcopy(RECOMMENDATION)
    result['rationale'] = key
    text = json.dumps(result)
    if escaped:
        text = text.replace(json.dumps(key)[1:-1], ''.join(f'\\u{ord(char):04x}' for char in key))
    payload = ({'content': [{'type': 'text', 'text': text}]}
               if profile['provider'] == 'claude'
               else {'choices': [{'message': {'content': text}}]})
    pipeline['post'].return_value = http_response(payload)
    with pytest.raises(ValueError, match='private connection data') as error:
        analyze(pipeline, profile={**profile, 'api_key': key})
    assert key not in str(error.value)


def test_deep_nesting_inside_a_schema_field_is_rejected_with_a_safe_value_error():
    text = json.dumps(RECOMMENDATION).replace(json.dumps(RECOMMENDATION['rationale']),
                                            '[' * 600 + '0' + ']' * 600)
    with pytest.raises(ValueError):
        visual.parse_recommendation(text, 120, TIMES)


@pytest.mark.parametrize('options', [
    {'frame_count': 7}, {'frame_count': 13}, {'frame_count': True}, {'frame_count': 8.0},
    {'brief': None}, {'brief': 'x' * 5001}, {'brief': '\x00'}, {'transcript_text': []},
    {'transcript_text': '\x00'}, {'supports_images': 'yes'}, {'capability_fallback': 'silent'},
    {'provider': 'unknown'}, {'provider': 'openai'}, {'profile': []},
    {'profile': {**PROFILE, 'kind': 'transcription'}},
    {'profile': {**PROFILE, 'model': ''}}, {'profile': {**PROFILE, 'api_key': []}},
    {'profile': {**PROFILE, 'base_url': 'https://user:password@vision.example.test/v1'}},
])
def test_invalid_input_fails_before_http_and_ffmpeg(pipeline, options):
    with pytest.raises(ValueError):
        analyze(pipeline, **options)
    pipeline['post'].assert_not_called()
    pipeline['ffmpeg'].assert_not_called()


@pytest.mark.parametrize('metadata', [
    None, {**METADATA, 'duration_sec': 0}, {**METADATA, 'duration_sec': float('inf')},
    {**METADATA, 'duration_sec': True}, {**METADATA, 'has_video': 'true'},
    {**METADATA, 'width': 0}, {**METADATA, 'height': True},
    {**METADATA, 'audio_codec': '/private/path'},
])
def test_invalid_metadata_fails_before_sampling_or_provider(pipeline, metadata):
    pipeline['probe'].return_value = metadata
    with pytest.raises(ValueError):
        analyze(pipeline)
    pipeline['post'].assert_not_called()
    pipeline['ffmpeg'].assert_not_called()


def test_probe_errors_and_missing_local_files_do_not_expose_source_paths(pipeline):
    pipeline['probe'].side_effect = FileNotFoundError(str(pipeline['source']))
    with pytest.raises(RuntimeError, match='probe') as error:
        analyze(pipeline)
    assert str(pipeline['source']) not in str(error.value)
    with pytest.raises(ValueError, match='existing local media'):
        visual.analyze_video('https://example.test/video.mp4', profile=PROFILE)
    pipeline['post'].assert_not_called()


@pytest.mark.parametrize('failure', ['timeout', 'ffmpeg', 'empty', 'truncated', 'oversized', 'total-limit'])
def test_extraction_failures_close_temporaries_and_never_send_images(pipeline, failure):
    def fail(command, *, stdout, **options):
        pipeline['outputs'].append(stdout)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, options['timeout'])
        if failure == 'ffmpeg':
            raise subprocess.CalledProcessError(1, command, stderr=b'private source path')
        image = {'empty': b'', 'truncated': b'\xff\xd8not-complete',
                 'oversized': b'\xff\xd8' + b'x' * visual.MAX_FRAME_BYTES + b'\xff\xd9',
                 'total-limit': b'\xff\xd8' + b'x' * (visual.MAX_FRAME_BYTES - 4) + b'\xff\xd9'}[failure]
        stdout.write(image)

    pipeline['ffmpeg'].side_effect = fail
    with pytest.raises(RuntimeError) as error:
        analyze(pipeline)
    assert str(pipeline['source']) not in str(error.value)
    assert pipeline['outputs'] and all(output.closed for output in pipeline['outputs'])
    assert pipeline['ffmpeg'].call_count <= 9
    pipeline['post'].assert_not_called()


def test_total_sampling_deadline_is_enforced(pipeline, monkeypatch):
    monkeypatch.setattr(visual.time, 'monotonic', Mock(side_effect=[0, 91]))
    with pytest.raises(RuntimeError, match='time budget'):
        analyze(pipeline)
    pipeline['ffmpeg'].assert_not_called()
    pipeline['post'].assert_not_called()


@pytest.mark.parametrize('source_size,sar', [
    ('1920x1080', '1/1'), ('91x319', '1/1'), ('720x576', '16/15'),
    ('720x576', '64/45'), ('640x480', '1/2'), ('1920x1080', '2/1'),
])
def test_real_ffmpeg_sampling_preserves_displayed_ratio_without_cropping(tmp_path, source_size, sar):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real FFmpeg/FFprobe required')
    source = tmp_path / 'source.mkv'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=c=red:s={source_size}:r=10', '-vf', f'setsar={sar}',
                    '-t', '1', '-c:v', 'ffv1', '-y', str(source)],
                   check=True, capture_output=True, timeout=30)
    frames = visual.sample_frames(str(source), 1, 8)
    assert len(frames) == 8
    first = tmp_path / 'frame.jpg'
    first.write_bytes(base64.b64decode(frames[0]['dataUrl'].split(',', 1)[1]))
    metadata = visual.media_service.probe_media(str(first))
    # FFmpeg's color source rounds odd dimensions down; compare to actual source.
    original = visual.media_service.probe_media(str(source))
    numerator, denominator = map(int, sar.split('/'))
    assert original['sample_aspect_ratio'] == pytest.approx(numerator / denominator)
    assert max(metadata['width'], metadata['height']) <= 768
    assert metadata['sample_aspect_ratio'] == 1
    assert metadata['width'] / metadata['height'] == pytest.approx(
        original['display_width'] / original['display_height'], abs=0.01)
    assert metadata['has_audio'] is False
    assert original['display_width'] >= metadata['width']
    assert original['display_height'] >= metadata['height']
