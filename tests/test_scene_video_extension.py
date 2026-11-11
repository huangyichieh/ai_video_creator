import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.files import input_hash
from story_channel.models import GeneratedAsset
from story_channel.costs import CostTracker
from story_channel.scene_video_region import ffmpeg
from story_channel.scene_video_extension import extension_seconds
from test_scene_video_edits import video,pixels
from test_scene_video_revision_ui import display
from test_animated_scene_cards_ui import prepare
from test_scene_production import episode
from test_studio import world


class Continuation:
    last_usage={}
    def __init__(self,tools):
        self.tools=tools
        self.calls=[]
    def generate_clip(self,prompt,references,config):
        self.calls.append((prompt,references,config))
        with Image.open(references[0]) as image:
            width,height=image.size
        width,height=width//2*2,height//2*2
        self.tools.run(["-f","lavfi","-i",f"color=red:s={width}x{height}:r=12","-t",config.options["seconds"],
            "-an","-c:v","libx264","-crf","0","-pix_fmt","yuv420p",config.options["output_path"]])
        self.last_usage={"video_seconds":config.options["seconds"],"job_id":"mock-extension"}
        return GeneratedAsset(type="video",provider="runway",model="klingO3_pro",file_path=config.options["output_path"],
            duration=config.options["seconds"],prompt_hash=input_hash(prompt))


def test_extension_appends_requested_second_preserves_original_and_records_usage(video,tmp_path):
    service,sid,options=video
    before=service.visuals()[sid]
    source=service.assets.local_path(before["asset"]["filePath"])
    original=source.read_bytes()
    tools=ffmpeg(service)
    provider=Continuation(tools)
    seconds=tools.validate(source)
    result=service.extend_visual(sid,options,expected=input_hash(before),seconds=1,
        prompt="The character slowly waves goodbye.",provider=provider)
    output=service.assets.local_path(result["asset"]["filePath"])
    assert tools.validate(output)==pytest.approx(seconds+1,abs=.15)
    assert source.read_bytes()==original and result["approval"]["contentHash"]==result["sourceHash"]
    assert result["revision"]==before["revision"]+1 and service.visual_current(sid,options)
    assert np.abs(pixels(tools,source,tmp_path/"old.png")-pixels(tools,output,tmp_path/"new.png")).max()<=2
    tools.run(["-ss",seconds+.5,"-i",output,"-frames:v","1",tmp_path/"tail.png"])
    with Image.open(tmp_path/"tail.png") as image:
        color=np.asarray(image.convert("RGB")).mean(axis=(0,1))
    assert color[0]>200 and color[1]<20
    assert len(provider.calls)==1 and provider.calls[0][2].options["seconds"]==3
    assert "waves goodbye" in provider.calls[0][0]
    params=result["asset"]["generationParams"]
    assert params["originalDuration"]==seconds and params["extensionSeconds"]==1
    assert params["requestedProviderSeconds"]==3
    assert service.assets.local_path(params["firstFrame"]).is_file()
    usage=[r for r in CostTracker(service.manager,service.project_id).records() if r.step=="animation:visual_extend:"+sid]
    assert usage and usage[-1].video_seconds==3
    assert {r["revision"] for r in service.revisions(sid)}=={1,2}
    with pytest.raises(ValueError,match="changed"):
        service.extend_visual(sid,options,expected=input_hash(before),seconds=1,prompt="Again",provider=provider)
    assert len(provider.calls)==1


def test_failed_extension_retains_active_revision_and_logs_error(video):
    service,sid,options=video
    service.approve_visual(sid,options,expected=service.visual_hash(sid,options))
    before=service.visuals()[sid]
    class Failing:
        last_usage={}
        def generate_clip(self,*args):
            self.last_usage={"video_seconds":3}
            raise RuntimeError("Injected extension rejection")
    with pytest.raises(RuntimeError,match="Injected extension"):
        service.extend_visual(sid,options,expected=input_hash(before),seconds=1,prompt="Walk forward",provider=Failing())
    assert service.visuals()[sid]==before
    assert any(service.path.glob("error_reports/**/*.json"))
    usage=[r for r in CostTracker(service.manager,service.project_id).records() if r.step=="animation:visual_extend:"+sid]
    assert usage[-1].outcome=="failed" and usage[-1].video_seconds==3


def test_extension_dialog_cancel_confirm_and_progress(video,monkeypatch):
    from story_channel.scene_production import SceneProduction
    service,sid,options=video
    target=f"animated-scene-card-{service.project_id}-{sid}"
    calls=[]
    release=Event()
    def extend(self,*args,**kwargs):
        calls.append(kwargs)
        assert release.wait(20)
        return self.visuals()[sid]
    monkeypatch.setattr(SceneProduction,"extend_visual",extend)
    app=AppTest.from_function(display,args=(service.manager,service.project_id),default_timeout=30).run()
    app.button(key=target+"-extend").click().run()
    assert not app.exception and app.get("dialog")
    assert app.button(key="confirm-scene-extension").disabled
    app.button(key="cancel-scene-extension").click().run()
    assert not app.get("dialog") and not calls
    app.button(key=target+"-extend").click().run()
    next(w for w in app.text_area if w.label=="Extension prompt").set_value("Wave goodbye").run()
    assert not app.button(key="confirm-scene-extension").disabled
    app.button(key="confirm-scene-extension").click().run()
    try:
        assert not app.exception and not app.get("dialog")
        assert any("Generating..." in caption.value for caption in app.caption)
        assert not app.session_state["workspace-task"]["future"].done()
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=5)
    assert len(calls)==1 and calls[0]["seconds"]==1 and calls[0]["prompt"]=="Wave goodbye"


def test_invalid_extension_duration():
    assert extension_seconds(1)==1
    for value in (0,-1,16,True,"bad",float("nan"),float("inf")):
        with pytest.raises(ValueError):
            extension_seconds(value)


def test_extensions_keep_dual_formats_and_can_be_extended_again(video):
    from story_channel.render_models import RenderOptions
    service,sid,options=video
    options=RenderOptions.model_validate({**options.model_dump(),
        "delivery_formats":["landscape_16_9","portrait_9_16"],"portrait_safe_area":True})
    original=service.generate_visual(sid,options,force=True)
    provider=Continuation(ffmpeg(service))
    result=service.extend_visual(sid,options,expected=input_hash(original),seconds=1,prompt="Keep waving",provider=provider)
    assert result["revision"]==3
    for name,version in result["formats"].items():
        assert version["status"]=="succeeded"
        assert version["asset"]["duration"]==pytest.approx(original["asset"]["duration"]+1,abs=.15)
        assert service.assets.local_path(version["asset"]["filePath"]).is_file()
    assert service.visual_current(sid,options)
    again=service.extend_visual(sid,options,expected=input_hash(result),seconds=1,prompt="Lower the hand",provider=provider)
    assert again["revision"]==4
    assert again["asset"]["duration"]==pytest.approx(original["asset"]["duration"]+2,abs=.15)
    assert len(provider.calls)==2
