import shutil
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest

from story_channel.detail_camera import validate
from story_channel.location_details import add_draft,save_camera,saved_camera,generate
from test_location_master_pack import master_pack
from test_location_details import prepare
from test_studio import world


def test_saved_pose_reset_and_new_session_keep_floor_position_and_fov(master_pack,monkeypatch):
    from streamlit.testing.v1 import AppTest
    engine,lid,provider,_ = master_pack
    add_draft(engine,lid,"desk_area","Desk area")
    setup = {"position":{"x":1,"y":-1},"yaw":135,"heightMeters":2.,"pitch":30.}
    save_camera(engine,lid,"desk_area",setup,85)
    received = []
    def floor_picker(**kwargs):
        return SimpleNamespace(camera=kwargs["data"]["camera"])
    def pose_picker(**kwargs):
        received.append(kwargs["data"])
        return SimpleNamespace(pose=kwargs["data"]["pose"])
    monkeypatch.setattr("story_channel.detail_camera_ui.picker",floor_picker)
    monkeypatch.setattr("story_channel.detail_camera_pose_ui.picker",pose_picker)
    def display(engine,lid):
        from story_channel.location_details_ui import camera_fields
        camera_fields(engine.load().location_reference_sets[lid],"pose-test",engine=engine,lid=lid,identity="desk_area")
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=20).run()
    assert not app.exception
    assert received[-1]["pose"] == {"heightMeters":2.,"pitch":30.}
    next(button for button in app.button if button.label == "Reset camera pose").click().run()
    assert not app.exception
    saved = saved_camera(engine,lid,"desk_area")
    assert saved["heightMeters"] == 1.5 and saved["pitch"] == 0
    assert saved["floorPlanSetup"]["position"] == setup["position"]
    assert saved["yaw"] == 135 and saved["fov"] == 85
    fresh = AppTest.from_function(display,args=(engine,lid)).run()
    assert not fresh.exception
    assert received[-1]["pose"] == {"heightMeters":1.5,"pitch":0}
    assert not provider.calls


def test_generated_detail_receives_saved_height_tilt_and_side_guide(master_pack):
    engine,lid,provider,_ = prepare(master_pack)
    state = generate(engine,lid,"desk_area","Desk area","Desk from above",
        camera_setup={"position":{"x":0,"y":0},"yaw":0,"heightMeters":2.2,"pitch":-25},fov=80)
    detail = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    metadata = state.records[detail["key"]].asset.generation_params
    assert metadata["camera"]["heightMeters"] == 2.2 and metadata["camera"]["pitch"] == -25
    assert metadata["camera"]["floorPlanSetup"]["pitch"] == -25
    prompt,refs = provider.calls[-1]
    assert "CAMERA POSE: height above floor 2.2 metres, tilt/pitch -25 degrees" in prompt
    assert "FINAL PERSPECTIVE CAMERA CONTRACT" in prompt
    assert "override the master framing" in prompt
    assert '"projection": "rectilinear_perspective"' in prompt
    assert metadata["cameraProjection"]["position"]["heightMeters"] == 2.2
    assert metadata["cameraProjection"]["horizontalFovDegrees"] == 80
    assert metadata["cameraProjection"]["pitchDegrees"] == -25
    assert metadata["cameraProjection"]["forwardWorld"][2] < 0
    assert refs[0].name == "camera_perspective.png"
    with Image.open(refs[0]) as guide:
        assert guide.size == (768,432)
    with Image.open(refs[1]) as guide:
        assert guide.height >= 900
    assert metadata["inputImageRoles"][:2] == ["perspective_pose_guide","camera_guide"]
    assert engine.local_path(metadata["perspectiveGuide"]["file"]) == refs[0]
    assert "World horizon is" in prompt and "Do not level the camera" in prompt


def test_perspective_camera_basis_and_fov_are_consistent():
    import math
    from story_channel.detail_camera import perspective_camera
    camera = perspective_camera({"floor_plan":"Office","object_layout":{"desk":"north"}},
        {"position":{"x":0,"y":0},"yaw":90,"heightMeters":2,"pitch":30},80)
    assert camera["forwardWorld"] == pytest.approx([math.sqrt(3)/2,0,.5])
    assert camera["rightWorld"] == pytest.approx([0,-1,0])
    assert camera["upWorld"] == pytest.approx([-.5,0,math.sqrt(3)/2])
    assert 0 < camera["verticalFovDegrees"] < 80
    assert camera["focalLengthPixels"][0] == camera["focalLengthPixels"][1]


@pytest.mark.parametrize("pose",[{"heightMeters":0},{"heightMeters":3},{"heightMeters":float("nan")},
    {"pitch":61},{"pitch":float("inf")}])
def test_invalid_pose_is_rejected(pose):
    with pytest.raises(ValueError):
        validate({"floor_plan":"Office","object_layout":{"desk":"north wall"}},
            {"position":{"x":0,"y":0},"yaw":0,**pose})


def test_pose_pointer_drag_commits_once_on_release_and_cancel_does_not_save():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for component interaction verification")
    from story_channel.detail_camera_pose_ui import JS
    harness = r'''
const handlers={},updates=[];let capture=false;
globalThis.ResizeObserver=class{observe(){}disconnect(){}};
const ctx=new Proxy({}, {get:()=>()=>{}});
const canvas={getContext:()=>ctx,getBoundingClientRect:()=>({left:0,top:0,width:760,height:300}),
 addEventListener:(name,fn)=>handlers[name]=fn,setPointerCapture:()=>capture=true,
 hasPointerCapture:()=>capture,releasePointerCapture:()=>capture=false};
const note={textContent:''};
const cleanup=setup({parentElement:{querySelector:s=>s==='canvas'?canvas:note},
 data:{roomHeight:3,pose:{heightMeters:1.5,pitch:0},disabled:false},setStateValue:(key,v)=>updates.push({...v})});
const e=(x,y)=>({button:0,clientX:x,clientY:y,pointerId:1,preventDefault(){}});
handlers.pointerdown(e(130,190));handlers.pointermove(e(130,155));
if(updates.length)throw Error('Do not save while height is dragging');
handlers.pointerup(e(130,155));
if(updates.length!==1||Math.abs(updates[0].heightMeters-2)>.0001||updates[0].pitch!==0)throw Error('Height must move without changing tilt');
handlers.pointerdown(e(250,110));handlers.pointermove(e(230,60));
if(updates.length!==1)throw Error('Do not save while tilt is dragging');
handlers.pointerup(e(230,60));
if(updates.length!==2||Math.abs(updates[1].pitch-26.565)>.01||updates[1].heightMeters!==2)throw Error('Tilt must rotate without moving height');
handlers.pointerdown(e(130,200));handlers.pointermove(e(130,100));handlers.pointercancel(e(130,100));
if(updates.length!==2)throw Error('Cancel must not save');
cleanup();
'''
    result = subprocess.run([node,"--input-type=module","-e",JS.replace("export default function","const setup = function",1)+harness],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0,result.stderr
