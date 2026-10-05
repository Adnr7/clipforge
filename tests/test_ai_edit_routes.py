"""AI Edit contracts: real SQLite, deferred workers, and controlled providers."""

import copy
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.routes import ai_edit, analysis
from backend.services import llm_service, visual_analysis_service
from tests.test_pipeline_routes import (
    DRAFTS, TRANSCRIPT, app, client, execute, jobs, rows, seed_project,
)


ROOT = '/api/projects/project'
SECRET = 'private-ai-edit-profile-key'
MODEL = 'connected-vision-model'
BRIEF = 'Audience: new editors\nGoal: preserve both subjects and a natural opening'
METADATA = {
    'duration_sec': 60, 'width': 1920, 'height': 1080,
    'has_video': True, 'has_audio': True, 'video_codec': 'h264', 'audio_codec': 'aac',
}
RECOMMENDATION = {
    'aspectRatio': {'mode': 'pad', 'ratio': '9:16'},
    'videoFilters': {'brightness': 0.05, 'contrast': 1.1, 'saturation': 1,
                     'blur': 0, 'sharpen': 0.2},
    'captionSettings': {'enabled': False, 'preset': 'minimal', 'placement': 'bottom'},
    'candidates': DRAFTS,
    'rationale': 'Padding preserves the subjects visible in the sampled frames.',
    'analysisBasis': 'sampled-frames',
    'source': {'width': 1920, 'height': 1080, 'durationSeconds': 60,
               'hasVideo': True, 'hasAudio': True, 'videoCodec': 'h264', 'audioCodec': 'aac'},
    'sampledFrameTimes': [index * 59.5 / 9 for index in range(10)],
    'contextNotice': 'Sparse sampled frames, not the full video. No audio was analyzed.',
}
TABLES = ('projects', 'transcripts', 'candidates', 'clips', 'filter_suggestions',
          'provider_profiles', 'provider_active_profiles')
OPENAI_COMPATIBLE_SIGNATURE = inspect.signature(llm_service.call_openai_compatible)
ANTHROPIC_SIGNATURE = inspect.signature(llm_service.call_anthropic)


def assert_status(response, expected):
    """Avoid dumping request bodies, long model output, or fixture credentials."""
    if response.status_code != expected:
        pytest.fail(f'Expected HTTP {expected}, got HTTP {response.status_code}', pytrace=False)


def assert_error(response, expected):
    assert_status(response, expected)
    assert isinstance(response.json, dict)
    assert isinstance(response.json.get('error'), str)
    assert response.json['error'].strip()


def seed_source(app, project_id='project', **options):
    seed_project(app, project_id, **options)
    execute(app, 'UPDATE projects SET source_metadata_json=? WHERE id=?',
            (json.dumps(METADATA), project_id))


def set_transcript(app, raw):
    if raw is None:
        execute(app, 'DELETE FROM transcripts WHERE project_id=?', ('project',))
    else:
        execute(app, 'UPDATE transcripts SET raw_json=? WHERE project_id=?', (raw, 'project'))


def create_profile(client, *, provider='custom', model=MODEL, active=True):
    response = client.post('/api/provider-profiles', json={
        'name': 'AI Edit connection', 'kind': 'llm', 'provider': provider,
        'baseUrl': 'https://vision.example.test/v1', 'apiKey': SECRET, 'model': model,
    })
    assert_status(response, 201)
    profile = response.json
    if active:
        assert_status(client.put('/api/provider-profiles/active', json={
            'kind': 'llm', 'profileId': profile['id'],
        }), 200)
    return profile


def snapshot(app):
    return {table: rows(app, f'SELECT * FROM {table} ORDER BY rowid') for table in TABLES}


def assert_unchanged(app, before):
    for table, expected in before.items():
        if rows(app, f'SELECT * FROM {table} ORDER BY rowid') != expected:
            pytest.fail(f'AI Edit unexpectedly mutated {table}', pytrace=False)


