from pathlib import Path
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image

from story_channel.files import input_hash
from story_channel.models import GeneratedAsset,ProviderConfig
from story_channel.scene_production import SceneProduction
from story_channel.scene_video_region import region
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from story_channel.costs import CostTracker
from test_animated_scene_cards_ui import prepare
from test_scene_production import episode
from test_studio import world


@pytest.fixture
def video(episode):
    assets,scenes = prepare(episode)
    engine = RenderPipeline(assets.manager,assets.project_id)
    engine.configure(options=RenderOptions(width=320,height=180,fps=12,music=False,sfx=False,subtitles="none"))
    service = SceneProduction(assets.manager,assets.project_id)
    sid = scenes[0].scene_id
    service.generate_visual(sid,engine.load().options)
    return service,sid,engine.load().options


def test_revisions_apply_preserves_provenance_auto_approves_and_numbers(video):
    service,sid,options = video
    first = deepcopy(service.visuals()[sid])
    second = service.generate_visual(sid,options,force=True)
    assert {r["revision"] for r in service.revisions(sid)} == {1,2}
    service.approve_visual(sid,options,expected=service.visual_hash(sid,options))
    before = service.visuals()[sid]
    cost_count = len(CostTracker(service.manager,service.project_id).records())
    chosen = service.apply_revision(sid,first["asset"]["assetId"],options,expected=input_hash(before))
    assert chosen["asset"] == first["asset"] and chosen["revision"] == 1
    assert chosen["approval"]["contentHash"]==chosen["sourceHash"] and service.visual_current(sid,options,approved=True)
    assert len(CostTracker(service.manager,service.project_id).records()) == cost_count
    assert service.generate_visual(sid,options,force=True)["revision"] == 3
    current = service.visuals()[sid]
    with pytest.raises(ValueError,match="changed"):
        service.apply_revision(sid,second["asset"]["assetId"],options,expected=input_hash(before))
    service.assets.local_path(first["asset"]["filePath"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError,match="missing, modified"):
        service.apply_revision(sid,first["asset"]["assetId"],options,expected=input_hash(current))
    assert service.visuals()[sid] == current


class RedRegion:
    last_usage = {}
    def __init__(self,tools):
        self.tools = tools
        self.calls=[]
    def edit_clip(self,prompt,source,config):
        self.calls.append((prompt,source,config))
        frame = Path(config.options["output_path"]).with_suffix('.png')
        self.tools.run(["-i",source,"-frames:v","1",frame])
        with Image.open(frame) as image:
            width,height = image.size
        path = config.options["output_path"]
        self.tools.run(["-f","lavfi","-i",f"color=red:s={width}x{height}:r=12","-t",config.options["duration"],
            "-an","-c:v","libx264","-crf","0","-pix_fmt","yuv420p",path])
        self.last_usage={"video_seconds":config.options["duration"],"job_id":"mock-regional-edit"}
        return GeneratedAsset(type="video",provider="runway",model="aleph2",file_path=path,
            duration=config.options["duration"],prompt_hash=input_hash(prompt))


def pixels(tools,path,destination):
    tools.run(["-ss","0.5","-i",path,"-frames:v","1",destination])
    with Image.open(destination) as image:
        return np.asarray(image.convert('RGB')).astype(int)


def test_region_edit_replaces_only_selected_window_keeps_source_and_records_cost(video,tmp_path):
    from story_channel.scene_video_region import ffmpeg
    service,sid,options = video
    original = service.visuals()[sid]
    source = service.assets.local_path(original["asset"]["filePath"])
    old_bytes = source.read_bytes()
    tools = ffmpeg(service)
    provider = RedRegion(tools)
    selection={"left":.25,"top":.25,"right":.75,"bottom":.75}
    edited = service.modify_region(sid,options,expected=input_hash(original),rectangle=selection,
        prompt="Replace this area with a red animated display",provider=provider)
    assert len(provider.calls) == 1 and edited["revision"] == 2
    assert source.read_bytes() == old_bytes and edited["approval"]["contentHash"]==edited["sourceHash"]
    assert edited["asset"]["model"] == "aleph2"
    before = pixels(tools,source,tmp_path/"before.png")
    after = pixels(tools,service.assets.local_path(edited["asset"]["filePath"]),tmp_path/"after.png")
    left,top,right,bottom = region(selection,(320,180))
    outside=np.ones(before.shape[:2],dtype=bool);outside[top:bottom,left:right]=False
    assert np.abs(before[outside]-after[outside]).max() <= 2
    assert np.abs(before[top:bottom,left:right]-after[top:bottom,left:right]).mean() > 10
    assert service.visual_current(sid,options)
    assert {r["revision"] for r in service.revisions(sid)} == {1,2}
    usage=[r for r in CostTracker(service.manager,service.project_id).records() if r.step=="animation:visual_region:"+sid]
    assert usage and usage[-1].model == "aleph2" and usage[-1].video_seconds > 0
    with pytest.raises(ValueError,match="changed"):
        service.modify_region(sid,options,expected=input_hash(original),rectangle=selection,prompt="Old source",provider=provider)
    assert len(provider.calls) == 1


def test_invalid_selection_or_failed_edit_preserves_active_clip_and_approval(video):
    service,sid,options = video
    service.approve_visual(sid,options,expected=service.visual_hash(sid,options))
    original=service.visuals()[sid]
    class Failing:
        last_usage={}
        calls=0
        def edit_clip(self,prompt,source,config):
            self.calls+=1
            self.last_usage={"video_seconds":config.options["duration"]}
            raise RuntimeError("Injected regional edit failure")
    provider=Failing()
    with pytest.raises(ValueError,match="valid zoom rectangle"):
        service.modify_region(sid,options,expected=input_hash(original),rectangle={"left":"bad"},prompt="Change it",provider=provider)
    assert provider.calls==0
    with pytest.raises(RuntimeError,match="Injected regional"):
        service.modify_region(sid,options,expected=input_hash(original),
            rectangle={"left":.1,"top":.1,"right":.5,"bottom":.5},prompt="Change it",provider=provider)
    assert service.visuals()[sid]==original
    assert provider.calls==1
    assert any(service.path.glob("error_reports/**/*.json"))
    usage=[r for r in CostTracker(service.manager,service.project_id).records() if r.step=="animation:visual_region:"+sid]
    assert usage[-1].outcome=="failed" and usage[-1].video_seconds>0


def test_runway_regional_edit_contract_does_not_send_unsupported_mask(tmp_path,monkeypatch):
    from story_channel.balanced_providers import RunwayVideoEditProvider
    provider=RunwayVideoEditProvider()
    requests=[]
    monkeypatch.setattr(provider,"upload",lambda path:"runway://source-crop")
    def task(endpoint,body,config,unit,amount):
        requests.append((endpoint,body,unit,amount));return "task-123"
    monkeypatch.setattr(provider,"task",task)
    config=ProviderConfig(provider="runway",model="aleph2",options={"output_path":str(tmp_path/'out.mp4'),"duration":3})
    asset=provider.edit_clip("A blue cabinet",tmp_path/'crop.mp4',config)
    endpoint,body,unit,amount=requests[0]
    assert endpoint=="/video_to_video" and body=={"model":"aleph2","videoUri":"runway://source-crop","promptText":"A blue cabinet","outputFormat":"mp4"}
    assert unit=="video_seconds" and amount==3 and asset.model=="aleph2"


def test_short_scene_uses_two_second_provider_input_then_restores_original_length(video):
    from uuid import uuid4
    from story_channel.files import atomic_write
    from story_channel.media_utils import file_hash
    from story_channel.scene_video_region import ffmpeg
    import json
    service,sid,options=video
    current=service.visuals()[sid]
    tools=ffmpeg(service)
    path=service.path/"videos"/"short_source.mp4"
    tools.run(["-i",service.assets.local_path(current["asset"]["filePath"]),"-t","1","-an",
        "-c:v","libx264","-crf","0","-pix_fmt","yuv420p",path])
    short=GeneratedAsset.model_validate(current["asset"]).model_copy(update={"asset_id":uuid4().hex,
        "file_path":path.relative_to(service.path).as_posix(),"duration":1})
    current.update(asset=short.model_dump(mode="json",by_alias=True),fileHash=file_hash(path),revision=2)
    current["formats"]={"landscape_16_9":{"status":"succeeded","asset":current["asset"],"fileHash":current["fileHash"]}}
    values=service.visuals();values[sid]=current
    atomic_write(service.path/"visual_scenes.json",json.dumps(values))
    provider=RedRegion(tools)
    result=service.modify_region(sid,options,expected=input_hash(current),
        rectangle={"left":.1,"top":.1,"right":.6,"bottom":.6},prompt="Red signal panel",provider=provider)
    assert provider.calls[0][2].options["duration"]==2
    assert tools.validate(provider.calls[0][1])==pytest.approx(2,abs=.1)
    assert result["asset"]["duration"]==pytest.approx(1,abs=.15)


def test_combined_delivery_edits_rebuild_portrait_and_revisions_apply_together(video):
    from story_channel.scene_video_region import ffmpeg
    service,sid,options=video
    first=service.visuals()[sid]
    options=RenderOptions.model_validate({**options.model_dump(),
        "delivery_formats":["landscape_16_9","portrait_9_16"],"portrait_safe_area":True})
    second=service.generate_visual(sid,options,force=True)
    result=service.modify_region(sid,options,expected=input_hash(second),
        rectangle={"left":.2,"top":.2,"right":.8,"bottom":.8},prompt="Red signal panel",provider=RedRegion(ffmpeg(service)))
    assert result["revision"]==3
    assert set(result["formats"])=={"landscape_16_9","portrait_9_16"}
    for version in result["formats"].values():
        assert version["status"]=="succeeded" and service.assets.local_path(version["asset"]["filePath"]).is_file()
    with pytest.raises(ValueError,match="delivery formats"):
        service.apply_revision(sid,first["asset"]["assetId"],options,expected=input_hash(result))
    applied=service.apply_revision(sid,second["asset"]["assetId"],options,expected=input_hash(result))
    assert applied["formats"]==second["formats"] and applied["approval"]["contentHash"]==applied["sourceHash"]
    assert service.generate_visual(sid,options,force=True)["revision"]==4


def test_time_range_edit_keeps_frames_before_after_and_outside_rectangle(video,tmp_path):
    from story_channel.scene_video_region import ffmpeg
    service,sid,options=video
    original=service.visuals()[sid]
    tools=ffmpeg(service)
    provider=RedRegion(tools)
    selection={"left":.25,"top":.25,"right":.75,"bottom":.75}
    source=service.assets.local_path(original["asset"]["filePath"])
    result=service.modify_region(sid,options,expected=input_hash(original),rectangle=selection,
        prompt="A red panel",provider=provider,start_time=1,end_time=2)
    output=service.assets.local_path(result["asset"]["filePath"])
    assert len(provider.calls)==1 and provider.calls[0][2].options["duration"]==2
    params=result["asset"]["generationParams"]
    assert params["startTime"]==1 and params["endTime"]==2
    assert params["segments"][0]["start"]==1 and params["segments"][0]["duration"]==1
    assert tools.validate(output)==pytest.approx(tools.validate(source),abs=.15)
    left,top,right,bottom=region(selection,(320,180))
    outside=np.ones((180,320),dtype=bool);outside[top:bottom,left:right]=False
    for index,second in enumerate((.5,1.,1.5,2.,2.5)):
        images=[]
        for name,path in (("source",source),("result",output)):
            dest=tmp_path/f"{name}_{index}.png"
            tools.run(["-ss",second,"-i",path,"-frames:v","1",dest])
            with Image.open(dest) as image:
                images.append(np.asarray(image.convert("RGB")).astype(int))
        before,after=images
        if 1<=second<2:
            assert np.abs(before[outside]-after[outside]).max()<=2
            assert np.abs(before[top:bottom,left:right]-after[top:bottom,left:right]).mean()>10
        else:
            assert np.abs(before-after).max()<=2


def test_invalid_edit_range_rejected_before_request(video):
    service,sid,options=video
    original=service.visuals()[sid]
    from story_channel.scene_video_region import ffmpeg
    provider=RedRegion(ffmpeg(service))
    for start,end in ((-1,1),(2,1),(1,1),(0,999),(float("nan"),2)):
        with pytest.raises(ValueError,match="Edit start"):
            service.modify_region(sid,options,expected=input_hash(original),
                rectangle={"left":.1,"top":.1,"right":.7,"bottom":.7},prompt="Red",
                provider=provider,start_time=start,end_time=end)
    assert not provider.calls and service.visuals()[sid]==original


def test_edit_timecodes():
    from story_channel.scene_video_region import timecode,format_timecode
    assert timecode("00:01:50")==110
    assert timecode("01:02:03.125")==3723.125
    assert format_timecode(3723.125)=="01:02:03.125"
    for value in ("00:60:00","00:00:60","bad","-1:00:00","NaN"):
        with pytest.raises(ValueError):
            timecode(value)


def test_browser_preview_is_cached_h264_proxy_and_keeps_original(video,monkeypatch):
    from story_channel.scene_video_region import playback_preview,ffmpeg
    service,sid,options=video
    record=service.visuals()[sid]
    source=service.assets.local_path(record["asset"]["filePath"])
    original=source.read_bytes()
    proxy,size,duration=playback_preview(service,sid,record)
    assert proxy!=source and proxy.is_file() and size==(320,180)
    assert source.read_bytes()==original
    assert ffmpeg(service).validate(proxy)==pytest.approx(duration,abs=.1)
    info=ffmpeg(service).run(["-i",proxy,"-f","null","-"]).stderr
    assert "h264" in info and "yuv420p" in info
    before=proxy.stat().st_mtime_ns
    # Reopening reuses the proxy, rather than transcoding the original again.
    again,again_size,_=playback_preview(service,sid,record)
    assert again==proxy and again_size==size and proxy.stat().st_mtime_ns==before
