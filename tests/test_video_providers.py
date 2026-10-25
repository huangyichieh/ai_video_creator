import json

import httpx
import pytest
from openai import OpenAI
from PIL import Image

from story_channel.llm import ProviderError, TransientProviderError
from story_channel.models import ProviderConfig
from story_channel.providers import VideoProvider
from story_channel.video_providers import OpenAIVideoProvider


def config(tmp_path, **extra):
    return ProviderConfig(provider='openai', model='sora-2', options={
        'output_path': str(tmp_path/'clip.mp4'), 'seconds': 8, 'size': '1280x720',
        'poll_interval': 0, **extra})


def job(status='queued', **extra):
    return {'id': 'video_test', 'object': 'video', 'model': 'sora-2', 'status': status,
            'progress': 100 if status == 'completed' else 20, 'created_at': 1,
            'seconds': '8', 'size': '1280x720', **extra}


def test_video_sdk_image_submit_poll_download_and_resume(tmp_path):
    image = tmp_path/'reference.png'
    Image.new('RGB', (160,90), 'red').save(image)
    requests, updates = [], []

    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            assert b'input_reference' in request.content
            return httpx.Response(200, json=job())
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'mock-video-bytes', headers={'content-type':'video/mp4'})
        return httpx.Response(200, json=job('completed'))

    with OpenAI(api_key='test-only', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        provider = OpenAIVideoProvider(client)
        assert isinstance(provider, VideoProvider)
        provider.generate_clip('Gentle camera move', [image], config(tmp_path, job_update=lambda *args: updates.append(args)))
        assert (tmp_path/'clip.mp4').read_bytes() == b'mock-video-bytes'
        assert provider.last_usage['video_seconds'] == 8
        assert updates[0] == ('video_test', 'queued', 20)
        provider.generate_clip('same', [], config(tmp_path, job_id='video_test'))
        assert provider.last_usage['video_seconds'] == 0
        assert sum(r.method == 'POST' for r in requests) == 1


def test_video_text_timeout_persists_job_and_failed_job_usage(tmp_path):
    updates, requests = [], []
    status = 'queued'
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=job(status))
    with OpenAI(api_key='test-only', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        provider = OpenAIVideoProvider(client)
        with pytest.raises(TransientProviderError, match='resume the saved job'):
            provider.generate_clip('Text-only', [], config(tmp_path, poll_timeout=0, job_update=lambda *args: updates.append(args)))
        assert provider.last_usage['video_seconds'] == 8
        assert b'Text-only' in requests[0].content
        assert b'input_reference' not in requests[0].content
        assert updates[0][0] == 'video_test'
        status = 'failed'
        with pytest.raises(ProviderError, match='video generation failed'):
            provider.generate_clip('same', [], config(tmp_path, job_id='video_test'))
        assert provider.last_usage['video_seconds'] == 0
        assert sum(r.method == 'POST' for r in requests) == 1


def test_rejected_video_request_records_no_generated_seconds(tmp_path):
    with OpenAI(api_key='test-only', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(429, json={'error':{'type':'insufficient_quota','message':'sensitive'}})))) as client:
        provider = OpenAIVideoProvider(client)
        with pytest.raises(ProviderError) as exc:
            provider.generate_clip('scene', [], config(tmp_path))
        assert 'sensitive' not in str(exc.value)
        assert provider.last_usage['video_seconds'] == 0


def test_video_edit_uses_current_job_as_source(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            assert request.url.path.endswith('/videos/edits')
            assert json.loads(request.content) == {'prompt':'Warmer lighting', 'video':{'id':'original_video'}}
            return httpx.Response(200, json=job('completed'))
        return httpx.Response(200, content=b'edited-video', headers={'content-type':'video/mp4'})
    with OpenAI(api_key='test-only', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        provider = OpenAIVideoProvider(client)
        clip = provider.generate_clip('Warmer lighting', [], config(tmp_path, edit_source='original_video'))
        assert clip.generation_params['editSource'] == 'original_video'
        assert provider.last_usage['video_seconds'] == 8
