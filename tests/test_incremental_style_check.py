from PIL import Image
from io import BytesIO

import pytest
from streamlit.testing.v1 import AppTest

from story_channel import style_check as review
from story_channel.costs import CostTracker
from story_channel.background_library import BackgroundLibrary,locations
from story_channel.floor_plan_guides import publish_guides
from story_channel.assets_workflow import next_assets_action
from story_channel.library_media import LibraryMedia
from test_style_rules import Reviewer
from test_location_master_pack import master_pack,approve_pack
from test_studio import world
from asset_test_helpers import complete_character_parts


def setup(master_pack):
    engine,lid,_,_ = master_pack
    engine.generate("character_references")
    engine.generate("backgrounds",subject=lid)
    return engine,lid


def test_unchanged_checks_are_free_and_only_new_revision_is_targeted(master_pack):
    engine,lid = setup(master_pack)
    provider = Reviewer()
    first = review.check_style(engine,provider=provider)
    assert len(first["reviewedThisRun"]) == 2
    before = len(CostTracker(engine.manager,engine.project_id).records())
    assert review.check_style(engine,provider=provider) == first
    assert len(provider.calls) == 1
    assert len(CostTracker(engine.manager,engine.project_id).records()) == before
    cid = engine.creative.artifact("characters").characters[0].character_id
    engine.generate("character_references",subject=cid,force=True)
    second = review.check_style(engine,provider=provider)
    assert second["reviewedThisRun"] == ["character_references:"+cid]
    content = provider.calls[-1][0]["content"]
    assert sum(item["type"] == "input_image" for item in content) == 2  # Context overview + single target.
    assert "CONTEXT ONLY" in content[0]["text"]
    assert not review.pending_candidates(engine,engine.load())


def test_new_background_detail_is_the_only_target_and_old_findings_are_retained(master_pack):
    engine,lid = setup(master_pack)
    key = review.candidates(engine,engine.load())[0]["key"]
    issue = review.StyleIssue(asset_key=key,reason="Ink differs",recommendation="Use shared linework")
    review.check_style(engine,provider=Reviewer([issue]))
    approve_pack(engine,lid)
    image = BytesIO()
    Image.new("RGB",(40,40),"red").save(image,format="PNG")
    state = BackgroundLibrary(engine).add_detail_reference(lid,"Desk area",image.getvalue(),detail_id="desk_area")
    detail = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    assert [entry["key"] for entry in review.pending_candidates(engine,state)] == [detail["key"]]
    result = review.check_style(engine,provider=Reviewer())
    assert result["reviewedThisRun"] == [detail["key"]]
    assert result["issues"] == [issue.model_dump(mode="json")]


def test_failed_incremental_review_keeps_previous_coverage_and_records_cost(master_pack):
    engine,lid = setup(master_pack)
    old = review.check_style(engine,provider=Reviewer())
    cid = engine.creative.artifact("characters").characters[0].character_id
    engine.generate("character_references",subject=cid,force=True)
    unchanged = "backgrounds:"+lid
    issue = review.StyleIssue(asset_key=unchanged,reason="Wrong target",recommendation="Do not flag context")
    with pytest.raises(Exception,match="unknown asset"):
        review.check_style(engine,provider=Reviewer([issue]))
    assert review.load_report(engine) == old
    assert len(review.pending_candidates(engine,engine.load())) == 1
    assert CostTracker(engine.manager,engine.project_id).records()[-1].outcome == "failed"


def test_optional_ui_disables_check_when_no_new_visuals(master_pack):
    engine,lid = setup(master_pack)
    review.check_style(engine,provider=Reviewer())
    def display(engine):
        from story_channel.style_rules_ui import style_check_controls
        style_check_controls(engine,"assets")
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    assert not app.exception
    assert next(button for button in app.button if button.label == "Style check").disabled
    assert any("Optional" in caption.value for caption in app.caption)


def test_background_approval_completes_required_flow_with_optional_work_undone(master_pack):
    engine,lid = setup(master_pack)
    complete_character_parts(engine)
    engine.approve("characters")
    library = BackgroundLibrary(engine)
    for location in locations(engine.creative):
        if location.location_id != lid:
            library.configure_references(location.location_id,floor_plan="Fixed room center",object_layout={"desk":"north wall"})
            publish_guides(engine,location.location_id,engine.load().location_reference_sets[location.location_id])
            engine.generate("backgrounds",subject=location.location_id)
        approve_pack(engine,location.location_id)
        library.approve(location.location_id)
    media = LibraryMedia(engine.manager,engine.project_id)
    media.add_prop("Optional mug","Ceramic mug",["whole","broken"])
    # No prop-mode images, no SFX, and no style report are required.
    assert not (engine.path/"style_check.json").exists()
    action = next_assets_action(engine,engine.inspect())
    assert action.stage == "complete"
    assert "optional" in action.detail.lower()
