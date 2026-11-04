import shutil
import subprocess

from PIL import Image
import pytest

from story_channel.detail_camera import validate
from story_channel.location_details import generate
from test_location_details import prepare
from test_location_master_pack import master_pack
from test_studio import world


def test_floor_plan_camera_is_passed_to_generation_without_resizing(master_pack):
    engine,lid,provider,_ = prepare(master_pack)
    state = generate(engine,lid,"entrance_area","Entrance","A detailed view of the entrance",camera_setup={
        "position":{"x":.5,"y":-1},"yaw":180})
    ref = state.location_reference_sets[lid]["detail_references"]["entrance_area"]
    record = state.records[ref["key"]]
    with Image.open(engine.local_path(record.asset.file_path)) as image:
        assert list(image.size) == record.asset.generation_params["providerImageSize"]
    params = record.asset.generation_params
    assert params["camera"]["floorPlanSetup"]["position"] == {"x":.5,"y":-1}
    assert params["camera"]["yaw"] == 180
    assert params["requestedSize"] == "1536x864"
    prompt,refs = provider.calls[-1]
    assert "red dot is the requested camera position" in prompt and "1536x864" in prompt
    assert refs[0].name == "camera_perspective.png" and refs[0].is_file()
    assert refs[1].name == "camera_setup.png" and refs[1].is_file()
    with Image.open(refs[1]) as guide:
        import numpy as np
        pixels = np.asarray(guide.convert("RGB"))
        assert ((pixels[:,:,0]>200)&(pixels[:,:,1]<70)&(pixels[:,:,2]<100)).sum() > 100


@pytest.mark.parametrize("setup",[{}, {"position":{"x":100,"y":0},"yaw":0},
    {"position":{"x":0,"y":0},"yaw":float("nan")}])
def test_camera_position_rejects_invalid_or_outside_room(setup):
    with pytest.raises(ValueError):
        validate({"floor_plan":"Office","object_layout":{"desk":"north"}},setup)


