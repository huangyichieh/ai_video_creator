from io import BytesIO

from PIL import Image
import pytest

from story_channel.background_library import BackgroundLibrary
from story_channel.location_details import generate,current,relevance
from story_channel.location_references import LocationReferenceResolver,scene_reference_guidance
from story_channel import location_pairs as pairs
from test_location_master_pack import master_pack,approve_pack
from test_studio import world


def png(color="red"):
    data = BytesIO()
    Image.new("RGB",(80,60),color).save(data,format="PNG")
    return data.getvalue()


def prepare(master_pack):
    engine,lid,provider,guides = master_pack
    engine.generate("backgrounds",subject=lid)
    approve_pack(engine,lid)
    return engine,lid,provider,guides


def test_master_is_primary_and_only_relevant_details_are_selected(master_pack):
    engine,lid,_,guides = prepare(master_pack)
    library = BackgroundLibrary(engine)
    library.add_detail_reference(lid,"Desk area",png(),detail_id="desk_area",object_ids=["desk"],position={"x":2,"y":3})
    state = library.add_detail_reference(lid,"Entrance area",png("blue"),detail_id="entrance_area",object_ids=["door"])
    desk = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    entrance = state.location_reference_sets[lid]["detail_references"]["entrance_area"]
    assert desk["objectIds"] == [guides["objectIds"]["desk"]]
    resolver = LocationReferenceResolver(engine,state)
    north,south = [f"backgrounds:{lid}__{name}" for name in ("front_panorama","back_panorama")]
    assert resolver.resolve(lid,0,target_area="desk_area") == [north,desk["key"]]
    assert resolver.resolve(lid,180,interaction_target=guides["objectIds"]["door"]) == [south,entrance["key"]]
    assert resolver.resolve(lid,0,character_position={"x":2.2,"y":3.1}) == [north,desk["key"]]
    assert resolver.resolve(lid,0) == [north]
    assert resolver.resolve(lid,0,target_area="desk_area",max_references=1) == [north]
    assert resolver.resolve(lid,0,target_area="desk_area",interaction_target="entrance_area") == [north,entrance["key"],desk["key"]]
    assert resolver.resolve(lid,0,target_area="desk_area",prefer_details=True) == [desk["key"],north]
    assert resolver.resolve(lid,0,target_area="desk_area",prefer_details=True,max_references=1) == [desk["key"]]
    assert resolver.resolve(lid,0,prefer_details=True) == [north]
    guidance = scene_reference_guidance(state,["character_references:keeper",desk["key"],north])
    assert "primary background plate" in guidance and "place the requested characters" in guidance


