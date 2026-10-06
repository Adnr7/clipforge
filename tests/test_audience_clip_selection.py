"""Audience-first decisions use real timing, evidence and independent review."""

import copy
import json
from unittest.mock import Mock

import pytest

from backend.services import clip_selection_service as selection, llm_service
from tests.test_pipeline_routes import app, client, execute, jobs, rows, seed_project


AUDIENCE = {'audience': 'First-time founders', 'goal': 'Make a clear customer pitch',
            'notes': 'Practical advice; avoid sponsor reads and celebrity gossip.'}
CORPUS = {'words': [], 'segments': [
    {'start': 0, 'end': 20, 'text': 'Welcome to the show. Today we have several stories to tell.'},
    {'start': 20, 'end': 40, 'text': 'Our sponsor has a special offer. Use discount code FIVE to get it.'},
    {'start': 40, 'end': 65, 'text': 'Lead with the customer problem. Show one example of the result they can achieve.'},
    {'start': 65, 'end': 90, 'text': 'A famous actor surprised the crowd. Everyone cheered at the unexpected announcement.'},
]}
GOOD = {'audienceFit': 5, 'hook': 4, 'payoff': 5, 'clarity': 4, 'shareability': 3}


def draft(unit=2, assessment=None, **overrides):
    text = CORPUS['segments'][unit]['text']
    sentences = text.split('. ')
    return {'startUnit': unit, 'endUnit': unit,
            'openingQuote': sentences[0], 'closingQuote': sentences[-1],
            'hook': 'Lead with the customer problem', 'topic': f'idea-{unit}',
            'audienceReason': 'A founder can use this to explain their product clearly.',
            'rationale': 'The opening identifies a problem and the ending delivers a practical action.',
            'assessment': assessment or copy.deepcopy(GOOD), **overrides}


def editorial_model(prompt, _maximum):
    if 'ROLE: evidence scout' in prompt:
        return json.dumps({'audience': AUDIENCE['audience'], 'candidates': [
            draft(1, {key: 5 for key in GOOD}),
            draft(3, {**GOOD, 'audienceFit': 1}, score=100),
            draft(2, score='not a numeric score', start=999, end=1000),
        ]})
    context = json.loads(prompt.split('\nContext: ')[1])
    return json.dumps({'reviews': [
        {'candidateId': candidate['candidateId'], 'selfContained': candidate['start'] == 40,
         'assessment': GOOD if candidate['start'] == 40 else {**GOOD, 'audienceFit': 1},
         'audienceReason': 'The complete example helps a first-time founder make a customer pitch.'}
        for candidate in context['candidates']
    ]})


def test_audience_and_payoff_outweigh_famous_or_high_scored_irrelevant_material():
    model = Mock(side_effect=editorial_model)
    clips = selection.analyze_timed_transcript(CORPUS, 90, 'Keep complete ideas', AUDIENCE, model)
    assert [(clip['start'], clip['end'], clip['score']) for clip in clips] == [(40, 65, 89)]
    assert clips[0]['selection']['audience'] == 'First-time founders'
    assert clips[0]['selection']['evidence']['openingQuote'] == 'Lead with the customer problem'
    assert not clips[0]['selection']['audienceInferred']
    assert model.call_count == 2
    scout, critic = [call.args[0] for call in model.call_args_list]
    assert AUDIENCE['goal'] in scout and AUDIENCE['notes'] in scout
    assert 'discount code' in critic and 'customer problem' in critic
    assert 'famous actor' not in critic  # Fails the local audience gate before review.


@pytest.mark.parametrize('change', [
    {'startUnit': 999}, {'endUnit': True}, {'openingQuote': 'This quote never appears in the video'},
    {'closingQuote': 'A fabricated promise for a viral ending'},
    {'assessment': {**GOOD, 'audienceFit': '5'}}, {'assessment': {**GOOD, 'clarity': float('nan')}},
])
def test_hallucinated_boundaries_quotes_and_rubrics_fail_before_review(change):
    model = Mock(return_value=json.dumps({'audience': 'Founders', 'candidates': [draft(**change)]}))
    with pytest.raises(ValueError):
        selection.analyze_timed_transcript(CORPUS, 90, '', AUDIENCE, model)
    assert model.call_count == 1