def visual_arguments(mock):
    call = mock.call_args
    bound = inspect.signature(visual_analysis_service.analyze_video).bind(*call.args, **call.kwargs)
    bound.apply_defaults()
    return bound.arguments


@pytest.fixture
def ai_services(monkeypatch):
    mocks = SimpleNamespace(
        worker=Mock(side_effect=lambda *args, **kwargs: copy.deepcopy(RECOMMENDATION)),
        recommend=Mock(side_effect=lambda *args, **kwargs: copy.deepcopy(RECOMMENDATION)),
        transcript=Mock(return_value=copy.deepcopy(DRAFTS)),
        compatible=Mock(return_value=json.dumps({'message': 'Preserve both subjects with padding.'})),
        anthropic=Mock(return_value=json.dumps({'message': 'Keep the opening grounded in the source.'})),
    )
    monkeypatch.setattr(analysis, 'analyze_video', mocks.worker)
    monkeypatch.setattr(analysis, 'analyze_transcript', mocks.transcript)
    monkeypatch.setattr(ai_edit, 'analyze_video', mocks.recommend)
    monkeypatch.setattr(llm_service, 'call_openai_compatible', mocks.compatible)
    monkeypatch.setattr(llm_service, 'call_anthropic', mocks.anthropic)
    return mocks


@pytest.mark.parametrize('raw', [
    None, '{', 'null', '[]', json.dumps({'segments': [], 'words': []}),
    json.dumps({'segments': [{'text': []}], 'words': 'invalid'}),
], ids=['missing', 'broken-json', 'null', 'array', 'empty-speech', 'malformed-arrays'])
def test_visual_worker_does_not_require_a_usable_transcript(app, client, jobs, ai_services, raw):
    seed_source(app, status='imported')
    set_transcript(app, raw)
    before_transcript = rows(app, 'SELECT * FROM transcripts')
    profile = create_profile(client)

    assert_status(client.post(f'{ROOT}/analyze', json={'mode': 'visual', 'brief': BRIEF}), 202)
    assert rows(app, 'SELECT status, last_job_stage FROM projects') == [
        {'status': 'analyzing', 'last_job_stage': 'analysis'},
    ]
    assert client.get(f'{ROOT}/analyze/status').json == {'status': 'processing', 'error': None}
    assert len(jobs.tasks) == 1
    ai_services.worker.assert_not_called()
    assert rows(app, 'SELECT id FROM candidates') == [{'id': 'candidate-project-0'}]

    jobs.run_all()

    ai_services.worker.assert_called_once()
    ai_services.transcript.assert_not_called()
    arguments = visual_arguments(ai_services.worker)
    assert arguments['source_path'] == rows(app, 'SELECT source_path FROM projects')[0]['source_path']
    assert arguments['provider'] == 'custom'
    assert arguments['profile']['id'] == profile['id']
    assert arguments['profile']['model'] == MODEL
    assert arguments['profile']['api_key'] == SECRET
    assert arguments['brief'] == BRIEF
    assert not arguments['transcript_text']
    assert client.get(f'{ROOT}/analyze/status').json == {'status': 'done', 'error': None}
    saved = rows(app, 'SELECT status, visual_analysis_json FROM projects')[0]
    assert saved['status'] == 'analyzed'
    recommendation = json.loads(saved['visual_analysis_json'])
    assert recommendation['analysisBasis'] == 'sampled-frames'
    assert recommendation['sampledFrameTimes'] == RECOMMENDATION['sampledFrameTimes']
    assert recommendation['model'] == MODEL
    assert rows(app, 'SELECT hook, score, rank, selected FROM candidates ORDER BY rank') == [
        {'hook': 'Second', 'score': 90, 'rank': 1, 'selected': 0},
        {'hook': 'First', 'score': 70, 'rank': 2, 'selected': 0},
    ]
    assert rows(app, 'SELECT * FROM transcripts') == before_transcript
    detail = client.get(ROOT)
    assert_status(detail, 200)
    assert detail.json['project']['visualAnalysis'] == recommendation
    assert SECRET not in json.dumps(recommendation)