@pytest.mark.parametrize("fov",[20,70,110])
def test_pointer_drag_saves_only_on_release_with_fov_fan(fov):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for component interaction verification")
    from story_channel.detail_camera_ui import JS
    harness = r'''
const handlers={},arcs=[],updates=[];let capture=false;
globalThis.ResizeObserver=class{observe(){}disconnect(){}};
const ctx=new Proxy({}, {get:(obj,key)=>key==='arc'?((...args)=>arcs.push(args)):(()=>{})});
const canvas={getContext:()=>ctx,getBoundingClientRect:()=>({left:0,top:0,width:800,height:600}),
 addEventListener:(name,fn)=>handlers[name]=fn,setPointerCapture:()=>capture=true,
 hasPointerCapture:()=>capture,releasePointerCapture:()=>capture=false};
const note={textContent:''};
const data={fov:TEST_FOV,transform:{minX:0,minY:0,scale:50},boundary:[{x:0,y:0},{x:10,y:0},{x:10,y:10},{x:0,y:10}],objects:[],camera:null,disabled:false};
const cleanup=setup({parentElement:{querySelector:s=>s==='canvas'?canvas:note},data,
 setStateValue:(key,value)=>updates.push(JSON.parse(JSON.stringify(value)))});
const e=(x,y)=>({button:0,clientX:x,clientY:y,pointerId:1,preventDefault(){}});
handlers.pointerdown(e(300,300));
handlers.pointermove(e(310,300));
if(updates.length)throw Error('Must not rerun while dragging');
const fan=arcs.filter(a=>a[2]===120).at(-1);
if(!fan||Math.abs((fan[4]-fan[3])*180/Math.PI-data.fov)>.00001)throw Error('Fan angle must equal FOV');
if(Math.abs((fan[3]+fan[4])/2)>.00001)throw Error('Fan must face east at yaw 90');
handlers.pointermove(e(500,300));handlers.pointerup(e(500,300));
if(updates.length!==1||updates[0].yaw!==90||updates[0].position.x!==5||updates[0].position.y!==5)throw Error('Wrong committed camera');
handlers.pointermove(e(300,100));
if(updates.length!==1)throw Error('Direction must stop changing after release');
handlers.pointerdown(e(300,300));handlers.pointercancel(e(300,300));
if(updates.length!==1)throw Error('Cancelled drags must not save');
handlers.pointerdown(e(790,590));handlers.pointerup(e(790,590));
if(updates.length!==1)throw Error('Outside-room camera must not save');
cleanup();
'''
    result = subprocess.run([node,"--input-type=module","-e",JS.replace("export default function", "const setup = function",1)+harness.replace("TEST_FOV",str(fov))],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0,result.stderr


def test_add_detail_creates_draft_without_media_or_provider_calls(master_pack):
    from story_channel.location_details import add_draft,current
    engine,lid,provider,_ = master_pack
    state = add_draft(engine,lid,"desk_area","Desk area","A desk close-up")
    ref = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    assert ref["status"] == "draft" and "file" not in ref
    assert ref["key"] not in state.records and not current(engine,state,lid,ref)
    assert not provider.calls  # Drafts also work before master approval.


def test_replacing_detail_draft_retains_paid_revision_until_generated(master_pack):
    from story_channel.location_details import add_draft,current
    engine,lid,provider,_ = prepare(master_pack)
    state = generate(engine,lid,"desk_area","Desk area","Original desk")
    ref = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    original = state.records[ref["key"]].asset.asset_id
    calls = len(provider.calls)
    state = add_draft(engine,lid,"desk_area","New desk angle","A different camera view")
    draft = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    assert len(provider.calls) == calls
    assert state.records[draft["key"]].asset.asset_id == original
    assert engine.local_path(draft["file"]).is_file()
    assert not current(engine,state,lid,draft)
    state = generate(engine,lid,"desk_area",draft["area"],draft["description"])
    applied = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    assert current(engine,state,lid,applied) and applied.get("status") != "draft"
    assert state.records[applied["key"]].revision == 2


def test_add_draft_ui_creates_box_before_showing_generation(master_pack):
    from streamlit.testing.v1 import AppTest
    engine,lid,provider,_ = prepare(master_pack)
    def display(engine,lid):
        from story_channel.location_details_ui import render
        render(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert not any(button.label == "Generate detail" for button in app.button)
    prefix = f"location-details-{engine.project_id}-{lid}"
    app.text_input(key=prefix+"-id").set_value("desk_area").run()
    app.text_input(key=prefix+"-area").set_value("Desk area").run()
    app.text_area(key=prefix+"-description").set_value("Close-up of the wooden desk").run()
    app.button(key=prefix+"-add-draft").click().run()
    assert not app.exception
    assert len(provider.calls) == 4
    assert any("Draft — not generated" in item.value for item in app.markdown)
    assert any(button.label == "Generate detail" for button in app.button)
    assert any(item.label == "Add detail background" for item in app.expander)
    assert app.text_input(key=prefix+"-id").value == ""
    assert app.text_input(key=prefix+"-area").value == ""
    assert app.text_area(key=prefix+"-description").value == ""
    assert any(field.value == "Close-up of the wooden desk" for field in app.text_area)
    # Adding another draft must use the new form ID, not the loop's last card ID.
    app.text_input(key=prefix+"-id").set_value("window_area").run()
    app.text_input(key=prefix+"-area").set_value("Window area").run()
    app.text_area(key=prefix+"-description").set_value("Look toward the north window").run()
    app.button(key=prefix+"-add-draft").click().run()
    assert not app.exception
    details = engine.load().location_reference_sets[lid]["detail_references"]
    assert set(details) == {"desk_area","window_area"}
    assert details["desk_area"]["description"] == "Close-up of the wooden desk"
    assert details["window_area"]["description"] == "Look toward the north window"
    assert any(field.value == "Look toward the north window" for field in app.text_area)
    assert len(provider.calls) == 4
