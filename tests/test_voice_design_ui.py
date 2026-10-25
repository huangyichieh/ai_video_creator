import pytest
from threading import Event
import streamlit as st

from story_channel.animation import AnimationDirector
from story_channel.costs import CostTracker
from story_channel.identity import IdentityManager
from story_channel.llm import OpenAILLMProvider, ProviderError
from test_assets_ui import prepare
from test_identity_ui import open_assets
from test_ui import finish_background
from test_voice_design import designed_prompt


@pytest.mark.parametrize("with_direction", [False, True])
def test_character_info_dialog_generates_saves_and_reopens_prompt(tmp_path, monkeypatch, with_direction):
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    cid = next(iter(library.voices))
    if with_direction:
        AnimationDirector(manager, project.project_id).prepare()
    calls = []

    def generated(self, prompt, response_model, config):
        calls.append((prompt, config))
        self.last_usage = {"input_tokens": 100, "output_tokens": 50}
        return designed_prompt()

    monkeypatch.setattr(OpenAILLMProvider, "generate", generated)
    app = open_assets(manager, monkeypatch)
    voice_key = f"animation-preview-voice-{project.project_id}-{cid}"
    app.text_input(key=voice_key).set_value("unsaved-provider-voice").run()
    app.button(key=f"voice-design-info-{project.project_id}-{cid}").click().run()
    assert not app.exception
    assert not calls
    assert any("My Voices" in item.label for item in app.get("link_button"))
    assert any("Add a new voice" in item.value for item in app.markdown)
    app.button(key=f"voice-design-generate-{project.project_id}-{cid}").click().run()
    finish_background(app)
    assert not app.exception
    assert len(calls) == 1 and calls[0][1].provider == "openai"
    assert any(designed_prompt().render() == item.value for item in app.code)
    app.button(key="voice-design-close").click().run()
    assert app.text_input(key=voice_key).value == "unsaved-provider-voice"
    app.button(key="nav-final").click().run()
    app.button(key="nav-assets").click().run()
    app.button(key=f"voice-design-info-{project.project_id}-{cid}").click().run()
    assert any(designed_prompt().render() == item.value for item in app.code)
    assert len(calls) == 1
    assert CostTracker(manager, project.project_id).records()[-1].step == f"voice_design:{cid}"
    assert not identity.load().voices[cid].voice_id