def test_replacing_detail_preserves_identity_and_revision_history(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    library = BackgroundLibrary(engine)
    state = library.add_detail_reference(lid,"Desk area",png(),detail_id="desk_area")
    first = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    state = library.add_detail_reference(lid,"Desk close up",png("blue"),detail_id="desk_area")
    latest = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    assert first["key"] == latest["key"]
    assert first["file"] != latest["file"]
    assert state.records[latest["key"]].revision == 2
    assert len(state.location_reference_sets[lid]["detail_references"]) == 1
    assert len(list((engine.path/"history"/"assets"/"backgrounds"/state.records[latest["key"]].subject).glob("*.json"))) == 1


def test_details_are_optional_and_stale_details_cannot_override_new_masters(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    library = BackgroundLibrary(engine)
    state = library.add_detail_reference(lid,"Desk area",png(),detail_id="desk_area")
    detail = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    assert current(engine,state,lid,detail)
    pairs.generate(engine,lid,"masters","New visual revision")
    state = approve_pack(engine,lid)
    assert not current(engine,state,lid,detail)
    assert LocationReferenceResolver(engine,state).resolve(lid,0,target_area="desk_area") == [f"backgrounds:{lid}__front_panorama"]
    assert LocationReferenceResolver(engine,state).resolve(lid,0,target_area="desk_area",prefer_details=True) == [f"backgrounds:{lid}__front_panorama"]
    state = library.approve(lid)
    assert library.approved(lid,state)  # Optional stale details never block required masters.


def test_ai_detail_uses_master_and_guide_and_records_local_only_role(master_pack):
    engine,lid,provider,guides = prepare(master_pack)
    state = generate(engine,lid,"desk_area","Desk area","Refine the wood texture",["desk"],yaw=0)
    assert len(provider.calls) == 5
    prompt,refs = provider.calls[-1]
    assert "not a new room design" in prompt
    assert "moving, resizing, duplicating or removing" in prompt
    assert "scene-ready local background plate" in prompt and "later character placement" in prompt
    assert refs[1] == engine.local_path(state.records[f"backgrounds:{lid}__front_panorama"].asset.file_path)
    assert refs[0] == engine.local_path(guides["views"]["front_panorama"]["cameraGuide"])
    ref = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    assert ref["role"] == "local_detail_only"
    assert current(engine,state,lid,ref)


def test_detail_can_request_new_camera_perspective_from_both_masters(master_pack):
    engine,lid,provider,guides = prepare(master_pack)
    state = generate(engine,lid,"desk_area","Desk area","Low-angle close-up of the existing desk",["desk"],
        yaw=135,pitch=-15,fov=45,camera_position="Beside the entrance, camera height 1.2 meters")
    prompt,refs = provider.calls[-1]
    assert refs[1] == engine.local_path(state.records[f"backgrounds:{lid}__east_panorama"].asset.file_path)
    assert refs[0] == engine.local_path(guides["views"]["east_panorama"]["cameraGuide"])
    assert refs[2] == engine.local_path(state.records[f"backgrounds:{lid}__back_panorama"].asset.file_path)
    assert "not a panorama or a copy" in prompt
    assert "move only the camera" in prompt and "Low-angle close-up" in prompt
    reference = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    params = state.records[reference["key"]].asset.generation_params
    assert params["camera"] == {"yaw":135,"pitch":-15,"fov":45,
        "positionDescription":"Beside the entrance, camera height 1.2 meters"}
    assert params["prompt"] == prompt and current(engine,state,lid,reference)


@pytest.mark.parametrize("camera", [{"yaw":float("nan")},{"pitch":80},{"fov":0}])
def test_invalid_detail_camera_does_not_call_provider(master_pack,camera):
    engine,lid,provider,_ = prepare(master_pack)
    before = len(provider.calls)
    with pytest.raises(ValueError,match="camera yaw"):
        generate(engine,lid,"desk_area","Desk area","Desk close-up",[],**camera)
    assert len(provider.calls) == before


def test_invalid_id_or_unknown_layout_object_is_rejected(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    for kwargs in ({"detail_id":"../escape"},{"detail_id":"desk_area","object_ids":["nonexistent"]}):
        with pytest.raises(ValueError):
            BackgroundLibrary(engine).add_detail_reference(lid,"Desk area",png(),**kwargs)


def test_character_position_uses_registered_object_footprint():
    from test_floor_plan_guides import plan
    spec = {"camera_plan":plan().model_dump(mode="json")}
    item = plan().objects[0]
    point = {"x":sum(p.x for p in item.footprint)/len(item.footprint),"y":sum(p.y for p in item.footprint)/len(item.footprint)}
    assert relevance(spec,{"objectIds":["PRINTER_01"]},"printer_area",character_position=point,ids={item.source_name:"PRINTER_01"}) > 0


def test_detail_ui_exposes_camera_setup_without_removed_fields(master_pack):
    from streamlit.testing.v1 import AppTest
    engine,lid,_,_ = prepare(master_pack)
    from story_channel.location_details import add_draft
    add_draft(engine,lid,"desk_area","Desk area","Wooden desk close-up")
    def display(engine,lid):
        from story_channel.location_details_ui import render
        render(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert any(field.label == "Stable detail ID" for field in app.text_input)
    assert not any(field.label == "Linked layout objects" for field in app.multiselect)
    assert any(field.label == "Detail prompt" for field in app.text_area)
    assert "Field of view (degrees)" in {field.label for field in app.number_input}
    assert not {"Camera yaw (degrees)","Camera pitch (degrees)"} & {field.label for field in app.number_input}
    assert any("Camera setup" in item.value for item in app.markdown)
    assert not app.get("file_uploader")
    assert any(button.label == "Generate detail" for button in app.button)