def test_critic_can_return_no_clips_instead_of_filling_a_quota():
    model = Mock(side_effect=[json.dumps({'audience': 'Founders', 'candidates': [draft()]}),
                             json.dumps({'reviews': []})])
    assert selection.analyze_timed_transcript(CORPUS, 90, '', AUDIENCE, model) == []


def test_quote_verification_does_not_treat_cpp_and_csharp_as_the_same_source():
    corpus = copy.deepcopy(CORPUS)
    corpus['segments'][2]['text'] = 'Teach C++ pointer lifetimes. Pair each allocation with ownership.'
    model = Mock(return_value=json.dumps({'audience': 'Developers', 'candidates': [draft(
        openingQuote='Teach C# pointer lifetimes', closingQuote='Pair each allocation with ownership.')] }))
    with pytest.raises(ValueError, match='actual opening'):
        selection.analyze_timed_transcript(corpus, 90, '', AUDIENCE, model)
    assert model.call_count == 1


def test_inferred_audience_is_labeled_and_provider_errors_do_not_expose_connection_data(monkeypatch):
    model = Mock(side_effect=[json.dumps({'audience': 'Founders', 'candidates': [draft()]}),
                             json.dumps({'reviews': [{'candidateId': 0, 'selfContained': True,
                               'assessment': GOOD, 'audienceReason': 'Useful pitch guidance for a founder.'}]})])
    assert selection.analyze_timed_transcript(CORPUS, 90, '', {}, model)[0]['selection']['audienceInferred']
    secret = 'fixture-arbitrary-provider-secret'
    monkeypatch.setattr(llm_service, 'call_openai_compatible', Mock(side_effect=RuntimeError(secret)))
    with pytest.raises(RuntimeError) as error:
        llm_service.analyze_transcript('speech', 90, 'custom', profile={
            'model': 'editor', 'base_url': 'https://editor.example.test/v1', 'api_key': secret},
            transcript_data=CORPUS, audience_brief=AUDIENCE)
    assert secret not in str(error.value)
    assert error.value.__suppress_context__


def test_analysis_budget_is_checked_before_requests(monkeypatch):
    monkeypatch.setattr(selection, 'WINDOW_CHARS', 350)
    transcript = {'segments': [{'start': index * 20, 'end': (index + 1) * 20,
                               'text': f'Complete idea {index}. ' + 'evidence ' * 20} for index in range(40)]}
    model = Mock()
    with pytest.raises(ValueError, match='analysis budget'):
        selection.analyze_timed_transcript(transcript, 800, '', AUDIENCE, model)
    model.assert_not_called()


def test_expired_analysis_budget_starts_no_provider_request(monkeypatch):
    monkeypatch.setattr(selection.time, 'monotonic', Mock(side_effect=[0, 1201]))
    model = Mock()
    with pytest.raises(ValueError, match='time budget'):
        selection.analyze_timed_transcript(CORPUS, 90, '', AUDIENCE, model)
    model.assert_not_called()


def test_critic_requests_are_batched_without_dropping_finalists(monkeypatch):
    monkeypatch.setattr(selection, 'MAX_REVIEW_CHARS', 450)
    calls = []

    def model(prompt, _maximum):
        calls.append(prompt)
        if 'ROLE: evidence scout' in prompt:
            return json.dumps({'audience': 'Founders', 'candidates': [draft(0), draft(2)]})
        context = json.loads(prompt.split('\nContext: ')[1])
        assert len(context['candidates']) == 1
        return json.dumps({'reviews': [{'candidateId': candidate['candidateId'], 'selfContained': True,
          'assessment': GOOD, 'audienceReason': 'The complete idea fits this viewer.'} for candidate in context['candidates']]})

    clips = selection.analyze_timed_transcript(CORPUS, 90, '', AUDIENCE, model)
    assert {(clip['start'], clip['end']) for clip in clips} == {(0, 20), (40, 65)}
    assert len(calls) == 3


