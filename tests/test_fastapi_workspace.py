"""Offline contract tests for the native client's router/service/worker boundary."""
from threading import Event
import json
import time

import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.models.api import CommandRequest
from backend.workers.jobs import Jobs
from story_channel.config import Settings
from test_studio import world
from test_scene_production import episode


@pytest.fixture
def client(tmp_path,monkeypatch):
    import os
    from backend.services.settings import API_KEYS
    for key in API_KEYS:monkeypatch.setenv(key,os.getenv(key,''))
    monkeypatch.setenv('STORY_STORAGE_ROOT',str(tmp_path/'storage'))
    monkeypatch.setenv('STORY_ENV_FILE',str(tmp_path/'.env'))
    settings=Settings(data_root=tmp_path/'storage/outputs/channel',llm_provider='demo',asset_provider='demo')
    with TestClient(create_app(settings,database=tmp_path/'storage/database/jobs.sqlite3')) as client:
        yield client


def wait_job(client,identity):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        job=client.get('/api/jobs/'+identity).json()
        if job['status'] not in ('queued','running','stopping'):return job
        time.sleep(.01)
    pytest.fail('Background job did not finish')


def test_world_review_approval_and_native_snapshot(client):
    assert client.get('/api/health').json()['frontend']=='Flutter Web'
    result=client.post('/api/projects',json={'name':'Test World','premise':'An island of memories.','language':'en'})
    assert result.status_code==201,result.text
    pid=result.json()['projectId']
    snapshot=client.get(f'/api/projects/{pid}/workspace/world').json()
    assert snapshot['sections'][0]['id']=='world_setting.json'
    assert snapshot['project']['title']=='Test World'
    exported=client.get(f'/api/projects/{pid}/exports/outline.json')
    assert exported.status_code==200
    assert exported.headers['content-type'].startswith('text/markdown')
    assert 'An island of memories.' in exported.text
    assert not list(client.app.state.service.manager.project_path(pid).rglob('*.md'))
    def run(operation,args=None):
        prepared=client.post(f'/api/projects/{pid}/prepare',json={'operation':operation,'arguments':args or {}})
        assert prepared.status_code==200,prepared.text
        result=client.post('/api/jobs',json={'project_id':pid,'operation':operation,'arguments':prepared.json()['values']})
        assert result.status_code==202,result.text
        job=wait_job(client,result.json()['id'])
        assert job['status']=='succeeded',job
        return job
    run('world.review')
    run('world.resolve_base',{'decision':'revert','text':''})
    run('world.approve_base')
    run('episodes.create',{'title':'Episode 1','outline':'A keeper finds a memory.','duration':120})
    projects=client.get('/api/projects').json()
    assert len(projects)==2
    episode=next(row for row in projects if row.get('worldProjectId'))
    assert episode['worldProjectId']==pid
    eid=episode['projectId']
    outline=next(row for row in client.get(f'/api/projects/{eid}/workspace/story').json()['sections'] if row['id']=='outline.json')
    edit=client.put(f'/api/projects/{eid}/artifacts/outline.json',json={
        'value':{'text':'The keeper finds a lost memory.'},'expected':outline['version']})
    assert edit.status_code==200,edit.text
    assert client.app.state.service.manager.read_outline(eid)=='The keeper finds a lost memory.'
    assert client.put(f'/api/projects/{eid}/artifacts/outline.json',json={
        'value':{'text':'Outdated edit'},'expected':outline['version']}).status_code==409
    assert client.get(f'/api/projects/{pid}/errors').json()==[]


def test_media_path_and_key_boundaries(client):
    result=client.post('/api/projects',json={'name':'Safe','premise':'A safe world.'})
    pid=result.json()['projectId']
    assert client.post('/api/jobs',json={'project_id':pid,'operation':'os.system'}).status_code==400
    assert client.post('/api/uploads',files={'file':('bad.exe',b'data')}).status_code==400
    assert client.get(f'/api/projects/{pid}/media/%2E%2E%2Fmanifest.json').status_code in (400,404)
    key='example-private-key-12345678'
    assert client.put('/api/settings/keys',json={'values':{'OPENAI_API_KEY':key}}).status_code==200
    response=client.get('/api/settings/keys')
    assert key not in response.text
    assert any(row['provided'] for row in response.json() if row['name']=='OPENAI_API_KEY')
    assert client.request('DELETE',f'/api/projects/{pid}',json={'confirmation':'wrong'}).status_code==400