@pytest.mark.parametrize('selection', ['active', 'explicit', 'provider-only'])
def test_visual_worker_uses_the_selected_connection_and_stays_project_scoped(
        app, client, jobs, ai_services, selection):
    seed_source(app)
    seed_source(app, 'other')
    other = rows(app, 'SELECT * FROM candidates WHERE project_id=?', ('other',))
    profile = create_profile(client, active=selection != 'explicit')
    body = {'mode': 'visual', 'brief': BRIEF}
    if selection == 'explicit':
        body.update(profileId=profile['id'], provider='custom')
    elif selection == 'provider-only':
        body['provider'] = 'openai'
    assert_status(client.post(f'{ROOT}/analyze', json=body), 202)
    jobs.run_all()
    assert client.get(f'{ROOT}/analyze/status').json['status'] == 'done'
    arguments = visual_arguments(ai_services.worker)
    assert arguments['transcript_text'] == 'Hello world'
    assert arguments['brief'] == BRIEF
    if selection == 'provider-only':
        assert arguments['provider'] == 'openai'
        assert arguments['profile'] is None
    else:
        assert arguments['provider'] == 'custom'
        assert arguments['profile']['id'] == profile['id']
        assert arguments['profile']['base_url'] == 'https://vision.example.test/v1'
    assert rows(app, 'SELECT * FROM candidates WHERE project_id=?', ('other',)) == other
    assert rows(app, 'SELECT status, visual_analysis_json FROM projects WHERE id=?', ('other',)) == [
        {'status': 'transcribed', 'visual_analysis_json': None},
    ]


@pytest.mark.parametrize('other_endpoint,other_body', [
    ('analyze', {'mode': 'visual'}), ('analyze', {'mode': 'transcript'}), ('transcribe', {}),
], ids=['visual-vs-visual', 'visual-vs-transcript', 'visual-vs-transcribe'])
def test_visual_analysis_claim_is_atomic_with_existing_pipeline_jobs(
        app, jobs, ai_services, other_endpoint, other_body):
    seed_source(app)
    ready = threading.Barrier(2)

    def submit(request):
        endpoint, body = request
        with app.test_client() as request_client:
            ready.wait(timeout=5)
            return request_client.post(f'{ROOT}/{endpoint}', json=body).status_code

    with ThreadPoolExecutor(max_workers=2) as requests:
        statuses = list(requests.map(submit, [
            ('analyze', {'mode': 'visual', 'brief': BRIEF}), (other_endpoint, other_body),
        ]))
    assert sorted(statuses) == [202, 409]
    assert len(jobs.tasks) == 1
    ai_services.worker.assert_not_called()


@pytest.mark.parametrize('state', ['pending', 'rendering'])
def test_visual_analysis_cannot_replace_candidates_of_active_renders(
        app, client, jobs, ai_services, state):
    seed_source(app)
    execute(app, 'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
            ('active-render', 'candidate-project-0', state))
    before = snapshot(app)
    assert_error(client.post(f'{ROOT}/analyze', json={'mode': 'visual'}), 409)
    assert_unchanged(app, before)
    assert jobs.tasks == []
    ai_services.worker.assert_not_called()


@pytest.mark.parametrize('bad_result', [
    None, {}, [], {**RECOMMENDATION, 'candidates': None},
    {**RECOMMENDATION, 'candidates': [None]},
    {**RECOMMENDATION, 'candidates': [{'start': 0, 'end': 20}]},
    {**RECOMMENDATION, 'candidates': [{**DRAFTS[0], 'score': float('nan')}]},
    {**RECOMMENDATION, 'candidates': [{**DRAFTS[0], 'start': -1}]},
    {**RECOMMENDATION, 'candidates': [{**DRAFTS[0], 'end': 61}]},
], ids=['null', 'empty-object', 'array', 'null-candidates', 'null-candidate',
        'incomplete-candidate', 'nan-score', 'negative-start', 'past-duration'])