@pytest.mark.parametrize('review', [
    {'candidateId': 40, 'selfContained': True, 'assessment': GOOD, 'audienceReason': 'Useful advice'},
    {'candidateId': False, 'selfContained': True, 'assessment': GOOD, 'audienceReason': 'Useful advice'},
    {'candidateId': 0, 'selfContained': 'yes', 'assessment': GOOD, 'audienceReason': 'Useful advice'},
])
def test_critic_cannot_introduce_candidates_or_fake_a_boolean_judgment(review):
    model = Mock(side_effect=[json.dumps({'audience': 'Founders', 'candidates': [draft()]}),
                             json.dumps({'reviews': [review]})])
    with pytest.raises(ValueError):
        selection.analyze_timed_transcript(CORPUS, 90, '', AUDIENCE, model)


def test_word_timing_is_grouped_without_interpolating_a_segment_duration():
    transcript = {'segments': [{'start': 0, 'end': 90, 'text': 'Lead with the problem. Then show a result.'}],
                  'words': [{'start': 10, 'end': 11, 'text': 'Lead'},
                            {'start': 11, 'end': 12, 'text': 'with'},
                            {'start': 12, 'end': 13, 'text': 'the'},
                            {'start': 13, 'end': 14, 'text': 'problem.'},
                            {'start': 30, 'end': 31, 'text': 'Then'},
                            {'start': 31, 'end': 32, 'text': 'show'},
                            {'start': 32, 'end': 33, 'text': 'a'},
                            {'start': 33, 'end': 34, 'text': 'result.'}]}
    assert selection.timed_units(transcript, 90) == [
        {'id': 0, 'start': 10, 'end': 14, 'text': 'Lead with the problem.'},
        {'id': 1, 'start': 30, 'end': 34, 'text': 'Then show a result.'},
    ]


def test_complete_short_words_are_not_mistaken_for_a_partial_word_array():
    texts = ['I', 'am', 'a', 'new', 'CEO', 'and', 'we', 'can', 'do', 'it'] * 3
    words = [{'start': index * 2, 'end': index * 2 + 1, 'text': text} for index, text in enumerate(texts)]
    transcript = {'words': words, 'segments': [{'start': 0, 'end': 90, 'text': ' '.join(texts)}]}
    units = selection.timed_units(transcript, 90)
    assert len(units) > 1
    assert units[-1]['end'] == 59
    assert all(unit['end'] - unit['start'] <= 9 for unit in units)


def test_windowing_covers_late_content_and_overlaps_boundaries(monkeypatch):
    monkeypatch.setattr(selection, 'WINDOW_CHARS', 1000)
    units = [{'id': index, 'start': index * 20, 'end': (index + 1) * 20,
              'text': f'Idea {index}. ' + 'evidence ' * 20} for index in range(20)]
    windows = selection.transcript_windows(units)
    assert len(windows) > 1
    assert {unit['id'] for window in windows for unit in window} == set(range(20))
    assert windows[-1][-1]['id'] == 19
    assert all(left[-1]['id'] >= right[0]['id'] for left, right in zip(windows, windows[1:]))


@pytest.mark.parametrize('transcript', [
    {'segments': [{'text': 'No timing'}]},
    {'segments': [{'start': 0, 'end': float('inf'), 'text': 'Invalid timing'}]},
    {'segments': [{'start': True, 'end': 20, 'text': 'Boolean timing'}]},
])
def test_bad_source_timing_makes_no_provider_request(transcript):
    model = Mock()
    with pytest.raises(ValueError):
        selection.analyze_timed_transcript(transcript, 90, '', AUDIENCE, model)
    model.assert_not_called()


