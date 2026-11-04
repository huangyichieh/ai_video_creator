from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.config import Settings
from story_channel.projects import ProjectManager
from story_channel.pipeline import Pipeline


def widget(collection, label):
    return next(item for item in collection if item.label == label)


def finish_background(app):
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=30)
        app.run()


def test_streamlit_create_generate_review_edit_and_complete(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    assert not app.exception
    widget(app.text_input, "Title").set_value("UI sample")
    widget(app.number_input, "Hours").set_value(1)
    widget(app.number_input, "Minutes").set_value(2)
    widget(app.number_input, "Seconds").set_value(3)
    widget(app.text_area, "Story outline").set_value("A keeper protects her village's memories.")
    widget(app.button, "Create project").click().run()
    assert not app.exception
    widget(app.button, "Generate / Resume pipeline").click().run()
    finish_background(app)
    assert not app.exception
    manager = ProjectManager(Settings(data_root=root))
    project = manager.list_projects()[0]
    assert project.current_step == "story_review"
    assert project.target_duration_sec == 3723
    assert next(item for item in app.metric if item.label == "Target length (HH:MM:SS)").value == "01:02:03"
    app.button(key="browse-review").click().run()
    assert not app.exception
    assert not app.button(key="ai-review-save-story").disabled
    app.button(key="ai-review-save-story").click().run()
    finish_background(app)
    app.button(key="close-story-approval").click().run()
    assert not app.exception
    app.button(key="nav-story-settings").click().run()
    widget(app.button, "Generate / Resume pipeline").click().run()
    finish_background(app)
    assert not app.exception
    assert manager.load(project.project_id).current_step == "storyboard_review"
    app.button(key="browse-storyboard-approval").click().run()
    app.button(key="ai-review-approve-unchanged-storyboard").click().run()
    finish_background(app)
    app.button(key="close-storyboard-approval").click().run()
    app.button(key="nav-story-settings").click().run()
    widget(app.button, "Generate / Resume pipeline").click().run()
    finish_background(app)
    assert manager.load(project.project_id).current_step == "complete"
    for page in ("Assets", "Final", "Analytics"):
        app.button(key=f"nav-{page.lower()}").click().run()
        assert not app.exception, page
    app.button(key="nav-story-settings").click().run()
    widget(app.text_area, "Edit outline").set_value("A different adventure.")
    widget(app.button, "Save outline").click().run()
    assert not app.exception
    assert manager.load(project.project_id).approved_steps == []


def test_streamlit_invalid_project_shows_error(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(tmp_path / "channel"))
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    widget(app.button, "Create project").click().run()
    assert not app.exception
    assert app.error


def test_overview_generates_one_stage_per_click_and_blocks_for_approval(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("A keeper protects the lighthouse.", encoding="utf-8")
    project = manager.create("Step by step", source)
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    assert app.session_state["workspace_page"] == "Story & Settings"
    assert [item.label for item in app.button if item.key and item.key.startswith("nav-")] == ["Story & Settings", "Assets", "Animations", "Final", "Analytics"]
    assert not app.radio
    assert app.button(key="nav-story-settings").proto.type == "primary"
    app.button(key="nav-animations").click().run()
    assert app.button(key="nav-animations").proto.type == "primary"
    assert app.button(key="nav-story-settings").proto.type == "secondary"
    app.button(key="nav-story-settings").click().run()
    for index, step in enumerate(("world", "characters", "story", "review"), 1):
        assert not app.button(key="generate-next-stage").disabled
        app.button(key="generate-next-stage").click().run()
        assert not app.exception
        current = manager.load(project.project_id)
        assert len(current.steps) == index
        assert current.steps[step].status == "succeeded"
    assert not app.button(key="generate-next-stage").disabled
    assert any("Awaiting review decisions / approval" in item.value for item in app.markdown)
    assert any("Blocked: story approval" in item.value for item in app.markdown)
    assert app.button(key="browse-review").label == "Browse"
    app.button(key="browse-review").click().run()
    app.button(key="ai-review-save-story").click().run()
    finish_background(app)
    app.button(key="close-story-approval").click().run()
    app.button(key="nav-story-settings").click().run()
    app.button(key="generate-next-stage").click().run()
    current = manager.load(project.project_id)
    assert current.steps["script"].status == "succeeded"
    assert "storyboard" not in current.steps


def test_streamlit_rejects_zero_target_length(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(tmp_path / "channel"))
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    widget(app.text_input, "Title").set_value("Zero duration")
    widget(app.text_area, "Story outline").set_value("A short story.")
    widget(app.number_input, "Hours").set_value(0)
    widget(app.number_input, "Minutes").set_value(0)
    widget(app.number_input, "Seconds").set_value(0)
    widget(app.button, "Create project").click().run()
    assert not app.exception
    assert any("at least one second" in error.value for error in app.error)
    assert not ProjectManager(Settings(data_root=tmp_path / "channel")).list_projects()


def test_stage_browser_shows_outputs_inputs_history_and_activity(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("A lighthouse keeper protects lost memories.", encoding="utf-8")
    project = manager.create("Browse sample", source)
    pipeline = Pipeline(manager, project.project_id, demo=True)
    pipeline.run()
    pipeline.approve("story")
    pipeline.run()
    pipeline.approve("storyboard")
    pipeline.run()
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    for step in ("world", "characters", "story", "script", "storyboard", "prompts"):
        app.button(key=f"browse-{step}").click().run()
        assert not app.exception, step
        assert {"Output", "Inputs", "History", "Activity"} <= {tab.label for tab in app.tabs}
        assert any(item.label == "Saved revision" for item in app.selectbox)
        assert app.json
        assert any("Story outline" in item.value for item in app.markdown)
        if step in ("story", "script"):
            assert any(item.value.startswith("# Browse sample") for item in app.markdown)
        if step == "characters":
            assert any("character_bible.md" in item.value for item in app.caption)
            assert any("characters/keeper.json" in item.value for item in app.caption)
        if step == "prompts":
            assert any("image_prompts.json" in item.value for item in app.caption)
            assert any("video_prompts.json" in item.value for item in app.caption)
        app.button(key="close-stage-browser").click().run()
        assert not app.exception
        assert "Output" not in {tab.label for tab in app.tabs} and "Project and activity" not in {tab.label for tab in app.tabs}
    # Browsing does not generate anything or create new versions.
    assert all(state.revision == 1 for state in manager.load(project.project_id).steps.values())
    app.button(key="nav-story-settings").click().run()
    app.button(key="browse-story").click().run()
    assert not app.exception
    assert app.tabs


def test_stage_browser_handles_missing_and_corrupt_outputs(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("A lighthouse story.", encoding="utf-8")
    project = manager.create("Empty stage", source)
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="browse-world").click().run()
    assert not app.exception
    assert any("No saved output" in item.value for item in app.info)
    app.button(key="close-stage-browser").click().run()
    (manager.project_path(project.project_id) / "world.json").write_text('{"unfinished":', encoding="utf-8")
    app.button(key="browse-world").click().run()
    assert not app.exception
    assert any("invalid JSON" in item.value for item in app.warning)
    assert any('{"unfinished":' in item.value for item in app.code)


def test_browser_edit_mark_revert_and_save(tmp_path, monkeypatch, caplog):
    import json
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("Original outline", encoding="utf-8")
    project = manager.create("Editable", source)
    pipeline = Pipeline(manager, project.project_id, demo=True)
    pipeline.generate("world")
    saved_world = (pipeline.path / "world.json").read_text(encoding="utf-8")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    assert not any(item.label == "Automatic generation (optional)" for item in app.expander)
    app.button(key="browse-world").click().run()
    assert "Edit World JSON" in {item.label for item in app.text_area}
    assert any("Inputs are read-only" in item.value for item in app.caption)
    widget(app.text_area, "Edit World JSON").set_value("{").run()
    assert any("1 unsaved change" in item.value for item in app.warning)
    assert app.button(key="regenerate-browser-openai").disabled
    app.button(key="save-browser-changes").click().run()
    assert app.error
    assert manager.read_outline(project.project_id) == "Original outline"
    assert (pipeline.path / "world.json").read_text(encoding="utf-8") == saved_world
    app.button(key="revert-browser-changes").click().run()
    assert not app.exception
    assert widget(app.text_area, "Edit World JSON").value == saved_world
    assert any(item.value == "Original outline" for item in app.markdown)
    assert not app.button(key="regenerate-browser-openai").disabled
    assert app.button(key="save-browser-changes").disabled

    world = json.loads(saved_world)
    world["era"] = "A new era"
    widget(app.text_area, "Edit World JSON").set_value(json.dumps(world)).run()
    assert not app.button(key="save-browser-changes").disabled
    assert any("Modified" in item.value for item in app.caption)
    assert any("A new era" in item.value for item in app.code)
    app.button(key="save-browser-changes").click().run()
    assert not app.exception
    assert pipeline.artifact("world").era == "A new era"
    assert manager.load(project.project_id).steps["world"].revision == 2
    assert any("Changes saved" in item.value for item in app.success)
    app.button(key="browse-world").click().run()
    assert json.loads(widget(app.text_area, "Edit World JSON").value)["era"] == "A new era"
    assert app.button(key="save-browser-changes").disabled
    assert not any("created with a default value" in record.message for record in caplog.records)
    widget(app.selectbox, "Saved revision").select_index(1).run()
    app.button(key="restore-browser-world").click().run()
    assert not app.exception
    assert widget(app.text_area, "Edit World JSON").value == saved_world
    assert not app.button(key="save-browser-changes").disabled
    app.button(key="revert-browser-changes").click().run()
    assert json.loads(widget(app.text_area, "Edit World JSON").value)["era"] == "A new era"
    assert app.button(key="save-browser-changes").disabled


def test_browser_typo_recommendations_use_draft_without_applying_changes(tmp_path, monkeypatch):
    import json
    from story_channel.proofreading import TypoReport
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("A story", encoding="utf-8")
    project = manager.create("Proofread", source)
    pipeline = Pipeline(manager, project.project_id, demo=True)
    pipeline.generate("world")
    saved = (pipeline.path / "world.json").read_bytes()

    def fake_check(flow, drafts, stage):
        assert stage == "world"
        assert set(drafts) == {"world"}
        assert json.loads(drafts["world"])["era"] == "teh era"
        return TypoReport(summary="Two typos found", suggestions=[dict(source="world", field="era", original="teh",
                          replacement="the", explanation="Spelling correction"),
                          dict(source="world", field="era", original="era", replacement="age", explanation="Second correction")])

    monkeypatch.setattr("story_channel.ui.check_typos", fake_check)
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="browse-world").click().run()
    data = json.loads(widget(app.text_area, "Edit World JSON").value)
    data["era"] = "teh era"
    widget(app.text_area, "Edit World JSON").set_value(json.dumps(data)).run()
    app.button(key="check-browser-typos").click().run()
    assert not app.exception
    assert any("Two typos found" in item.value for item in app.markdown)
    assert any("Suggested: the" in item.value for item in app.text)
    assert json.loads(widget(app.text_area, "Edit World JSON").value)["era"] == "teh era"
    assert (pipeline.path / "world.json").read_bytes() == saved
    assert not any("input JSON" in item.label or "Edit story outline" == item.label for item in app.text_area)
    assert not any("changed since this check" in item.value for item in app.warning)
    app.button(key="apply-typo-0").click().run()
    assert not app.exception
    assert json.loads(widget(app.text_area, "Edit World JSON").value)["era"] == "the era"
    assert app.button(key="apply-typo-0").disabled
    assert not app.button(key="apply-typo-1").disabled
    app.button(key="apply-typo-1").click().run()
    assert json.loads(widget(app.text_area, "Edit World JSON").value)["era"] == "the age"
    assert not app.button(key="save-browser-changes").disabled
    assert (pipeline.path / "world.json").read_bytes() == saved
    data["era"] = "the era"
    widget(app.text_area, "Edit World JSON").set_value(json.dumps(data)).run()
    assert any("changed since this check" in item.value for item in app.warning)
    app.button(key="revert-browser-changes").click().run()
    assert widget(app.text_area, "Edit World JSON").value == saved.decode("utf-8")
    assert app.button(key="save-browser-changes").disabled


def test_dialog_openai_regeneration_refreshes_content_and_keeps_history(tmp_path, monkeypatch):
    import json
    from story_channel.demo import DemoLLMProvider
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("A lighthouse story", encoding="utf-8")
    project = manager.create("Regenerate sample", source)
    flow = Pipeline(manager, project.project_id, demo=True)
    flow.generate("world")
    original = (flow.path / "world.json").read_bytes()

    class FakeOpenAI(DemoLLMProvider):
        def generate(self, prompt, response_model, config):
            assert config.provider == "openai"
            output = super().generate(prompt, response_model, config)
            output.era = "OpenAI regenerated era"
            return output

    monkeypatch.setattr("story_channel.pipeline.OpenAILLMProvider", lambda **kwargs: FakeOpenAI())
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="browse-world").click().run()
    app.button(key="regenerate-browser-openai").click().run()
    assert not app.exception
    assert app.tabs
    assert json.loads(widget(app.text_area, "Edit World JSON").value)["era"] == "OpenAI regenerated era"
    assert manager.load(project.project_id).steps["world"].revision == 2
    assert (flow.path / "history" / "world" / "000001.json").read_bytes() == original
    assert app.button(key="save-browser-changes").disabled


def test_overview_outline_and_outline_page_typo_apply_then_save(tmp_path, monkeypatch):
    from story_channel.proofreading import TypoReport
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("teh lighthouse", encoding="utf-8")
    project = manager.create("Outline proofreading", source)

    def fake_check(flow, drafts, stage):
        assert stage == "outline"
        assert drafts == {"outline": "teh lighthouse"}
        return TypoReport(summary="One typo", suggestions=[dict(source="outline", field="text", original="teh",
                          replacement="the", explanation="Correct spelling")])

    monkeypatch.setattr("legacy_pages.check_typos", fake_check)
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    assert any(item.value == "teh lighthouse" for item in app.markdown)
    app.button(key="nav-story-settings").click().run()
    app.button(key="check-outline-typos").click().run()
    assert not app.exception
    app.button(key="apply-outline-typo-0").click().run()
    assert not app.exception
    assert widget(app.text_area, "Edit outline").value == "the lighthouse"
    assert manager.read_outline(project.project_id) == "teh lighthouse"
    assert app.button(key="apply-outline-typo-0").disabled
    widget(app.button, "Save outline").click().run()
    assert manager.read_outline(project.project_id) == "the lighthouse"
    app.button(key="nav-story-settings").click().run()
    assert any(item.value == "the lighthouse" for item in app.markdown)


def test_human_story_approval_dialog_shows_complete_package_and_approves(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("The keeper saves the island's memories.", encoding="utf-8")
    project = manager.create("Story approval package", source)
    flow = Pipeline(manager, project.project_id, demo=True)
    flow.run()
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="browse-review").click().run()
    assert not app.exception
    assert {"Outline", "World", "Characters", "Story", "AI Review (Story)", "Project and activity"} <= {tab.label for tab in app.tabs}
    names = {item.value for item in app.caption}
    assert {"world.md", "character_bible.md", "story.md", "review_report.md"} <= names
    assert "characters/keeper.md" not in names
    assert not any(name.endswith(".json") for name in names)
    path = manager.project_path(project.project_id)
    assert all((path / name).is_file() for name in ("world.md", "review_report.md", "characters/keeper.md"))
    assert "manifest.md" not in names
    summary = next(table for table in app.table if "Version" in table.value.columns)
    assert list(summary.value["Stage"]) == ["World", "Characters", "Story", "AI Review (Story)"]
    assert set(summary.value["Version"]) == {"r1"}
    app.button(key="toggle-approval-json-story").click().run()
    assert app.button(key="toggle-approval-json-story").label == "Hide JSON"
    names = {item.value for item in app.caption}
    assert {"world.json", "characters.json", "story.json", "review_report.json"} <= names
    assert "characters/keeper.json" not in names
    assert not {"manifest.json", "approvals/story/usage.json", "approvals/story/events.json"} & names
    app.button(key="toggle-approval-json-story").click().run()
    assert app.button(key="toggle-approval-json-story").label == "Show JSON"
    assert not any(item.value.endswith(".json") for item in app.caption)
    assert not app.button(key="ai-review-save-story").disabled
    app.button(key="ai-review-save-story").click().run()
    finish_background(app)
    app.button(key="close-story-approval").click().run()
    assert not app.exception
    assert "Output" not in {tab.label for tab in app.tabs} and "Project and activity" not in {tab.label for tab in app.tabs}
    current = manager.load(project.project_id)
    assert current.current_step == "script"
    assert "AI Review (Story)" in current.approvals["story"].note


def test_human_story_approval_dialog_blocks_missing_review(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("A keeper story.", encoding="utf-8")
    project = manager.create("Not ready", source)
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="browse-review").click().run()
    assert not app.exception
    assert not any(b.key == "ai-review-save-story" for b in app.button)
    assert app.button(key="run-ai-review-story").disabled
    assert not manager.load(project.project_id).approvals


def test_ai_storyboard_dialog_shows_script_and_storyboard_then_approves_unchanged(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("A lighthouse keeper saves the village.", encoding="utf-8")
    project = manager.create("Storyboard approval", source)
    flow = Pipeline(manager, project.project_id, demo=True)
    flow.run()
    flow.approve("story")
    flow.run()
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="browse-storyboard-approval").click().run()
    assert not app.exception
    assert {"Script", "Storyboard"} <= {tab.label for tab in app.tabs}
    names = {item.value for item in app.caption}
    assert {"script.md", "storyboard.md"} <= names
    assert not {"script.json", "scenes.json"} & names
    app.button(key="toggle-approval-json-storyboard").click().run()
    assert app.button(key="toggle-approval-json-storyboard").label == "Hide JSON"
    names = {item.value for item in app.caption}
    assert {"script.json", "scenes.json"} <= names
    ordered_names = [item.value for item in app.caption]
    assert ordered_names.index("script.md") < ordered_names.index("script.json")
    assert ordered_names.index("storyboard.md") < ordered_names.index("scenes.json")
    assert "story.json" not in names
    app.button(key="toggle-approval-json-storyboard").click().run()
    assert not any(item.value.endswith(".json") for item in app.caption)
    assert app.button(key="ai-review-save-storyboard").disabled
    assert not app.button(key="ai-review-approve-unchanged-storyboard").disabled
    assert not any(b.key == "approve-storyboard-dialog" for b in app.button)
    app.button(key="ai-review-approve-unchanged-storyboard").click().run()
    finish_background(app)
    assert not app.exception
    assert "Output" not in {tab.label for tab in app.tabs} and "Project and activity" not in {tab.label for tab in app.tabs}
    current = manager.load(project.project_id)
    assert current.current_step == "prompts"
    assert "unchanged" in current.approvals["storyboard"].note


def test_human_storyboard_approval_blocks_missing_outputs(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    monkeypatch.setenv("STORY_DATA_ROOT", str(root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    manager = ProjectManager(Settings(data_root=root))
    source = tmp_path / "outline.md"
    source.write_text("An outline.", encoding="utf-8")
    project = manager.create("Not ready", source)
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="browse-storyboard-approval").click().run()
    assert not app.exception
    assert app.button(key="ai-review-save-storyboard").disabled
    assert app.button(key="ai-review-approve-unchanged-storyboard").disabled
    assert not manager.load(project.project_id).approvals
