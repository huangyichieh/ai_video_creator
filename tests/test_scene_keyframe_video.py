"""Timed Aleph finishing, real local media and mocked paid providers."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from story_channel import scene_keyframes as frames
from story_channel import scene_keyframe_video as multi
from story_channel.costs import CostTracker
from story_channel.ffmpeg_renderer import FFmpeg
from story_channel.files import atomic_write,input_hash
from story_channel.models import GeneratedAsset,ProviderConfig
from story_channel.render_models import RenderOptions
from story_channel.scene_production import SceneProduction
from test_scene_keyframes import ImageProvider
from test_animated_scene_cards_ui import prepare
from test_scene_production import episode
from test_studio import world


@pytest.fixture
def timed(episode):
    engine,scenes=prepare(episode)
    sid=scenes[0].scene_id
    token=uuid4().hex
    draft=frames.start(engine,sid,token)
    first=draft['frames'][0]['id']
    last=uuid4().hex
    frames.generate(engine,token,last,first,'Same character waves',provider=ImageProvider())
    frames.confirm(engine,token,{first:0,last:scenes[0].duration_sec},keep_open=True)
    service=SceneProduction(engine.manager,engine.project_id)
    options=RenderOptions(provider='runway',video_model='klingO3_pro',width=320,height=180,fps=12,
        subtitles='none',music=False,sfx=False)
    return service,sid,options,token


class PaidMedia:
    last_usage={}
    def __init__(self,tools,model):
        self.tools,self.model=tools,model
        self.calls=[]
        self.fail=False
    def output(self,prompt,config,duration):
        self.last_usage={'video_seconds':duration,'job_id':self.model+'-'+uuid4().hex}
        if self.fail:
            import json
            target=Path(config.options['output_path'])
            atomic_write(target.with_suffix(target.suffix+'.task.json'),json.dumps({'id':self.last_usage['job_id'],'status':'FAILED'}))
            raise RuntimeError('Injected Aleph failure')
        target=config.options['output_path']
        self.tools.run(['-f','lavfi','-i','color=blue:s=320x180:r=12','-t',duration,
            '-an','-c:v','libx264','-pix_fmt','yuv420p',target])
        return GeneratedAsset(type='video',provider='runway',model=self.model,file_path=target,
            duration=duration,prompt_hash=input_hash(prompt))
    def generate_clip(self,prompt,references,config):
        self.calls.append((prompt,references,config))
        return self.output(prompt,config,config.options['seconds'])
    def edit_clip(self,prompt,source,config):
        self.calls.append((prompt,source,config))
        return self.output(prompt,config,config.options['duration'])


def providers(service,monkeypatch):
    from story_channel import balanced_providers as bp
    tools=FFmpeg(service.manager.settings.ffmpeg_path)
    base,aleph=PaidMedia(tools,'klingO3_pro'),PaidMedia(tools,'aleph2')
    monkeypatch.setattr(bp,'video_provider',lambda *_:base)
    monkeypatch.setattr(bp,'RunwayVideoEditProvider',lambda **_:aleph)
    return tools,base,aleph


def test_timed_frames_reach_aleph_and_time_edit_invalidates_video(timed,monkeypatch):
    service,sid,options,token=timed
    tools,base,aleph=providers(service,monkeypatch)
    result=service.generate_visual(sid,options)
    assert len(base.calls)==len(aleph.calls)==1
    assert 'last_frame' in base.calls[0][2].options
    guides=aleph.calls[0][2].options['keyframes']
    duration=multi.saved(service,sid)['frames'][-1]['timeSec']
    assert [guide['seconds'] for guide in guides]==[0,duration]
    assert all(Path(guide['file']).is_file() for guide in guides)
    assert result['status']=='succeeded' and result['asset']['model']=='aleph2'
    assert abs(tools.validate(service.assets.local_path(result['asset']['filePath']))-duration)<.1
    assert service.visual_current(sid,options)
    records=[r for r in CostTracker(service.manager,service.project_id).records() if r.step.startswith('animation:visual')]
    assert {r.model for r in records}=={'klingO3_pro','aleph2'}
    saved=multi.saved(service,sid)
    times={frame['id']:frame['timeSec'] for frame in saved['frames']}
    frames.confirm(service.assets,token,times,keep_open=True)
    assert service.visual_current(sid,options)
    times[saved['frames'][-1]['id']]=duration/2
    frames.confirm(service.assets,token,times,keep_open=True)
    assert not service.visual_current(sid,options)


def test_failed_aleph_reuses_paid_base_on_retry(timed,monkeypatch):
    service,sid,options,_=timed
    _,base,aleph=providers(service,monkeypatch)
    aleph.fail=True
    with pytest.raises(RuntimeError,match='Injected Aleph'):
        service.generate_visual(sid,options)
    failed=service.visuals()[sid]
    assert failed['status']=='failed' and failed['multiKeyframeJob']['baseAsset']
    aleph.fail=False
    success=service.generate_visual(sid,options)
    assert success['status']=='succeeded'
    assert len(base.calls)==1 and len(aleph.calls)==2
    assert aleph.calls[0][1]==aleph.calls[1][1]
    assert list(Path(aleph.calls[1][2].options['output_path']).parent.glob('failed-task-*.json'))
    usage=[r for r in CostTracker(service.manager,service.project_id).records() if r.model=='aleph2']
    assert len(usage)==2


def test_stale_keyframes_rejected_before_paid_generation(timed,monkeypatch):
    import json
    service,sid,options,_=timed
    _,base,aleph=providers(service,monkeypatch)
    saved={sid:deepcopy(multi.saved(service,sid))}
    saved[sid]['sourceHash']='outdated'
    atomic_write(service.path/'scene_image_keyframes.json',json.dumps(saved))
    with pytest.raises(ValueError,match='outdated storyboard'):
        service.generate_visual(sid,options)
    assert not base.calls and not aleph.calls


def test_portrait_retry_reuses_finished_aleph_primary(timed,monkeypatch):
    from story_channel import animation_formats as formats
    service,sid,options,_=timed
    options=RenderOptions.model_validate({**options.model_dump(),
        'delivery_formats':['landscape_16_9','portrait_9_16']})
    _,base,aleph=providers(service,monkeypatch)
    original=formats.create_portrait
    def failure(*args):
        raise RuntimeError('Injected portrait failure')
    monkeypatch.setattr(formats,'create_portrait',failure)
    with pytest.raises(RuntimeError,match='portrait failure'):
        service.generate_visual(sid,options)
    assert service.visuals()[sid]['formats']['landscape_16_9']['asset']['model']=='aleph2'
    monkeypatch.setattr(formats,'create_portrait',original)
    result=service.generate_visual(sid,options)
    assert len(base.calls)==len(aleph.calls)==1
    assert result['formats']['portrait_9_16']['status']=='succeeded'
    assert result['formats']['portrait_9_16']['asset']['generationParams']['sourceAssetId']==result['asset']['assetId']
    assert service.visual_current(sid,options)


def test_long_video_preserves_absolute_timing_and_completed_segments_on_retry(timed,monkeypatch):
    service,sid,options,_=timed
    tools,base,aleph=providers(service,monkeypatch)
    folder=service.path/'long-base'
    folder.mkdir()
    config=ProviderConfig(provider='runway',model='klingO3_pro',options={'output_path':str(folder/'base.mp4')})
    source=base.output('base',config,31.)
    source.file_path=Path(source.file_path).relative_to(service.path).as_posix()
    keyframes=multi.validated(service,sid,options)
    keyframes['duration']=31.
    keyframes['frames'][-1]['timeSec']=31.
    keyframes['hash']=input_hash(keyframes['frames'])
    callback=aleph.edit_clip
    def fail_second(prompt,video,configuration):
        if len(aleph.calls)==1:
            aleph.fail=True
        return callback(prompt,video,configuration)
    monkeypatch.setattr(aleph,'edit_clip',fail_second)
    with pytest.raises(RuntimeError,match='Injected Aleph'):
        multi.finish(service,sid,source,options,keyframes,tools,lambda _:None,provider=aleph)
    assert len(aleph.calls)==2
    monkeypatch.setattr(aleph,'edit_clip',callback)
    aleph.fail=False
    result=multi.finish(service,sid,source,options,keyframes,tools,lambda _:None,provider=aleph)
    assert len(aleph.calls)==3
    assert abs(tools.validate(service.assets.local_path(result.file_path))-31)<.1
    assert [part['start'] for part in result.generation_params['parts']]==[0,15.5]
    assert [guide['seconds'] for guide in aleph.calls[2][2].options['keyframes']]==[0,15.5]
    assert aleph.calls[2][2].options['duration']==15.5
    cached=multi.finish(service,sid,source,options,keyframes,tools,lambda _:None,provider=aleph)
    assert cached.asset_id==result.asset_id and len(aleph.calls)==3


def test_aleph_request_uses_absolute_times_and_endpoint_fraction(tmp_path,monkeypatch):
    from story_channel.balanced_providers import RunwayVideoEditProvider
    provider=RunwayVideoEditProvider()
    requests=[]
    monkeypatch.setattr(provider,'upload',lambda value:'runway://'+Path(value).name)
    monkeypatch.setattr(provider,'task',lambda endpoint,body,*args:requests.append((endpoint,body)) or 'fake-job')
    config=ProviderConfig(provider='runway',model='aleph2',options={'output_path':str(tmp_path/'out.mp4'),
        'duration':4,'keyframes':[{'file':'a.png','seconds':0},{'file':'b.png','seconds':1.5},{'file':'c.png','seconds':4}]})
    provider.edit_clip('Follow the timed images','input.mp4',config)
    assert requests[0][0]=='/video_to_video'
    assert requests[0][1]['keyframes']==[{'uri':'runway://a.png','seconds':0.},
        {'uri':'runway://b.png','seconds':1.5},{'uri':'runway://c.png','at':1}]
    config.options['keyframes']*=2
    with pytest.raises(ValueError,match='five'):
        provider.edit_clip('Follow the timed images','input.mp4',config)
    assert len(requests)==1