def test_voice_prompt_failure_remains_visible_inside_dialog(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    library = IdentityManager(manager, project.project_id).prepare()
    cid = next(iter(library.voices))

    def failed(self, *args):
        self.last_usage = {}
        raise ProviderError("Test voice design error")

    monkeypatch.setattr(OpenAILLMProvider, "generate", failed)
    app = open_assets(manager, monkeypatch)
    app.button(key=f"voice-design-info-{project.project_id}-{cid}").click().run()
    app.button(key=f"voice-design-generate-{project.project_id}-{cid}").click().run()
    if "workspace-task" in app.session_state:
        with pytest.raises(ProviderError):
            app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    app.run()
    assert not app.exception
    assert any("Test voice design error" in error.value for error in app.error)
    assert CostTracker(manager, project.project_id).records()[-1].outcome == "failed"


@pytest.mark.parametrize("close_early", [False, True])
def test_voice_prompt_shows_loading_without_reopening_dialog_and_survives_close(tmp_path, monkeypatch, close_early):
    manager, project = prepare(tmp_path)
    library = IdentityManager(manager, project.project_id).prepare()
    cid = next(iter(library.voices))
    release = Event()

    def delayed(self, *args):
        assert release.wait(timeout=30)
        self.last_usage = {"input_tokens": 100, "output_tokens": 50}
        return designed_prompt()

    monkeypatch.setattr(OpenAILLMProvider, "generate", delayed)
    app = open_assets(manager, monkeypatch)
    app.button(key=f"voice-design-info-{project.project_id}-{cid}").click().run()
    reruns = []
    original_rerun = st.rerun

    def track_rerun(*args, **kwargs):
        reruns.append(kwargs.get("scope", "app"))
        return original_rerun(*args, **kwargs)

    monkeypatch.setattr(st, "rerun", track_rerun)
    try:
        generate_key = f"voice-design-generate-{project.project_id}-{cid}"
        app.button(key=generate_key).click().run()
        task = app.session_state["workspace-task"]
        assert not task["future"].done()
        assert not reruns
        assert app.button(key=generate_key).disabled
        assert any(item.value == "Generating voice prompt..." for item in app.code)
        assert app.session_state["voice-design-target"]["character_id"] == cid
        app.run()
        assert not reruns and not app.exception
        if close_early:
            app.button(key="voice-design-close").click().run()
            assert "voice-design-target" not in app.session_state
            app.button(key="nav-final").click().run()
            assert not task["future"].done()
        release.set()
        task["future"].result(timeout=10)
        app.run()
        if close_early:
            app.button(key="nav-assets").click().run()
            app.button(key=f"voice-design-info-{project.project_id}-{cid}").click().run()
        else:
            assert not reruns
        assert not app.exception
        assert any(item.value == designed_prompt().render() for item in app.code)
        assert not app.button(key=generate_key).disabled
    finally:
        release.set()


def test_parent_task_poller_defers_to_open_dialog_even_when_complete(tmp_path, monkeypatch):
    from concurrent.futures import Future
    from story_channel.ui_tasks import task_status

    future = Future()
    future.set_result(None)
    target = {"project_id": "project", "character_id": "character"}
    state = {"voice-design-target": target,
             "workspace-task": {"future": future, "dialog_owner": ("voice-design-target", target)}}
    monkeypatch.setattr(st, "session_state", state)

    class AppRerun(Exception):
        pass

    def rerun(**kwargs):
        raise AppRerun

    monkeypatch.setattr(st, "rerun", rerun)
    task_status.__wrapped__(tmp_path)
    del state["voice-design-target"]
    with pytest.raises(AppRerun):
        task_status.__wrapped__(tmp_path)


def test_narrator_help_generates_in_place_and_keeps_its_own_prompt(tmp_path, monkeypatch):
    from story_channel.pipeline import Pipeline
    from story_channel.voice_design import load_voice_design
    manager, project = prepare(tmp_path)
    release = Event()
    requests = []

    def generated(self, prompt, response_model, config):
        requests.append(prompt)
        assert "supplied narrator only" in prompt
        assert "do not copy a cast character" in prompt
        assert release.wait(timeout=30)
        self.last_usage = {"input_tokens": 100, "output_tokens": 50}
        return designed_prompt().model_copy(update={"persona": "warm cinematic storyteller"})

    monkeypatch.setattr(OpenAILLMProvider, "generate", generated)
    app = open_assets(manager, monkeypatch)
    input_key = f"asset-narrator-{project.project_id}"
    app.text_input(key=input_key).set_value("unsaved-narrator-selection").run()
    app.button(key=f"voice-design-info-{project.project_id}-_narrator").click().run()
    assert not requests and not app.exception
    assert any("Narrator provider Voice ID" in item.value for item in app.markdown)
    try:
        app.button(key=f"voice-design-generate-{project.project_id}-_narrator").click().run()
        assert any(item.value == "Generating voice prompt..." for item in app.code)
        assert app.session_state["voice-design-target"]["narrator"] is True
        release.set()
        finish_background(app)
        assert not app.exception
        assert any("warm cinematic storyteller" in item.value for item in app.code)
        app.button(key="voice-design-close").click().run()
        assert app.text_input(key=input_key).value == "unsaved-narrator-selection"
        app.button(key=f"voice-design-info-{project.project_id}-_narrator").click().run()
        assert any("warm cinematic storyteller" in item.value for item in app.code)
        assert len(requests) == 1
        assert load_voice_design(Pipeline(manager, project.project_id), "narrator", narrator=True).role == "narrator"
        assert CostTracker(manager, project.project_id).records()[-1].step == "voice_design:narrator:voice"
    finally:
        release.set()
