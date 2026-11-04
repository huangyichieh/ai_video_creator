import json

from story_channel.floor_plan_guides import publish_guides
from story_channel.visual_generation import VisualGeneration
from test_shared_room import make_room
from test_floor_plan_guides import setup, plan
from test_studio import world


def test_existing_world_shared_room_returns_to_two_masters_without_deleting_media(world):
    engine,library,lid = make_room(world)
    state = engine.generate("backgrounds",subject=lid+"__room_panorama")
    old = state.records[f"backgrounds:{lid}__room_panorama"]
    path = engine.local_path(old.asset.file_path)
    previous = path.read_bytes()
    state.location_workflow_version = 0  # Simulate a project saved before the restored workflow.
    engine._save(state)
    state = engine.inspect()
    assert state.location_workflow_version == 1
    assert state.location_reference_sets[lid]["generation_method"] == "dual_panorama"
    tasks = [t for t in engine.tasks(state) if t.subject.startswith(lid+"__")]
    assert len(tasks) == 12
    assert [t.operation for t in tasks[:4]] == ["location_panorama"]*4
    assert all(t.operation == "location_projection" for t in tasks[4:])
    assert path.read_bytes() == previous
    assert not library.approved(lid,state)
    assert engine.inspect().location_workflow_version == 1


def test_master_prompt_and_images_use_camera_visible_objects_only(world):
    engine,library,lid = setup(world)
    floor = "8m square; PRINTER_01 on north; DOOR_01 on south; WINDOW_01 on east"
    objects = {"printer":"north","door":"south","window":"east"}
    library.configure_references(lid,"KEY_360",floor,objects,camera_plan=plan().model_dump())
    state = engine.load()
    guides = publish_guides(engine,lid,state.location_reference_sets[lid])
    from test_dual_panorama import approve_bridges
    approve_bridges(engine,lid)
    state = engine.load()
    visual = VisualGeneration(engine)
    subject = lid+"__front_panorama"
    inputs = visual.get("backgrounds",subject,state)
    inputs["prompt"] = "printer PRINTER_01 north; door DOOR_01 south; window WINDOW_01 east"
    task = next(t for t in engine.tasks(state) if t.subject == subject)
    # Even a legacy generic task prompt must not leak hidden objects into the request.
    task.prompt += " DOOR_01 door hidden behind the camera"
    prompt,refs = visual._panorama_request(state,lid,subject,inputs,task,shorten=False)
    assert "DOOR_01" not in prompt
    assert '"door"' not in prompt
    assert "PRINTER_01" in prompt and "WINDOW_01" in prompt
    assert refs[2] == engine.local_path(guides["views"]["front_panorama"]["visibleFloorPlan"])
    assert engine.local_path(guides["floorPlan"]) not in refs
    assert len(refs) == 5
    scope = json.loads(prompt.split("Visible view context: ",1)[1].split(" Camera guide metadata:",1)[0])
    assert set(scope["visibleObjects"]) == {"printer","window"}