@pytest.mark.parametrize('provider', ['custom', 'claude', 'ollama'])
def test_supported_providers_receive_the_same_audience_method_and_frozen_connection(monkeypatch, provider):
    profile = {'provider': provider, 'model': 'selected-editor-model',
               'base_url': 'https://editor.example.test/v1', 'api_key': 'fixture-profile-key'}
    calls = []

    def call(prompt, *args, **options):
        calls.append((prompt, options))
        return editorial_model(prompt, options['max_tokens'])

    monkeypatch.setattr(llm_service, 'call_openai_compatible', call)
    monkeypatch.setattr(llm_service, 'call_anthropic', call)
    result = llm_service.analyze_transcript('plain text is not used for boundary guesses', 90,
        provider, profile=profile, transcript_data=CORPUS, audience_brief=AUDIENCE)
    assert [(candidate['start'], candidate['end']) for candidate in result] == [(40, 65)]
    assert len(calls) == 2
    assert all(options['profile']['model'] == 'selected-editor-model' for _, options in calls)
    assert all(options['profile']['api_key'] == 'fixture-profile-key' for _, options in calls)
    assert all(options['retries'] == 1 and options['timeout'] == (5, 60) for _, options in calls)


def configure_source(app):
    seed_project(app)
    execute(app, 'UPDATE projects SET source_duration=90 WHERE id="project"')
    execute(app, 'UPDATE transcripts SET raw_json=? WHERE project_id="project"', (json.dumps(CORPUS),))


def test_button_job_persists_audience_assessment_and_source_evidence(app, client, jobs, monkeypatch):
    configure_source(app)
    model = Mock(side_effect=lambda prompt, *args, **options: editorial_model(prompt, options['max_tokens']))
    monkeypatch.setattr(llm_service, 'call_openai_compatible', model)
    response = client.post('/api/projects/project/analyze', json={'mode': 'transcript', 'audienceBrief': AUDIENCE})
    assert response.status_code == 202
    model.assert_not_called()  # Dispatch is asynchronous, not a form-change side effect.
    jobs.run_all()
    detail = client.get('/api/projects/project').json
    assert detail['jobs']['analysis']['status'] == 'done'
    assert [(clip['start_sec'], clip['end_sec']) for clip in detail['candidates']] == [(40, 65)]
    assert detail['candidates'][0]['selection']['assessment']['audienceFit'] == 5
    assert detail['candidates'][0]['selection']['evidence']['basis'] == 'transcript'
    assert 'selection_json' not in detail['candidates'][0]


def test_failed_evidence_review_preserves_existing_selection_and_artifacts(app, client, jobs, monkeypatch):
    configure_source(app)
    execute(app, 'INSERT INTO clips (id,candidate_id,status,output_path) VALUES (?,?,?,?)',
            ('existing-render', 'candidate-project-0', 'done', '/synthetic/clip.mp4'))
    before = rows(app, 'SELECT * FROM candidates'), rows(app, 'SELECT * FROM clips')
    monkeypatch.setattr(llm_service, 'call_openai_compatible', Mock(return_value=json.dumps({
        'audience': 'Founders', 'candidates': [draft(openingQuote='Made up evidence nowhere in source')]})))
    assert client.post('/api/projects/project/analyze', json={'audienceBrief': AUDIENCE}).status_code == 202
    jobs.run_all()
    assert (rows(app, 'SELECT * FROM candidates'), rows(app, 'SELECT * FROM clips')) == before
    assert client.get('/api/projects/project/analyze/status').json['status'] == 'error'


@pytest.mark.parametrize('audience', [[], {'audience': []}, {'audience': 'x' * 1001},
                                     {'goal': 'bad\x00goal'}, {'provider': 'another-account'}])
def test_invalid_audience_contract_does_not_dispatch(app, client, jobs, audience):
    configure_source(app)
    body = {'audienceBrief': audience}
    assert client.post('/api/projects/project/analyze', json=body).status_code == 400
    assert jobs.tasks == []