def test_bad_visual_results_preserve_candidates_clips_and_saved_recommendation(
        app, client, jobs, ai_services, bad_result):
    seed_source(app)
    execute(app, 'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
            ('saved-render', 'candidate-project-0', 'done'))
    old = json.dumps({**RECOMMENDATION, 'model': 'previous-model'})
    execute(app, 'UPDATE projects SET visual_analysis_json=?', (old,))
    before_candidates = rows(app, 'SELECT * FROM candidates')
    before_clips = rows(app, 'SELECT * FROM clips')
    ai_services.worker.side_effect = None
    ai_services.worker.return_value = copy.deepcopy(bad_result)

    assert_status(client.post(f'{ROOT}/analyze', json={'mode': 'visual'}), 202)
    jobs.run_all()

    assert client.get(f'{ROOT}/analyze/status').json['status'] == 'error'
    assert rows(app, 'SELECT status, visual_analysis_json FROM projects') == [
        {'status': 'error', 'visual_analysis_json': old},
    ]
    assert rows(app, 'SELECT * FROM candidates') == before_candidates
    assert rows(app, 'SELECT * FROM clips') == before_clips


def test_visual_completion_write_failure_rolls_back_the_whole_replacement(
        app, client, jobs, ai_services):
    seed_source(app)
    execute(app, 'INSERT INTO clips (id, candidate_id, status) VALUES (?,?,?)',
            ('saved-render', 'candidate-project-0', 'done'))
    execute(app, """CREATE TRIGGER reject_visual_completion BEFORE UPDATE ON projects
                    WHEN NEW.status='analyzed' BEGIN SELECT RAISE(FAIL, 'write failed'); END""")
    before_candidates = rows(app, 'SELECT * FROM candidates')
    before_clips = rows(app, 'SELECT * FROM clips')
    assert_status(client.post(f'{ROOT}/analyze', json={'mode': 'visual'}), 202)
    jobs.run_all()
    assert client.get(f'{ROOT}/analyze/status').json['status'] == 'error'
    assert rows(app, 'SELECT visual_analysis_json FROM projects') == [{'visual_analysis_json': None}]
    assert rows(app, 'SELECT * FROM candidates') == before_candidates
    assert rows(app, 'SELECT * FROM clips') == before_clips


def test_visual_provider_failure_is_retryable_without_replacing_previous_outputs(
        app, client, jobs, ai_services):
    seed_source(app)
    before = rows(app, 'SELECT * FROM candidates')
    ai_services.worker.side_effect = RuntimeError('Visual analysis provider request failed')
    assert_status(client.post(f'{ROOT}/analyze', json={'mode': 'visual'}), 202)
    jobs.run_all()
    assert client.get(f'{ROOT}/analyze/status').json['status'] == 'error'
    assert rows(app, 'SELECT * FROM candidates') == before
    ai_services.worker.side_effect = lambda *args, **kwargs: copy.deepcopy(RECOMMENDATION)
    assert_status(client.post(f'{ROOT}/analyze', json={'mode': 'visual'}), 202)
    assert client.get(f'{ROOT}/analyze/status').json == {'status': 'processing', 'error': None}
    jobs.run_all()
    assert client.get(f'{ROOT}/analyze/status').json == {'status': 'done', 'error': None}


@pytest.mark.parametrize('body', [None, {}, {'mode': 'transcript'}],
                         ids=['no-body', 'empty-object', 'explicit-transcript'])
def test_legacy_analysis_still_defaults_to_transcript(app, client, jobs, ai_services, body):
    seed_source(app)
    options = {} if body is None else {'json': body}
    assert_status(client.post(f'{ROOT}/analyze', **options), 202)
    jobs.run_all()
    ai_services.worker.assert_not_called()
    ai_services.transcript.assert_called_once()
    call = ai_services.transcript.call_args
    bound = inspect.signature(llm_service.analyze_transcript).bind(*call.args, **call.kwargs)
    assert bound.arguments['transcript_text'] == 'Hello world'
    assert bound.arguments['duration'] == 60
    assert client.get(f'{ROOT}/analyze/status').json == {'status': 'done', 'error': None}


