import json

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.background_library import BackgroundLibrary
from story_channel.floor_plan_revisions import revisions,signature,preview,apply
from story_channel.floor_plan_guides import current_guides
from story_channel.location_details import add_draft
from story_channel.location_layout import generate_location_layout
from test_location_layout import layout
from test_location_details import prepare
from test_location_master_pack import master_pack
from test_studio import world


def test_preview_and_apply_restore_matched_layout_and_reopen_approval(master_pack):
    engine,lid,provider,_ = prepare(master_pack)
    old = dict(engine.load().location_reference_sets[lid])
    identity = signature(old)
    add_draft(engine,lid,"desk_area","Desk area")
    library = BackgroundLibrary(engine)
    library.configure_references(lid,floor_plan="New floor plan with east desk",object_layout={"desk":"east wall","door":"south wall"})
    changed = signature(engine.load().location_reference_sets[lid])
    assert changed != identity
    before = len(provider.calls)
    item = preview(engine,lid,identity)
    assert engine.local_path(item["guides"]["floorPlan"]).is_file()
    assert signature(engine.load().location_reference_sets[lid]) == changed
    assert current_guides(engine,lid,engine.load().location_reference_sets[lid]) is None
    apply(engine,lid,identity,expected=changed)
    state = engine.load()
    assert state.location_reference_sets[lid]["floor_plan"] == old["floor_plan"]
    assert state.location_reference_sets[lid]["object_layout"] == old["object_layout"]
    assert "desk_area" in state.location_reference_sets[lid]["detail_references"]
    assert current_guides(engine,lid,state.location_reference_sets[lid])
    assert not any(key.startswith(lid+":") for key in state.location_master_approvals)
    assert len(revisions(engine,lid)) == 2
    assert len(provider.calls) == before
    assert all(engine.local_path(record.asset.file_path).exists() for record in state.records.values() if record.asset)


def test_revision_apply_rejects_stale_expectation(master_pack):
    engine,lid,_,_ = master_pack
    original = signature(engine.load().location_reference_sets[lid])
    BackgroundLibrary(engine).configure_references(lid,floor_plan="Updated floor",object_layout={"desk":"west wall"})
    with pytest.raises(ValueError,match="another session"):
        apply(engine,lid,original,expected=original)
    assert engine.load().location_reference_sets[lid]["floor_plan"] == "Updated floor"


def test_revision_restores_matching_spatial_camera_plan(master_pack):
    from test_floor_plan_guides import plan
    from story_channel.floor_plan_guides import publish_guides
    engine,lid,_,_ = master_pack
    library = BackgroundLibrary(engine)
    objects = {"printer":"north wall","door":"south wall","window":"east wall"}
    original = plan()
    library.configure_references(lid,floor_plan="8 metre square; camera at origin",object_layout=objects,camera_plan=original.model_dump())
    state = engine.load()
    identity = signature(state.location_reference_sets[lid])
    publish_guides(engine,lid,state.location_reference_sets[lid])
    shifted = original.model_copy(deep=True)
    shifted.camera.x = 1
    library.configure_references(lid,floor_plan="8 metre square; camera shifted east",object_layout=objects,camera_plan=shifted.model_dump())
    apply(engine,lid,identity)
    restored = engine.load().location_reference_sets[lid]
    assert restored["camera_plan"]["camera"] == {"x":0,"y":0}
    assert restored["camera_plan"]["objects"] == original.model_dump(mode="json",by_alias=True)["objects"]
    assert current_guides(engine,lid,restored)["camera"] == {"x":0,"y":0}


def test_ai_layout_drafts_get_revisions_before_save(master_pack):
    engine,lid,_,_ = master_pack
    original = signature(engine.load().location_reference_sets[lid])
    class Provider:
        last_usage = {}
        def generate(self,prompt,schema,config):
            return layout()
    result = generate_location_layout(engine,lid,"KEY_360",provider=Provider())
    assert any(item["layout"]["floor_plan"] == result.floor_plan and item["source"].startswith("AI layout") for item in revisions(engine,lid))
    assert signature(engine.load().location_reference_sets[lid]) == original


def test_revision_dialog_previews_applies_and_refreshes_editor_fields(master_pack):
    engine,lid,provider,_ = master_pack
    original = dict(engine.load().location_reference_sets[lid])
    identity = signature(original)
    BackgroundLibrary(engine).configure_references(lid,floor_plan="Changed floor",object_layout={"desk":"east wall","door":"south wall"})
    def display(engine,lid):
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.background_assets_ui import reference_set_settings
        from story_channel.background_library import BackgroundLibrary
        show_pending_dialog()
        reference_set_settings(engine,BackgroundLibrary(engine),lid,engine.load())
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    app.button(key=f"floor-plan-revisions-{engine.project_id}-{lid}").click().run()
    assert not app.exception and app.get("dialog")
    app.selectbox(key=f"floor-plan-revision-choice-{engine.project_id}-{lid}").select(identity).run()
    assert not app.exception
    assert len(app.get("image")) >= 3
    app.button(key="apply-floor-plan-revision").click().run()
    assert not app.exception
    assert engine.load().location_reference_sets[lid]["floor_plan"] == original["floor_plan"]
    app.button(key="close-floor-plan-revisions").click().run()
    assert not app.exception
    prefix = f"location-reference-set-{engine.project_id}-{lid}"
    assert app.text_area(key=prefix+"-floor").value == original["floor_plan"]
    assert json.loads(app.text_area(key=prefix+"-layout").value) == original["object_layout"]
    assert not provider.calls
