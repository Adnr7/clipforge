import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.transcription_service import (
    InvalidDeepgramResponse, normalize_deepgram, normalize_whisper, transcribe_deepgram,
)


class TestNormalizeDeepgram:
    def test_nested_words_are_sorted_punctuated_and_grouped(self):
        data = {
            'results': {'channels': [
                {'detected_language': 'fr', 'alternatives': [{'words': [
                    {'word': 'later', 'start': 5, 'end': 6, 'speaker': 1},
                    {'word': 'hello', 'punctuated_word': 'Hello,', 'start': 0, 'end': 1, 'speaker': 0},
                    {'word': 'again', 'start': 3, 'end': 3.5, 'speaker': 0},
                ]}]},
                {'alternatives': [{'words': [
                    {'word': 'world', 'start': 1, 'end': 1.5, 'speaker': 0},
                ]}]},
            ]},
        }
        result = normalize_deepgram(data)
        assert result['language'] == 'fr'
        assert result['duration'] == 6
        assert [word['start'] for word in result['words']] == [0, 1, 3, 5]
        assert [segment['text'] for segment in result['segments']] == ['Hello, world', 'again', 'later']
        assert result['speakers'] == ['Speaker 1', 'Speaker 2']

    def test_overlapping_words_do_not_shrink_segment_end(self):
        data = {'results': {'channels': [{'alternatives': [{'words': [
            {'word': 'long', 'start': 0, 'end': 3},
            {'word': 'short', 'start': 1, 'end': 2},
            {'word': 'next', 'start': 4, 'end': 5},
        ]}]}]}}
        result = normalize_deepgram(data)
        assert len(result['segments']) == 1
        assert result['segments'][0]['end'] == 5

    def test_ignores_invalid_words(self):
        data = {'channels': [{'words': [
            {'word': 'bad', 'start': 'NaN', 'end': 1},
            {'word': 'backwards', 'start': 2, 'end': 1},
            {'word': '', 'start': 0, 'end': 1},
            {'word': 'valid', 'start': '1', 'end': '2'},
        ]}]}
        assert [word['text'] for word in normalize_deepgram(data)['words']] == ['valid']

    def test_normalize_deepgram_basic(self):
        data = {
            "metadata": {"duration": 120.5, "language": "en"},
            "channels": [
                {
                    "channel": 0,
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 1.0},
                        {"word": "world", "start": 1.0, "end": 2.0},
                    ]
                }
            ]
        }
        result = normalize_deepgram(data)
        assert result['language'] == 'en'
        assert result['duration'] == 120.5
        assert len(result['words']) == 2
        assert result['words'][0]['text'] == 'Hello'
        assert len(result['speakers']) == 1

    def test_normalize_deepgram_multi_speaker(self):
        data = {
            "metadata": {"duration": 60.0, "language": "en"},
            "channels": [
                {
                    "channel": 0,
                    "words": [{"word": "Hi", "start": 0.0, "end": 0.5}]
                },
                {
                    "channel": 1,
                    "words": [{"word": "There", "start": 0.5, "end": 1.0}]
                }
            ]
        }
        result = normalize_deepgram(data)
        assert len(result['speakers']) == 2
        assert result['speakers'] == ['Speaker 1', 'Speaker 2']


class TestNormalizeWhisper:
    def test_real_whisper_shape_infers_duration_and_sorts(self):
        result = normalize_whisper({'language': 'en', 'segments': [
            {'start': 2, 'end': 3, 'text': ' later', 'words': [{'word': ' later', 'start': 2, 'end': 3}]},
            {'start': 0, 'end': 1, 'text': ' first', 'words': [{'word': ' first', 'start': 0, 'end': 1}]},
        ]})
        assert result['duration'] == 3
        assert [word['text'] for word in result['words']] == ['first', 'later']
        assert [segment['start'] for segment in result['segments']] == [0, 2]

    def test_normalize_whisper_basic(self):
        data = {
            "language": "en",
            "duration": 45.0,
            "segments": [
                {
                    "start": 0.0, "end": 5.0, "text": "Hello world",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 2.0},
                        {"word": "world", "start": 2.0, "end": 4.0},
                    ]
                }
            ]
        }
        result = normalize_whisper(data)
        assert result['language'] == 'en'
        assert result['duration'] == 45.0
        assert len(result['segments']) == 1
        assert len(result['words']) == 2


