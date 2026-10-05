import os
import sys
import json
import pytest
import requests
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.llm_service import (
    PROVIDERS, parse_candidates, check_connection, analyze_transcript,
    call_openai_compatible, call_anthropic
)


class TestParseCandidates:
    @pytest.mark.parametrize('key', ['candidates', 'moments', 'clips', 'results', 'highlights'])
    def test_all_object_wrappers(self, key):
        result = parse_candidates(json.dumps({key: [{'start': '1.5', 'end': '20', 'score': '80'}]}))
        assert result == [{'start': 1.5, 'end': 20.0, 'score': 80.0, 'hook': '', 'rationale': ''}]

    def test_fallback_ignores_non_list_preferred_key(self):
        result = parse_candidates('{"candidates": null, "highlights": [{"start": 1, "end": 20, "score": 80}]}')
        assert len(result) == 1

    @pytest.mark.parametrize('candidate', [
        None, 'clip', [], {}, {'start': 0, 'end': 10},
        {'start': -1, 'end': 20, 'score': 50},
        {'start': 10, 'end': 10, 'score': 50},
        {'start': 'NaN', 'end': 20, 'score': 50},
        {'start': 0, 'end': 'Infinity', 'score': 50},
        {'start': True, 'end': 20, 'score': 50},
        {'start': 0, 'end': 20, 'score': 101},
        {'start': 0, 'end': 20, 'score': 50, 'hook': []},
        {'start': 0, 'end': 20, 'score': 50, 'rationale': None},
    ])
    def test_rejects_malformed_candidates_with_useful_error(self, candidate):
        with pytest.raises(ValueError, match='[Cc]andidate'):
            parse_candidates(json.dumps([candidate]))

    def test_parse_json_array(self):
        result = parse_candidates(
            '[{"start": 10.0, "end": 30.0, "score": 85, "hook": "Test", "rationale": "Rationale"}]'
        )
        assert len(result) == 1
        assert result[0]['score'] == 85
        assert result[0]['hook'] == 'Test'

    def test_parse_wrapped_object(self):
        result = parse_candidates(
            '{"candidates": [{"start": 5.0, "end": 20.0, "score": 70, "hook": "Wrapped", "rationale": "Test"}]}'
        )
        assert len(result) == 1

    def test_parse_code_block(self):
        result = parse_candidates(
            '```json\n[{"start": 0.0, "end": 15.0, "score": 90, "hook": "Code block", "rationale": "Test"}]\n```'
        )
        assert len(result) == 1

    def test_parse_invalid_raises(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            parse_candidates('not valid json')

    def test_parse_non_list_raises(self):
        with pytest.raises(ValueError, match="valid JSON array"):
            parse_candidates('{"not": "a list"}')


class TestTestConnection:
    @patch('backend.services.llm_service.call_openai_compatible')
    def test_test_connection_success(self, mock_call):
        mock_call.return_value = 'ok'
        result = check_connection('deepseek')
        assert result['ok'] is True
        assert result['message'] == 'Connected'

    @patch('backend.services.llm_service.call_anthropic')
    def test_test_connection_claude(self, mock_call):
        mock_call.return_value = 'ok'
        result = check_connection('claude')
        assert result['ok'] is True

    @patch('backend.services.llm_service.call_openai_compatible')
    def test_test_connection_failure(self, mock_call):
        mock_call.side_effect = Exception('Connection refused')
        result = check_connection('deepseek')
        assert result['ok'] is False
        assert 'Connection refused' in result['message']


def test_connection_requests_json_object():
    with patch('backend.services.llm_service.call_openai_compatible', return_value='{"ok": true}') as call:
        assert check_connection('openai')['ok'] is True
        assert 'json' in call.call_args.args[0].lower()


def test_analysis_requests_object_and_rejects_out_of_bounds():
    with patch('backend.services.llm_service.call_openai_compatible', return_value='{"candidates": [{"start": 0, "end": 31, "score": 80}]}') as call:
        with pytest.raises(ValueError, match='duration'):
            analyze_transcript('transcript', 30, 'openai')
        assert 'JSON object' in call.call_args.args[0]
        assert '"candidates"' in call.call_args.args[0]


@pytest.mark.parametrize('provider,adapter', [
    ('openai', 'call_openai_compatible'), ('claude', 'call_anthropic'),
])
def test_analysis_preserves_full_length_editing_brief(provider, adapter):
    brief = 'a' * 4980 + ' FINAL PREFERENCE!!!'
    with patch(f'backend.services.llm_service.{adapter}', return_value='{"candidates": []}') as call:
        assert analyze_transcript('speech', 60, provider, brief=brief) == []
        assert call.call_args.args[0].endswith(brief)


@pytest.mark.parametrize('brief', [None, 'x' * 5001, 'bad\x00brief'])
def test_analysis_rejects_invalid_editing_brief_before_provider_call(brief):
    with patch('backend.services.llm_service.call_openai_compatible') as call:
        with pytest.raises(ValueError, match='brief'):
            analyze_transcript('speech', 60, 'openai', brief=brief)
        call.assert_not_called()


def test_ollama_does_not_require_environment_key():
    with patch('backend.services.llm_service.requests.post') as post:
        post.return_value.json.return_value = {'choices': [{'message': {'content': '{"candidates": []}'}}]}
        assert call_openai_compatible('Return JSON', 'ollama') == '{"candidates": []}'
        assert 'Authorization' not in post.call_args.kwargs['headers']


@pytest.mark.parametrize('profile,expected', [
    (None, 'qwen2.5:7b'), ({'model': 'llama3.3:latest'}, 'llama3.3:latest'),
])
def test_ollama_model_respects_environment_and_profile_override(monkeypatch, profile, expected):
    monkeypatch.setenv('OLLAMA_MODEL', 'qwen2.5:7b')
    with patch('backend.services.llm_service.requests.post') as post:
        post.return_value.json.return_value = {'choices': [{'message': {'content': '{}'}}]}
        assert call_openai_compatible('Return JSON', 'ollama', profile=profile) == '{}'
        assert post.call_args.kwargs['json']['model'] == expected


@pytest.mark.parametrize('provider', ['unknown', 'custom'])
def test_invalid_provider_configuration_fails_before_request(provider, monkeypatch):
    monkeypatch.delenv('CUSTOM_BASE_URL', raising=False)
    monkeypatch.delenv('CUSTOM_MODEL', raising=False)
    with patch('backend.services.llm_service.requests.post') as post:
        with pytest.raises(ValueError):
            call_openai_compatible('Return JSON', provider)
        post.assert_not_called()


def test_provider_auth_error_is_clear_and_not_retried():
    response = requests.Response()
    response.status_code = 401
    response._content = b'{"error": {"message": "Invalid API key"}}'
    with patch('backend.services.llm_service.requests.post', return_value=response) as post, patch('backend.services.llm_service.time.sleep'):
        with pytest.raises(RuntimeError, match='openai.*401.*Invalid API key'):
            call_openai_compatible('Return JSON', 'openai')
        assert post.call_count == 1


def test_transient_provider_error_is_retried():
    response = MagicMock()
    response.json.return_value = {'choices': [{'message': {'content': '{}'}}]}
    with patch('backend.services.llm_service.requests.post', side_effect=[requests.Timeout('slow'), response]) as post, patch('backend.services.llm_service.time.sleep'):
        assert call_openai_compatible('Return JSON', 'openai') == '{}'
        assert post.call_count == 2


@pytest.mark.parametrize('payload', [{'error': {'message': 'Quota exhausted'}}, {'choices': []}, {'choices': [{'message': {'content': None, 'refusal': 'Refused'}}]}])
def test_provider_payload_errors_are_useful(payload):
    with patch('backend.services.llm_service.requests.post') as post, patch('backend.services.llm_service.time.sleep'):
        post.return_value.json.return_value = payload
        with pytest.raises(RuntimeError, match='openai'):
            call_openai_compatible('Return JSON', 'openai')
        assert post.call_count == 1


def test_anthropic_joins_text_blocks_and_ignores_thinking():
    with patch('backend.services.llm_service.requests.post') as post:
        post.return_value.json.return_value = {'content': [
            {'type': 'thinking', 'thinking': 'reasoning'},
            {'type': 'text', 'text': '{"candidates":'},
            {'type': 'text', 'text': ' []}'},
        ]}
        assert call_anthropic('Return JSON') == '{"candidates": []}'