def test_transcript_analysis_receives_the_optional_editing_brief(app, client, jobs, ai_services):
    seed_source(app)
    assert_status(client.post(f'{ROOT}/analyze', json={'mode': 'transcript', 'brief': BRIEF}), 202)
    jobs.run_all()
    call = ai_services.transcript.call_args
    bound = inspect.signature(llm_service.analyze_transcript).bind(*call.args, **call.kwargs)
    assert bound.arguments['brief'] == BRIEF
    assert bound.arguments['transcript_text'] == 'Hello world'
    ai_services.worker.assert_not_called()
    assert client.get(f'{ROOT}/analyze/status').json['status'] == 'done'


@pytest.mark.parametrize('endpoint', [
    'ai-edit/chat', 'ai-edit/recommendations', 'visual', 'transcript',
])
def test_full_length_editing_brief_reaches_each_model_path_intact(
        app, client, jobs, ai_services, endpoint):
    seed_source(app)
    create_profile(client)
    brief = 'Audience: ' + 'a' * 1000 + '\nGoal: ' + 'g' * 1000 + '\nNotes: ' + 'n' * 2000
    brief += '\nPlatform: YouTube Shorts\nCaptions: on\nFINAL PREFERENCE'
    # Exercise the shared API ceiling as well as the form's combined field lengths.
    brief += 'x' * (5000 - len(brief))
    if endpoint in ('visual', 'transcript'):
        response = client.post(f'{ROOT}/analyze', json={'mode': endpoint, 'brief': brief})
        assert_status(response, 202)
        jobs.run_all()
        service = ai_services.worker if endpoint == 'visual' else ai_services.transcript
        assert service.call_args.kwargs['brief'] == brief
        assert client.get(f'{ROOT}/analyze/status').json['status'] == 'done'
    else:
        body = {'brief': brief}
        if endpoint == 'ai-edit/chat':
            body['message'] = 'Help me keep the full brief.'
        before = snapshot(app)
        assert_status(client.post(f'{ROOT}/{endpoint}', json=body), 200)
        if endpoint == 'ai-edit/chat':
            context = json.loads(ai_services.compatible.call_args.args[0].split('Context:\n')[1])
            assert context['brief'] == brief
        else:
            assert ai_services.recommend.call_args.kwargs['brief'] == brief
        assert_unchanged(app, before)


@pytest.mark.parametrize('raw', [None, '{', json.dumps({'segments': [], 'words': []})],
                         ids=['missing', 'broken-json', 'no-speech'])
def test_transcript_mode_still_requires_usable_speech(app, client, jobs, ai_services, raw):
    seed_source(app)
    set_transcript(app, raw)
    before = snapshot(app)
    assert_error(client.post(f'{ROOT}/analyze', json={'mode': 'transcript', 'brief': BRIEF}), 400)
    assert_unchanged(app, before)
    assert jobs.tasks == []
    ai_services.transcript.assert_not_called()


@pytest.mark.parametrize('body', [
    [], {'mode': []}, {'mode': 'unknown'}, {'mode': 'visual', 'brief': None},
    {'mode': 'visual', 'brief': []}, {'mode': 'visual', 'brief': 'x' * 5001},
    {'mode': 'visual', 'provider': []}, {'mode': 'visual', 'provider': 'unknown'},
    {'mode': 'visual', 'profileId': []}, {'mode': 'visual', 'model': 'override-model'},
], ids=['array', 'mode-type', 'unknown-mode', 'null-brief', 'brief-type', 'long-brief',
        'provider-type', 'unknown-provider', 'profile-type', 'unknown-field'])
def test_invalid_analysis_requests_do_not_claim_or_dispatch(app, client, jobs, ai_services, body):
    seed_source(app)
    before = snapshot(app)
    assert_error(client.post(f'{ROOT}/analyze', json=body), 400)
    assert_unchanged(app, before)
    assert jobs.tasks == []
    ai_services.worker.assert_not_called()
    ai_services.transcript.assert_not_called()


