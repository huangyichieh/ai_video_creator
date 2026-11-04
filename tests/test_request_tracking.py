from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import base64
import io
import json

import httpx
import pytest
from openai import OpenAI
from PIL import Image

from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.creative import Story
from story_channel.llm import OpenAILLMProvider, ProviderError, token_cost
from story_channel.media_providers import OpenAIImageProvider
from story_channel.models import ProviderConfig, UsageRecord
from story_channel.projects import ProjectManager
from story_channel.request_tracking import tracked_call, request_fields, request_rows
from story_channel.usage_attribution import assign_historical_requests
from test_openai_adapter import response_payload


def create(manager,tmp_path,title):
    source=tmp_path/'outline.md'
    source.write_text('Outline',encoding='utf-8')
    return manager.create(title,source)


def test_response_ids_and_client_ids_link_to_story_and_usage(tmp_path):
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    story=create(manager,tmp_path,'Tracked')
    sent=[]
    def handler(request):
        sent.append(request.headers['X-Client-Request-Id'])
        return httpx.Response(200,json=response_payload(),headers={'x-request-id':'req_server_123'})
    with OpenAI(api_key='secret-key-must-not-be-logged',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        provider=OpenAILLMProvider(client=client)
        config=ProviderConfig(provider='openai',model='test-model')
        tracked_call(manager,story.project_id,'story',provider.generate,'private story prompt',Story,config)
        record=UsageRecord(provider='openai',model='test-model',step='story',**request_fields(provider.last_usage))
        CostTracker(manager,story.project_id).record(record)
    rows=request_rows(manager.project_path(story.project_id)/'requests.jsonl')
    assert len(rows)==1
    assert rows[0]['story_project_id']==story.project_id
    assert rows[0]['request_id']=='req_server_123'
    assert sent[0].startswith('story_'+story.project_id+'_')
    usage=CostTracker(manager,story.project_id).records()[0]
    assert usage.story_project_id==story.project_id
    assert usage.request_ids==['req_server_123']
    assert usage.client_request_ids==sent
    text=(manager.project_path(story.project_id)/'requests.jsonl').read_text()
    assert 'private story prompt' not in text and 'secret-key' not in text


@pytest.mark.parametrize('timeout',[False,True])
def test_failure_keeps_tracking_even_without_server_id(tmp_path,timeout):
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    story=create(manager,tmp_path,'Failed')
    def handler(request):
        if timeout:
            raise httpx.ConnectError('private-secret',request=request)
        return httpx.Response(401,json={'error':{'message':'private-secret'}},headers={'x-request-id':'req_failed'})
    with OpenAI(api_key='test-only',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        provider=OpenAILLMProvider(client=client)
        with pytest.raises(ProviderError):
            tracked_call(manager,story.project_id,'story',provider.generate,'prompt',Story,ProviderConfig(provider='openai',model='test'))
        assert len(provider.last_usage['client_request_ids'])==1
        assert provider.last_usage['request_ids']==([] if timeout else ['req_failed'])
        assert token_cost(provider.last_usage,Decimal(1),Decimal(1))==(Decimal(0),False)
    row=request_rows(manager.project_path(story.project_id)/'requests.jsonl')[0]
    assert row['event']==('no_response' if timeout else 'response')
    assert 'private-secret' not in json.dumps(row)


def test_parallel_media_requests_keep_story_relations_separate(tmp_path):
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    projects=[create(manager,tmp_path,str(i)) for i in range(6)]
    image=io.BytesIO()
    Image.new('RGB',(32,32),'blue').save(image,'PNG')
    barrier=Barrier(2)  # OpenAI's app pool now admits two requests at once.
    def handler(request):
        barrier.wait(timeout=10)
        client_id=request.headers['X-Client-Request-Id']
        return httpx.Response(200,json={'created':1,'data':[{'b64_json':base64.b64encode(image.getvalue()).decode()}]},
                              headers={'x-request-id':'req_'+client_id})
    with OpenAI(api_key='test-only',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        provider=OpenAIImageProvider(client)
        def generate(project):
            config=ProviderConfig(provider='openai',model='gpt-image-1',options={'output_path':str(manager.project_path(project.project_id)/'image.png')})
            tracked_call(manager,project.project_id,'assets:scene_images',provider.generate,'prompt',[],config)
            CostTracker(manager,project.project_id).record(UsageRecord(provider='openai',model='gpt-image-1',step='assets:scene_images',**request_fields(provider.last_usage)))
        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(generate,projects))
    for project in projects:
        usage=CostTracker(manager,project.project_id).records()[0]
        assert len(usage.client_request_ids)==1
        assert project.project_id in usage.client_request_ids[0]
        assert usage.request_ids==['req_'+usage.client_request_ids[0]]
        rows=request_rows(manager.project_path(project.project_id)/'requests.jsonl')
        assert len(rows)==1 and rows[0]['story_project_id']==project.project_id


def test_legacy_assignment_preserves_provenance_backups_totals_and_unknown_ids(tmp_path):
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    target=create(manager,tmp_path,'Test')
    source=create(manager,tmp_path,'Other')
    CostTracker(manager,target.project_id).record(UsageRecord(provider='openai',model='test',step='story',cost='.14'))
    original=UsageRecord(provider='openai',model='test',step='world',cost='1.00')
    CostTracker(manager,source.project_id).record(original)
    CostTracker(manager,source.project_id).record(UsageRecord(provider='demo',model='demo',step='world'))
    report=assign_historical_requests(manager,target.project_id)
    assert report['assigned']==2
    records=CostTracker(manager,target.project_id).records()
    assert len(records)==2
    assert all(r.story_project_id==target.project_id and r.attribution=='legacy_assigned' for r in records)
    moved=next(r for r in records if r.record_id==original.record_id)
    assert moved.source_project_id==source.project_id
    assert not moved.request_ids and not moved.client_request_ids
    assert manager.load(target.project_id).actual_cost==Decimal('1.14')
    assert manager.load(source.project_id).actual_cost==0
    assert len(CostTracker(manager,source.project_id).records())==1
    assert (manager.project_path(source.project_id)/'history'/'usage_attribution'/report['migration_id']/'usage.json').exists()
    assert assign_historical_requests(manager,target.project_id)['assigned']==0
    assert len(request_rows(manager.project_path(target.project_id)/'requests.jsonl'))==2
