from pathlib import Path
from streamlit.testing.v1 import AppTest
from story_channel.config import Settings
from story_channel.projects import ProjectManager
from test_ui import finish_background
from test_scene_production import episode
from test_studio import world


def test_world_first_creation_navigation_assets_and_episode_outline(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    assert not app.exception
    assert {b.label for b in app.button if b.key and b.key.startswith("nav-")} == {"World", "Assets", "Story/Episode", "Animations", "Final", "Analytics"}
    assert not any(t.label == "Title" for t in app.text_input)
    next(t for t in app.text_area if t.label == "Premise").set_value("An island where memories power lighthouses.")
    next(b for b in app.button if b.label == "Create project").click().run()
    assert not app.exception
    app.button(key="nav-assets").click().run()
    assert any("Approve World" in i.value for i in app.info)
    app.button(key="nav-world").click().run()
    assert app.button(key="studio-world-approve").disabled
    app.button(key="studio-world-review").click().run()
    finish_background(app)
    assert app.button(key="studio-world-approve").disabled
    app.button(key="studio-world-revert").click().run()
    app.button(key="studio-world-approve").click().run()
    finish_background(app)
    assert not app.exception
    app.button(key="nav-assets").click().run()
    app.button(key="studio-add-character").click().run()
    finish_background(app)
    assert not app.exception
    assert {"Character References", "Background", "SFX", "Props", "Style check", "Visual", "Voice", "Costumes"} <= {tab.label for tab in app.tabs}
    app.button(key="nav-story-settings").click().run()
    next(t for t in app.text_input if t.label == "Episode title").set_value("First episode")
    next(t for t in app.text_area if t.label == "Story outline").set_value("The keeper saves a memory.")
    next(b for b in app.button if b.label == "Create episode").click().run()
    assert not app.exception
    assert {"Outline", "Story", "Script", "Storyboard"} <= {tab.label for tab in app.tabs}
    assert "Prompts" not in {tab.label for tab in app.tabs}
    manager = ProjectManager(Settings(data_root=root))
    episode = next(p for p in manager.list_projects() if p.world_project_id)
    app.button(key=f"episode-unchanged-{episode.project_id}-outline").click().run()
    finish_background(app)
    assert not app.exception
    assert "outline" in manager.load(episode.project_id).content_approvals
    draft_key = f"studio-draft-episode-{episode.project_id}-outline"
    revised = "The keeper rescues a visitor and discovers a missing memory."
    app.text_area(key=draft_key).set_value(revised).run()
    assert app.button(key=f"episode-generate-{episode.project_id}-story").disabled
    app.button(key="nav-world").click().run()
    app.button(key="nav-story-settings").click().run()
    assert app.text_area(key=draft_key).value == revised
    app.button(key="episode-save-outline-" + episode.project_id).click().run()
    assert not app.exception
    assert manager.read_outline(episode.project_id) == revised
    assert "outline" not in manager.load(episode.project_id).content_approvals
    app.button(key=f"episode-unchanged-{episode.project_id}-outline").click().run()
    finish_background(app)
    app.button(key=f"episode-generate-{episode.project_id}-story").click().run()
    finish_background(app)
    assert not app.exception
    assert manager.load(episode.project_id).steps["story"].status == "succeeded"


def test_script_review_has_individual_editable_decisions_and_retains_shared_world(episode, monkeypatch):
    world, project, pipeline = episode
    import json
    from story_channel.studio_ui import review_episode
    from story_channel.files import atomic_write, input_hash
    report = review_episode(pipeline, "script")
    proposed = pipeline.artifact("script").model_dump(mode="json", by_alias=True)
    proposed["scenes"][0]["purpose"] += " A clearer motivation."
    proposed["scenes"][0]["narration"] += " An optional extra thought."
    report.advice.proposed_text = json.dumps(proposed, ensure_ascii=False, indent=2)
    atomic_write(pipeline.path / "episode_script_review.json", report.model_dump_json(by_alias=True, indent=2))
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-story-settings").click().run()
    assert not app.exception
    save_key = f"episode-save-approve-{project.project_id}-script"
    assert app.button(key=save_key).disabled
    app.button(key=f"episode-apply-{project.project_id}-script-0").click().run()
    app.button(key=f"episode-revert-{project.project_id}-script-1").click().run()
    assert not app.button(key=save_key).disabled
    key = f"studio-draft-review-{project.project_id}-script-{input_hash(report.model_dump())[:12]}-0"
    app.text_area(key=key).set_value("A manually refined motivation.").run()
    assert app.button(key=save_key).disabled
    app.button(key=f"episode-apply-{project.project_id}-script-0").click().run()
    app.button(key=save_key).click().run()
    finish_background(app)
    assert not app.exception
    current = pipeline.inspect()
    assert pipeline.artifact("script").scenes[0].purpose == "A manually refined motivation."
    assert current.steps["storyboard"].status == "stale"
    assert "script" in current.content_approvals
    assert "prompts" not in current.content_approvals
    assert world.require_approved()


def test_episode_review_rejects_structural_changes_and_unresolved_decisions(episode):
    world, project, pipeline = episode
    import json
    import pytest
    from story_channel.studio import resolve_content_review, content_digest, review_differences
    from story_channel.studio_ui import review_episode
    from story_channel.files import atomic_write, input_hash
    original = pipeline.artifact("script").model_dump_json(by_alias=True, indent=2)
    malformed = json.loads(original)
    malformed["scenes"][0]["sceneId"] = "different_scene"
    with pytest.raises(ValueError, match="IDs"):
        review_differences("script", original, json.dumps(malformed))
    report = review_episode(pipeline, "script")
    draft = json.loads(original)
    draft["scenes"][0]["purpose"] += " Clarify intent."
    report.advice.proposed_text = json.dumps(draft)
    atomic_write(pipeline.path / "episode_script_review.json", report.model_dump_json(by_alias=True))
    with pytest.raises(ValueError, match="every difference"):
        resolve_content_review(pipeline, "script", json.dumps(draft), expected=content_digest(pipeline, "script"),
            report_digest=input_hash(report.model_dump()), decisions=[])
    assert pipeline.artifact("script").model_dump_json(by_alias=True, indent=2) == original