def test_visual_profile_and_provider_must_match_before_dispatch(app, client, jobs, ai_services):
    seed_source(app)
    profile = create_profile(client)
    before = snapshot(app)
    assert_error(client.post(f'{ROOT}/analyze', json={
        'mode': 'visual', 'provider': 'openai', 'profileId': profile['id'],
    }), 400)
    assert_unchanged(app, before)
    assert jobs.tasks == []
    ai_services.worker.assert_not_called()


@pytest.mark.parametrize('raw,expected_text', [
    (None, None), ('{', None), (json.dumps({'segments': [], 'words': []}), None),
    (json.dumps(TRANSCRIPT), 'Hello world'),
], ids=['no-transcript', 'broken-transcript', 'silent-transcript', 'speech-transcript'])
def test_recommendations_return_full_visual_evidence_using_active_profile_without_mutations(
        app, client, jobs, ai_services, raw, expected_text):
    seed_source(app)
    seed_source(app, 'other')
    set_transcript(app, raw)
    create_profile(client)
    before = snapshot(app)

    response = client.post(f'{ROOT}/ai-edit/recommendations', json={'brief': BRIEF})

    assert_status(response, 200)
    for key, value in RECOMMENDATION.items():
        # Score ordering is allowed at the route's persistence/response boundary.
        if key == 'candidates':
            assert sorted(response.json[key], key=lambda candidate: candidate['score']) == DRAFTS
        else:
            assert response.json[key] == value
    assert response.json['model'] == MODEL
    assert response.json['provider'] == 'custom'
    assert SECRET not in response.text
    ai_services.recommend.assert_called_once()
    arguments = visual_arguments(ai_services.recommend)
    assert arguments['source_path'] == rows(app, 'SELECT source_path FROM projects WHERE id=?',
                                            ('project',))[0]['source_path']
    assert arguments['provider'] == 'custom'
    assert arguments['profile']['model'] == MODEL
    assert arguments['profile']['api_key'] == SECRET
    assert arguments['brief'] == BRIEF
    assert (arguments['transcript_text'] or None) == expected_text
    ai_services.worker.assert_not_called()
    ai_services.transcript.assert_not_called()
    assert jobs.tasks == []
    assert_unchanged(app, before)


@pytest.mark.parametrize('provider', ['custom', 'claude'])
@pytest.mark.parametrize('has_transcript', [False, True], ids=['metadata', 'transcript'])
def test_chat_uses_current_analysis_connection_with_scoped_context_and_no_mutations(
        app, client, ai_services, provider, has_transcript):
    seed_source(app)
    seed_source(app, 'other')
    execute(app, 'UPDATE transcripts SET raw_json=? WHERE project_id=?',
            (json.dumps({'segments': [{'text': 'OTHER_PROJECT_PRIVATE_SPEECH'}]}), 'other'))
    if not has_transcript:
        set_transcript(app, None)
    profile = create_profile(client, provider=provider, model='chat-connected-model')
    private_name = 'private-source-filename.mp4'
    private_path = rows(app, 'SELECT source_path FROM projects WHERE id=?', ('project',))[0]['source_path']
    execute(app, 'UPDATE projects SET name=?, source_metadata_json=? WHERE id=?',
            (private_name, json.dumps({**METADATA, 'filename': private_name, 'path': private_path,
                                      'api_key': SECRET}), 'project'))
    history = [
        {'role': 'guide', 'text': 'Review the brief before editing.'},
        {'role': 'user', 'text': 'Preserve the wide composition.'},
        {'role': 'model', 'text': 'Padding can keep both subjects visible.'},
    ]
    before = snapshot(app)
    response = client.post(f'{ROOT}/ai-edit/chat', json={
        'message': 'What should I change about the opening?', 'messages': history, 'brief': BRIEF,
    })

    assert_status(response, 200)
    expected_message = ('Keep the opening grounded in the source.' if provider == 'claude'
                        else 'Preserve both subjects with padding.')
    assert response.json['message'] == expected_message
    assert response.json['model'] == 'chat-connected-model'
    assert response.json['basis'] == ('transcript' if has_transcript else 'metadata')
    selected = ai_services.anthropic if provider == 'claude' else ai_services.compatible
    unused = ai_services.compatible if provider == 'claude' else ai_services.anthropic
    selected.assert_called_once()
    unused.assert_not_called()
    call = selected.call_args
    signature = ANTHROPIC_SIGNATURE if provider == 'claude' else OPENAI_COMPATIBLE_SIGNATURE
    bound = signature.bind(*call.args, **call.kwargs).arguments
    assert bound['profile']['id'] == profile['id']
    assert bound['profile']['api_key'] == SECRET
    assert bound['profile']['model'] == 'chat-connected-model'
    if provider != 'claude':
        assert bound['provider'] == 'custom'
    prompt = bound['prompt']
    assert isinstance(prompt, str)
    for required in ('What should I change about the opening?', BRIEF, '1920', '1080', '60',
                     *(message['text'] for message in history)):
        if required not in prompt and json.dumps(required)[1:-1] not in prompt:
            pytest.fail('Chat prompt is missing requested project context', pytrace=False)
    assert ('Hello world' in prompt) is has_transcript
    for private in (private_name, private_path, SECRET, 'OTHER_PROJECT_PRIVATE_SPEECH'):
        if private in prompt:
            pytest.fail('Chat prompt exposed private or other-project context', pytrace=False)
    assert_unchanged(app, before)
    ai_services.worker.assert_not_called()
    ai_services.recommend.assert_not_called()


