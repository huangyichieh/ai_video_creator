import json
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.storyboard_asset_check import check,approve,require,bind_sound
from story_channel.files import atomic_write
from story_channel.pipeline import ApprovalRequired
from test_scene_production import episode
from test_studio import world


def test_created_assets_are_automatically_approved_without_receipts_or_buttons(episode):
    world,project,pipeline = episode
    (pipeline.path/"storyboard_asset_approvals.json").unlink(missing_ok=True)
    rows = check(world.manager,project.project_id)
    assert all(row["Ready"] and row["Approved"] for row in rows)
    require(world.manager,project.project_id)
    def display(manager,project_id):
        from story_channel.storyboard_asset_check_ui import render
        render(manager,project_id)
    app = AppTest.from_function(display,args=(world.manager,project.project_id),default_timeout=25).run()
    assert not app.exception
    assert not any(button.label in ("Approve asset","Approve prop") for button in app.button)
    row = next(row for row in rows if row["Category"] == "SFX")
    assert any(panel.key == f"check-sfx-{project.project_id}-{row['ID']}-card" for panel in app.expander)
    assert next(r for r in check(world.manager,project.project_id) if r["ID"] == row["ID"])["Approved"]
    assert any("Approval" in frame.value.columns and "Automatically approved" in list(frame.value["Approval"]) for frame in app.dataframe)


def test_intact_changed_asset_stays_automatically_ready(episode):
    world,project,pipeline = episode
    rows = check(world.manager,project.project_id)
    assert all(row["Approved"] for row in rows)
    row = next(row for row in rows if row["Category"] == "SFX")
    source = json.loads((pipeline.path/"storyboard_sfx.json").read_text(encoding="utf-8"))["entries"][row["ID"]]
    atomic_write(pipeline.path/(source["file"]+".source.json"),json.dumps({"file":source["file"],"source":"Updated audio provenance"}))
    bind_sound(world.manager,project.project_id,row["Asset"],source["file"])
    changed = check(world.manager,project.project_id)
    current = next(r for r in changed if r["ID"] == row["ID"])
    assert current["Ready"] and current["Approved"]
    assert all(r["Approved"] for r in changed if r["ID"] != row["ID"])
    with pytest.raises(ValueError,match="asset changed"):
        approve(world.manager,project.project_id,"SFX",row["ID"],expected=row["ContentHash"])
    approve(world.manager,project.project_id,"SFX",row["ID"],expected=current["ContentHash"])
    require(world.manager,project.project_id)


def test_missing_files_cannot_be_approved(episode):
    world,project,pipeline = episode
    source = json.loads((pipeline.path/"storyboard_sfx.json").read_text(encoding="utf-8"))["entries"]
    row = next(r for r in check(world.manager,project.project_id) if r["Category"] == "SFX")
    (pipeline.path/source[row["ID"]]["file"]).unlink()
    with pytest.raises(ValueError,match="Create this asset"):
        approve(world.manager,project.project_id,"SFX",row["ID"],expected=row["ContentHash"])