def test_deepgram_request_has_bounded_timeout(tmp_path):
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'audio')
    with patch('backend.services.transcription_service.requests.post') as post:
        post.return_value.json.return_value = {'results': {'channels': [
            {'alternatives': [{'transcript': '', 'words': []}]},
        ]}}
        transcribe_deepgram(str(audio), 'test-key')
        timeout = post.call_args.kwargs['timeout']
        assert all(0 < value <= 600 for value in (timeout if isinstance(timeout, tuple) else [timeout]))
        post.return_value.raise_for_status.assert_called_once()


@pytest.mark.parametrize('payload', [
    None, [], {}, {'error': 'failed'}, {'err_code': 'BAD_REQUEST', 'err_msg': 'failed'},
    {'results': None}, {'results': {}}, {'results': {'channels': []}},
    {'results': {'channels': {}}}, {'results': {'channels': [None]}},
    {'results': {'channels': [{'alternatives': []}]}},
    {'results': {'channels': [{'alternatives': [None]}]}},
    {'results': {'channels': [{'alternatives': [{}]}]}},
    {'results': {'channels': [{'alternatives': [{'words': None}]}]}},
    {'results': {'channels': [{'alternatives': [{'words': {}, 'transcript': ''}]}]}},
    {'results': {'channels': [{'alternatives': [{'words': [], 'transcript': 'Lost speech'}]}]}},
    {'results': {'channels': [{'alternatives': [{'words': []}]}]}},
    {'results': {'channels': [{'alternatives': [{'words': [], 'transcript': None}]}]}},
    {'metadata': None, 'results': {'channels': [{'alternatives': [{'words': []}]}]}},
    {'channels': [{'words': []}]},  # The live API must use the real response envelope.
])
def test_deepgram_rejects_malformed_http_success(tmp_path, payload):
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'audio')
    with patch('backend.services.transcription_service.requests.post') as post:
        post.return_value.json.return_value = payload
        with pytest.raises(InvalidDeepgramResponse, match='invalid transcription response'):
            transcribe_deepgram(str(audio), 'test-key')


@pytest.mark.parametrize('word', [
    None, 'token', {}, {'word': 'speech'},
    {'word': 'speech', 'start': float('nan'), 'end': 1},
    {'word': 'speech', 'start': 0, 'end': float('inf')},
    {'word': 'speech', 'start': 2, 'end': 1},
    {'word': 'speech', 'start': -1, 'end': 1},
    {'word': 'speech', 'start': False, 'end': 1},
    {'word': 'speech', 'start': 0, 'end': True},
    {'word': ' ', 'start': 0, 'end': 1},
])
def test_deepgram_live_response_rejects_invalid_tokens_even_with_valid_speech(tmp_path, word):
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'audio')
    payload = {'results': {'channels': [{'alternatives': [{
        'transcript': 'Some speech', 'words': [
            {'word': 'Some', 'start': 0, 'end': 1}, word,
        ],
    }]}]}}
    with patch('backend.services.transcription_service.requests.post') as post:
        post.return_value.json.return_value = payload
        with pytest.raises(InvalidDeepgramResponse):
            transcribe_deepgram(str(audio), 'test-key')


def test_deepgram_invalid_json_has_distinct_error_without_response_body(tmp_path):
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'audio')
    with patch('backend.services.transcription_service.requests.post') as post:
        post.return_value.json.side_effect = ValueError('private provider response body')
        with pytest.raises(InvalidDeepgramResponse, match='invalid transcription response') as error:
            transcribe_deepgram(str(audio), 'test-key')
    assert 'private' not in str(error.value)


def test_deepgram_accepts_real_silence_response(tmp_path):
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'audio')
    payload = {'metadata': {'duration': 12.5}, 'results': {'channels': [
        {'alternatives': [{'transcript': '', 'confidence': 0.0, 'words': []}]},
    ]}}
    with patch('backend.services.transcription_service.requests.post') as post:
        post.return_value.json.return_value = payload
        result = transcribe_deepgram(str(audio), 'test-key')
    assert result['words'] == []
    assert result['segments'] == []
    assert result['duration'] == 12.5
