from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from story_channel.assets_status import assets_status_rows
from story_channel.identity import IdentityManager
from test_assets_ui import prepare


def indexed(engine):
    return {(r["Stage"], r["Subject"]): r for r in assets_status_rows(engine, engine.inspect())}


def test_status_covers_every_tab_and_ungenerated_requirements(tmp_path):
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    rows = assets_status_rows(engine, engine.inspect())
    assert {row["Tab"] for row in rows} >= {"Character Reference Packs", "Locations / Backgrounds", "Costumes", "Props", "Voice Profiles", "Style References", "Music / SFX library", "Reusable generated images"}
    assert all(row["Status"] == "Not generated" for row in rows if row["Stage"] in ("character_references", "backgrounds"))
    assert all(row["Approval"] != "Approved" for row in rows)
    assert any(row["Stage"] == "identity_approval" and "Voice Profiles" in row["Checklist"] for row in rows)
    assert any(row["Stage"] == "voice_auditions" and "Optional" in row["Status"] for row in rows)
    assert any(row["Stage"] == "voice_approval" and row["Status"] == "Blocked" for row in rows)


def test_approvals_track_current_files_and_all_generated_emotions(tmp_path):
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    changed = library.model_copy(deep=True)
    for voice in changed.voices.values():
        voice.voice_id = "nova"
    identity.save(changed, expected=library.model_dump_json(by_alias=True))
    engine = AssetPipeline(manager, project.project_id)
    engine.generate("character_references")
    engine.approve("characters")
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    rows = indexed(engine)
    assert rows["character_references_approval", "All references"]["Approval"] == "Approved"
    assert rows["backgrounds_approval", "All references"]["Approval"] == "Approved"
    assert rows["identity_approval", "Identity and reference library"]["Approval"] != "Approved"
    identity.approve()
    director = AnimationDirector(manager, project.project_id)
    state = director.prepare()
    cid = next(iter(state.plan.voices))
    text = "Keep the beacon burning. We will find our way through this storm together, one step at a time."
    director.preview_voice(cid, text)
    director.preview_voice(cid, text, emotion="happy")
    from story_channel.assets_workflow import next_assets_action
    pending = next_assets_action(engine, engine.inspect())
    assert pending.stage == "voice_approval" and cid in pending.detail
    director.approve_voice(cid)
    assert next_assets_action(engine, engine.inspect()).stage == "complete"
    rows = indexed(engine)
    assert rows["identity_approval", "Identity and reference library"]["Approval"] == "Approved"
    assert rows["voice_approval", cid]["Approval"] == "Approved"
    assert rows["voice_auditions", f"{cid} · happy"]["Approval"] == "Approved"
    assert rows["voice_auditions", f"{cid} · angry"]["Status"] == "Optional · not generated"
    sample = director.load().emotion_previews[cid]["happy"]
    engine.local_path(sample.asset.file_path).write_bytes(b"damaged audio")
    rows = indexed(engine)
    assert rows["voice_auditions", f"{cid} · happy"]["Status"] == "Outdated"
    assert rows["voice_auditions", f"{cid} · baseline"]["Approval"] != "Approved"
    assert rows["voice_approval", cid]["Status"] == "Blocked"
    running = assets_status_rows(engine, engine.inspect(), running_label=f"Previewing {cid}: happy")
    assert next(row for row in running if row["Stage"] == "voice_auditions" and row["Subject"] == f"{cid} · happy")["Status"] == "Generating…"


def test_checking_an_approval_row_cannot_approve_or_regenerate(tmp_path, monkeypatch):
    from test_identity_ui import open_assets
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    engine.generate("character_references")
    rows = assets_status_rows(engine, engine.inspect())
    index = next(i for i, row in enumerate(rows) if row["Stage"] == "character_references_approval")
    app = open_assets(manager, monkeypatch)
    key = f"production-selection-{project.project_id}-False"
    app.session_state[key] = {"edited_rows": {index: {"Regenerate": True}}, "added_rows": [], "deleted_rows": []}
    app.run()
    assert not app.exception and app.button(key="regenerate-selected-assets").disabled
    assert "characters" not in manager.load(project.project_id).asset_approvals
    assert "workspace-task" not in app.session_state
