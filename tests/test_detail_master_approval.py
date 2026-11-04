from story_channel.background_library import BackgroundLibrary
from story_channel.files import input_hash
from story_channel.location_details import add_draft,generate,remove
from story_channel.location_references import LocationReferenceResolver
from test_location_details import prepare,png
from test_location_master_pack import master_pack,approve_pack
from test_studio import world


def test_optional_detail_changes_preserve_master_approval(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    library = BackgroundLibrary(engine)
    state = library.approve(lid)
    approved = state.background_approvals[lid].content_hash
    def check(state):
        assert library.approved(lid,state)
        assert state.background_approvals[lid].content_hash == approved
    state = add_draft(engine,lid,"desk_area","Desk area","Close-up")
    check(state)
    check(engine.inspect())
    state = generate(engine,lid,"desk_area","Desk area","Refine wood grain",yaw=0)
    check(state)
    reference = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    state = generate(engine,lid,"desk_area","Desk area","",zoom={"expected":input_hash(reference),
        "rectangle":{"left":.1,"top":.1,"right":.9,"bottom":.9}})
    check(state)
    state = remove(engine,lid,"desk_area",confirmation="desk_area")
    check(state)
    assert "desk_area" in state.location_reference_sets[lid]["removed_detail_ids"]
    state = library.add_detail_reference(lid,"Desk area",png(),detail_id="desk_area")
    assert "desk_area" not in state.location_reference_sets[lid]["removed_detail_ids"]
    check(state)


def test_master_changes_still_require_master_review(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    library = BackgroundLibrary(engine)
    library.approve(lid)
    from story_channel.location_pairs import generate as generate_pair
    state = generate_pair(engine,lid,"masters","Updated lighting")
    assert not library.approved(lid,state)
    state = approve_pack(engine,lid)
    assert library.approved(lid,state)


def test_previously_cleared_background_approval_restores_from_approved_masters(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    library = BackgroundLibrary(engine)
    library.add_detail_reference(lid,"Desk area",png(),detail_id="desk_area")
    state = engine.load()
    state.background_approvals.pop(lid,None)
    engine._save(state)
    assert library.approved(lid,engine.inspect())


def test_animation_resolver_uses_only_generated_current_detail_images(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    library = BackgroundLibrary(engine)
    add_draft(engine,lid,"draft_area","Draft area","Not generated")
    state = library.add_detail_reference(lid,"Desk area",png(),detail_id="desk_area")
    detail = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    def selected(state,area):
        return LocationReferenceResolver(engine,state).resolve(lid,target_area=area,prefer_details=True)
    assert all("__detail_" not in key for key in selected(state,"draft_area"))
    assert selected(state,"desk_area")[0] == detail["key"]
    engine.local_path(detail["file"]).unlink()
    assert detail["key"] not in selected(state,"desk_area")
    assert library.approved(lid,engine.inspect())