def test_queue_stop_and_restart_preserve_completed_result(tmp_path):
    started,release=Event(),Event()
    def execute(pid,op,args,stop):
        started.set()
        assert release.wait(5)
        return {'completed':'retained'}
    database=tmp_path/'jobs.sqlite3'
    queue=Jobs(database,execute)
    first=queue.submit(CommandRequest(project_id='p',operation='first'))
    assert started.wait(5)
    second=queue.submit(CommandRequest(project_id='p',operation='second'))
    queue.stop(second['id'])
    release.set();queue.shutdown()
    assert queue.get(first['id'])['result']=={'completed':'retained'}
    assert queue.get(second['id'])['status']=='stopped'
    reopened=Jobs(database,execute)
    try:assert reopened.get(first['id'])['result']=={'completed':'retained'}
    finally:reopened.shutdown()


def test_relocated_models_keep_class_identity():
    from backend.models.creative import Scene
    from story_channel.creative import Scene as CompatibilityScene
    assert Scene is CompatibilityScene


def test_queued_project_mutations_do_not_block_independent_provider_jobs(tmp_path):
    started,release,sample=Event(),Event(),Event()
    def execute(pid,op,args,stop):
        if op=='voice_design.generate_voice_design':sample.set();return 'sample'
        started.set()
        assert release.wait(5)
        return 'saved'
    queue=Jobs(tmp_path/'jobs.sqlite3',execute)
    try:
        queue.submit(CommandRequest(project_id='p',operation='first'))
        assert started.wait(2)
        for _ in range(4):queue.submit(CommandRequest(project_id='p',operation='queued'))
        queue.submit(CommandRequest(project_id='p',operation='voice_design.generate_voice_design'))
        assert sample.wait(2)
    finally:
        release.set();queue.shutdown()


def test_native_scene_editor_keeps_approval_and_uses_current_sources(episode,tmp_path,monkeypatch):
    world,project,pipeline=episode
    monkeypatch.setenv('STORY_STORAGE_ROOT',str(tmp_path/'storage'))
    from story_channel.assets import AssetPipeline
    engine=AssetPipeline(world.manager,project.project_id)
    engine.generate('scene_images')
    with TestClient(create_app(world.manager.settings,database=tmp_path/'jobs.sqlite3')) as client:
        pid=project.project_id
        page=client.get(f'/api/projects/{pid}/workspace/animations')
        assert page.status_code==200,page.text
        assert page.json()['computed']['assetCheck']
        sid=pipeline.artifact('storyboard').scenes[0].scene_id
        prepared=client.post(f'/api/projects/{pid}/prepare',json={'operation':'storyboard_editor.save_scene','arguments':{'scene_id':sid,'auto_approve':True}}).json()
        args=prepared['values']
        assert 'scenes' in args['data']
        args['data']['scenes'][0]['action']+=' A small pause follows.'
        result=client.post('/api/jobs',json={'project_id':pid,'operation':'storyboard_editor.save_scene','arguments':args})
        assert wait_job(client,result.json()['id'])['status']=='succeeded'
        assert 'storyboard' in world.manager.load(pid).approvals
        assert pipeline.artifact('storyboard').scenes[0].action.endswith('A small pause follows.')
        revisions=client.get(f'/api/projects/{pid}/revisions',params={'subject':sid})
        assert revisions.status_code==200 and revisions.json()['records']
        image_file=revisions.json()['records'][0]['asset']['filePath']
        from PIL import Image
        from io import BytesIO
        preview=client.get(f'/api/projects/{pid}/media/{image_file}',params={'width':96})
        assert preview.status_code==200 and max(Image.open(BytesIO(preview.content)).size)<=96
        review=client.post('/api/jobs',json={'project_id':pid,'operation':'batch.review_episode','arguments':{'kind':'outline'}})
        assert wait_job(client,review.json()['id'])['status']=='succeeded'
        reviewed=client.get(f'/api/projects/{pid}/reviews/outline')
        assert reviewed.status_code==200,reviewed.text
        value=reviewed.json()
        approve=client.post('/api/jobs',json={'project_id':pid,'operation':'studio.resolve_content_review','arguments':{
            'name':'outline','text':value['sourceText'],'expected':value['expected'],'report_digest':value['reportDigest'],
            'decisions':[{'path':change['path'],'choice':'revert'} for change in value['session']['changes']]}})
        assert wait_job(client,approve.json()['id'])['status']=='succeeded'


def test_visual_input_form_uses_saved_prompt(world,tmp_path,monkeypatch):
    world.generate_characters()
    monkeypatch.setenv('STORY_STORAGE_ROOT',str(tmp_path/'storage'))
    with TestClient(create_app(world.manager.settings,database=tmp_path/'jobs.sqlite3')) as client:
        pid=world.project_id
        cid=world.pipeline.artifact('characters').characters[0].character_id
        prepared=client.post(f'/api/projects/{pid}/prepare',json={'operation':'visual.update','arguments':{'stage':'character_references','subject':cid}})
        assert prepared.status_code==200,prepared.text
        assert prepared.json()['values']['prompt']
        assert {'mode','prompt'} <= {p['name'] for p in prepared.json()['definition']['parameters']}