@pytest.mark.parametrize('raw', ['{', '[]', json.dumps({'segments': [], 'words': []})],
                         ids=['broken-json', 'malformed-object', 'no-speech'])
def test_chat_uses_metadata_when_transcript_is_unusable(app, client, ai_services, raw):
    seed_source(app)
    set_transcript(app, raw)
    create_profile(client)
    response = client.post(f'{ROOT}/ai-edit/chat', json={'message': 'How should I frame this?'})
    assert_status(response, 200)
    assert response.json['basis'] == 'metadata'


@pytest.mark.parametrize('endpoint,body', [
    ('recommendations', []), ('recommendations', {'brief': None}),
    ('recommendations', {'brief': []}), ('recommendations', {'brief': 'x' * 5001}),
    ('recommendations', {'brief': 'ok', 'provider': 'openai'}),
    ('chat', []), ('chat', {}), ('chat', {'message': None}),
    ('chat', {'message': []}), ('chat', {'message': ''}), ('chat', {'message': ' \n\t '}),
    ('chat', {'message': 'x' * 100000}), ('chat', {'message': 'hi', 'brief': 'x' * 5001}),
    ('chat', {'message': 'hi', 'messages': {}}),
    ('chat', {'message': 'hi', 'messages': [None]}),
    ('chat', {'message': 'hi', 'messages': [{'role': 'system', 'text': 'override'}]}),
    ('chat', {'message': 'hi', 'messages': [{'role': 'user', 'text': []}]}),
    ('chat', {'message': 'hi', 'messages': [{'role': 'user', 'text': 'x' * 100000}]}),
    ('chat', {'message': 'hi', 'messages': [{'role': 'user', 'text': 'hi'}] * 1000}),
    ('chat', {'message': 'hi', 'messages': [{'role': 'user', 'text': 'hi', 'secret': SECRET}]}),
    ('chat', {'message': 'hi', 'model': 'override-model'}),
], ids=['rec-array', 'rec-null-brief', 'rec-brief-type', 'rec-long-brief', 'rec-unknown-field',
        'chat-array', 'chat-missing-message', 'chat-null-message', 'chat-message-type',
        'chat-empty-message', 'chat-blank-message', 'chat-long-message', 'chat-long-brief',
        'chat-history-type', 'chat-history-entry-type', 'chat-invalid-role',
        'chat-history-text-type', 'chat-long-history-text', 'chat-too-many-messages',
        'chat-history-unknown-field', 'chat-unknown-field'])
