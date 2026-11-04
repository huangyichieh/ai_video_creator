from pathlib import Path

from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.background_library import BackgroundLibrary
from story_channel.asset_models import AssetRecord
from story_channel.models import GeneratedAsset
from story_channel.media_utils import file_hash
from story_channel.location_references import LocationReferenceSet,view_names
from test_location_master_pack import master_pack
from test_studio import world


def test_new_locations_have_one_workflow_without_selecting_type(master_pack):
    engine,_,_,_ = master_pack
    lid = BackgroundLibrary(engine).add("Meeting room","A round table beside the window").location_id
    spec = engine.load().location_reference_sets[lid]
    assert view_names(spec) == ["front_panorama","back_panorama"]
    assert spec["generation_method"] == "dual_panorama"
    assert LocationReferenceSet(reference_type="ONE_TIME").reference_type == "KEY_360"
    assert LocationReferenceSet().reference_type == "KEY_360"


def test_simple_registration_migrates_without_deleting_media(master_pack):
    engine,lid,_,_ = master_pack
    state = engine.load()
    state.location_reference_sets[lid]["reference_type"] = "ONE_TIME"
    state.location_workflow_version = 1
    path = engine.path/"images"/"old_simple.png"
    path.parent.mkdir(exist_ok=True)
    Image.new("RGB",(16,16),"red").save(path)
    subject = lid+"__master_wide"
    asset = GeneratedAsset(type="image",provider="demo",model="old-simple",file_path=path.relative_to(engine.path).as_posix(),prompt_hash="a"*64)
    state.records["backgrounds:"+subject] = AssetRecord(stage="backgrounds",subject=subject,status="succeeded",asset=asset,file_hash=file_hash(path))
    engine._save(state)
    state = engine.inspect()
    assert state.location_reference_sets[lid]["reference_type"] == "KEY_360"
    assert state.location_workflow_version == 2
    assert path.is_file()
    assert list((engine.path/"history"/"assets"/"backgrounds"/subject).glob("*.json"))
    tasks = [task for task in engine.tasks(state) if task.stage == "backgrounds" and task.subject.startswith(lid+"__")]
    assert {task.subject for task in tasks} == {lid+"__front_panorama",lid+"__back_panorama"}
    BackgroundLibrary(engine).configure_references(lid,"ONE_TIME","Same room",{"desk":"north"})
    assert engine.load().location_reference_sets[lid]["reference_type"] == "KEY_360"


def test_background_ui_offers_detail_and_simple_location_types(master_pack):
    engine,lid,_,_ = master_pack
    def display(engine):
        from story_channel.background_assets_ui import background_cards
        background_cards(engine)
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    assert not app.exception
    selectors = [widget for widget in app.selectbox if widget.label == "Location type"]
    assert selectors
    assert all(widget.options == ["DETAIL","SIMPLE"] for widget in selectors)
    assert any(button.key == "generate-location-pair-"+lid+"-masters" for button in app.button)
