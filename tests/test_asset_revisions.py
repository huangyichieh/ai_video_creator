import pytest

from story_channel.assets_status import assets_status_rows
from story_channel.costs import CostTracker
from test_assets import assets, complete


def test_apply_old_reference_preserves_provenance_requires_approval_and_keeps_revision_numbers(assets):
    complete(assets)
    first = assets.load().records["character_references:keeper"]
    history_file = assets.path / "history/assets/character_references/keeper" / f"{first.asset.asset_id}.json"
    original_history = history_file.read_bytes()
    assets.generate("character_references", subject="keeper", force=True)
    second = assets.load().records["character_references:keeper"]
    assert second.revision == 2
    assert any(r.status == "stale" for r in assets.inspect().records.values() if r.stage == "scene_images")
    assets.set_style_prompt("character_references", "keeper", "Soft watercolour lighting")
    assert assets.load().records["character_references:keeper"].status == "stale"
    costs = len(CostTracker(assets.manager, assets.project_id).records())
    state = assets.apply_revision("character_references", "keeper", first.asset.asset_id)
    selected = state.records["character_references:keeper"]
    assert selected.revision == 1 and selected.asset.asset_id == first.asset.asset_id
    assert selected.input_hash == first.input_hash and selected.applied_input_hash != first.input_hash
    assert selected.status == "succeeded" and state.next_stage == "character_review"
    assert not assets.manager.load(assets.project_id).asset_approvals
    assert history_file.read_bytes() == original_history
    assert len(CostTracker(assets.manager, assets.project_id).records()) == costs
    rows = assets_status_rows(assets, state)
    row = next(r for r in rows if r["Stage"] == "character_references")
    assert row["Revision"] == 1 and row["Status"] == "succeeded" and row["Approval"] == "Not approved"
    assets.approve("characters")
    assert "characters" in assets.manager.load(assets.project_id).asset_approvals
    assets.generate("character_references", subject="keeper", force=True)
    assert assets.load().records["character_references:keeper"].revision == 3
    assert {r.revision for r in assets.revisions("character_references", "keeper")} == {1, 2, 3}


def test_apply_background_clears_its_approval_and_rejects_missing_or_modified_revisions(assets):
    assets.generate("character_references")
    assets.approve("characters")
    assets.generate("backgrounds")
    assets.approve("backgrounds")
    first = next(r for r in assets.load().records.values() if r.stage == "backgrounds")
    assets.generate("backgrounds", subject=first.subject, force=True)
    assets.approve("backgrounds")
    state = assets.apply_revision("backgrounds", first.subject, first.asset.asset_id)
    assert state.next_stage == "background_review"
    assert "characters" in assets.manager.load(assets.project_id).asset_approvals
    assert "backgrounds" not in assets.manager.load(assets.project_id).asset_approvals
    assets.approve("backgrounds")
    assets.local_path(first.asset.file_path).write_bytes(b"modified image")
    with pytest.raises(ValueError, match="missing or modified"):
        assets.apply_revision("backgrounds", first.subject, first.asset.asset_id)
    with pytest.raises(ValueError, match="missing or modified"):
        assets.apply_revision("backgrounds", first.subject, "unknown-id")


def test_unrelated_voice_model_changes_keep_current_reference_files_and_approvals(assets):
    assets.generate("character_references")
    assets.approve("characters")
    assets.generate("backgrounds")
    assets.approve("backgrounds")
    assets.settings.tts_model = "another-voice-model"
    state = assets.inspect()
    assert all(r.status == "succeeded" for r in state.records.values())
    assert {"characters", "backgrounds"} <= assets.manager.load(assets.project_id).asset_approvals.keys()


def test_revision_browser_preview_does_not_apply_and_apply_refreshes_table(tmp_path, monkeypatch):
    from story_channel.assets import AssetPipeline
    from test_assets_ui import prepare
    from test_identity_ui import open_assets
    from test_ui import finish_background
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    engine.generate("character_references")
    first = next(r for r in engine.load().records.values() if r.stage == "character_references")
    engine.generate("character_references", subject=first.subject, force=True)
    engine.approve("characters")
    app = open_assets(manager, monkeypatch)
    prefix = f"reference-revisions-{project.project_id}-character_references-{first.subject}"
    app.selectbox(key=prefix + "-choice").select(first.asset.asset_id).run()
    assert not app.exception
    assert engine.load().records[f"character_references:{first.subject}"].revision == 2
    assert "characters" in manager.load(project.project_id).asset_approvals
    app.button(key=prefix + "-apply").click().run()
    finish_background(app)
    assert not app.exception
    assert app.button(key=prefix + "-apply").disabled
    rows = next(frame.value for frame in app.dataframe if "Regenerate" in frame.value.columns)
    row = rows[(rows.Stage == "character_references") & (rows.Subject == first.subject)].iloc[0]
    assert row.Revision == 1 and row.Status == "succeeded" and row.Approval == "Not approved"
    assert engine.load().next_stage == "character_review"