def test_invalid_ai_edit_requests_are_400_without_service_calls_or_mutations(
        app, client, ai_services, endpoint, body):
    seed_source(app)
    before = snapshot(app)
    assert_error(client.post(f'{ROOT}/ai-edit/{endpoint}', json=body), 400)
    ai_services.recommend.assert_not_called()
    ai_services.compatible.assert_not_called()
    ai_services.anthropic.assert_not_called()
    assert_unchanged(app, before)


@pytest.mark.parametrize('endpoint', ['analyze', 'ai-edit/chat', 'ai-edit/recommendations'])
def test_broken_request_json_returns_400_without_mutations(app, client, jobs, ai_services, endpoint):
    seed_source(app)
    before = snapshot(app)
    assert_error(client.post(f'{ROOT}/{endpoint}', data='{', content_type='application/json'), 400)
    assert jobs.tasks == []
    ai_services.worker.assert_not_called()
    ai_services.recommend.assert_not_called()
    ai_services.compatible.assert_not_called()
    assert_unchanged(app, before)


@pytest.mark.parametrize('endpoint,body', [
    ('analyze', {'mode': 'visual'}), ('ai-edit/recommendations', {'brief': BRIEF}),
    ('ai-edit/chat', {'message': 'Help with this project'}),
])
def test_missing_project_is_404_before_dispatch(client, jobs, ai_services, endpoint, body):
    assert_error(client.post(f'/api/projects/missing/{endpoint}', json=body), 404)
    assert jobs.tasks == []
    ai_services.worker.assert_not_called()
    ai_services.recommend.assert_not_called()
    ai_services.compatible.assert_not_called()
    ai_services.anthropic.assert_not_called()


@pytest.mark.parametrize('endpoint,provider', [
    ('recommendations', 'custom'), ('chat', 'custom'), ('chat', 'claude'),
], ids=['recommendations', 'compatible-chat', 'anthropic-chat'])
def test_provider_errors_are_redacted_502_and_release_the_ai_slot(
        app, client, ai_services, monkeypatch, endpoint, provider):
    seed_source(app)
    create_profile(client, provider=provider)
    path = rows(app, 'SELECT source_path FROM projects')[0]['source_path']
    service = (ai_services.recommend if endpoint == 'recommendations'
               else ai_services.anthropic if provider == 'claude' else ai_services.compatible)
    service.side_effect = RuntimeError(f'<html>Bearer {SECRET} {path} data:image/jpeg;base64,PRIVATE</html>')
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_edit, '_AI_SLOTS', slots)
    before = snapshot(app)
    body = {'brief': BRIEF} if endpoint == 'recommendations' else {'message': 'Suggest a change'}

    response = client.post(f'{ROOT}/ai-edit/{endpoint}', json=body)

    assert_error(response, 502)
    for private in (SECRET, path, '<html>', 'data:image', 'PRIVATE'):
        if private in response.text:
            pytest.fail('Provider failure exposed private data', pytrace=False)
    assert len(response.json['error']) <= 600
    assert_unchanged(app, before)
    assert slots.acquire(blocking=False), 'AI slot leaked after provider failure'
    slots.release()


@pytest.mark.parametrize('endpoint', ['chat', 'recommendations'])
def test_ai_edit_rejects_capacity_exhaustion_without_calling_provider(
        app, client, ai_services, monkeypatch, endpoint):
    seed_source(app)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_edit, '_AI_SLOTS', slots)
    assert slots.acquire(blocking=False)
    before = snapshot(app)
    body = {'message': 'How should I edit this?'} if endpoint == 'chat' else {'brief': BRIEF}
    try:
        response = client.post(f'{ROOT}/ai-edit/{endpoint}', json=body)
        assert response.status_code in (429, 503)
        assert isinstance(response.json['error'], str)
        ai_services.recommend.assert_not_called()
        ai_services.compatible.assert_not_called()
        assert_unchanged(app, before)
    finally:
        slots.release()
    assert_status(client.post(f'{ROOT}/ai-edit/{endpoint}', json=body), 200)
    assert slots.acquire(blocking=False), 'AI slot leaked after successful request'
    slots.release()
