from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.generation_preferences import MODELS, PROCESSES, preset, process_for, preferences, save_preferences, openai_config
from story_channel.pipeline import Pipeline
from story_channel.studio import Episodes
from story_channel.demo import DemoLLMProvider
from story_channel.costs import CostTracker
from story_channel.modification import modify_content, OutlineEdit
from test_studio import world
from test_pipeline import flow


def test_presets_and_all_text_entrypoints_route_to_processes():
    assert set(preset()) == set(PROCESSES)
    assert set(preset("Economic").values()) == {"gpt-6-luna"}
    assert preset()["metadata"] == "gpt-6-luna"
    assert preset()["story"] == "gpt-6.1-sol"
    stages = {"world_addition_consistency": "canon", "world_review": "world_review",
              "world_addition_structure": "metadata", "episode_outline_review": "outline",
              "episode_script_review": "dialogue_review", "episode_story_review": "story_review",
              "episode_storyboard_review": "storyboard_review", "review": "story_review",
              "scene_direction:scene_01": "performance", "voice_delivery": "performance",
              "voice_design:husband_01": "voice_design", "sfx_prompt": "sound_description",
              "typo_check:world": "proofreading", "provider_prompt": "prompts",
              "character_references:husband_01": "asset_edit", "finishing_plan": "animation_edit"}
    for stage, process in stages.items():
        assert process_for(stage) == process


def test_preferences_are_shared_by_episodes_without_invalidating_existing_work(world):
    episode = Episodes(world.manager, world.project_id).create("Episode", "A keeper finds a memory.", 60)
    before = world.manager.load(world.project_id)
    child_before = world.manager.load(episode.project_id)
    models = preset("Economic")
    models["canon"] = "gpt-6-astra"
    save_preferences(world.manager, episode.project_id, models, demo=True)
    assert preferences(world.manager, world.project_id) == models
    assert preferences(world.manager, episode.project_id) == models
    assert world.require_approved().approval
    after = world.manager.load(world.project_id)
    assert after.steps == before.steps and after.approvals == before.approvals
    assert world.manager.load(episode.project_id) == child_before
    pipeline = Pipeline(world.manager, episode.project_id)
    assert openai_config(pipeline, "world_addition_consistency", force=True).model == "gpt-6-astra"
    assert openai_config(pipeline, "story").provider == "demo"
    with pytest.raises(ValueError):
        save_preferences(world.manager, world.project_id, {"story": "invalid"})


def test_generation_modification_and_cost_records_use_saved_models(flow):
    choices = preset("Economic")
    choices["world"] = "gpt-5.6-sol"
    choices["outline"] = "gpt-6-astra"
    save_preferences(flow.manager, flow.project_id, choices)
    class Fake(DemoLLMProvider):
        last_usage = {"input_tokens": 10, "output_tokens": 5}
        def generate(self, prompt, response_model, config):
            self.model = config.model
            if response_model is OutlineEdit:
                return OutlineEdit(text="Edited outline")
            return super().generate(prompt, response_model, config)
    provider = Fake()
    pipeline = Pipeline(flow.manager, flow.project_id, provider=provider, demo=False)
    pipeline.generate("world")
    assert provider.model == "gpt-5.6-sol"
    assert CostTracker(flow.manager, flow.project_id).records()[-1].model == "gpt-5.6-sol"
    assert flow.manager.load(flow.project_id).provider_config["llm:world"].model == "gpt-5.6-sol"
    modify_content(pipeline, OutlineEdit(text="Original"), "Improve it", "outline", provider=provider)
    assert provider.model == "gpt-6-astra"
    assert CostTracker(flow.manager, flow.project_id).records()[-1].model == "gpt-6-astra"
    choices["world"] = "gpt-6-luna"
    save_preferences(flow.manager, flow.project_id, choices)
    assert pipeline.inspect().steps["world"].status == "succeeded"
    pipeline.generate("world", force=True)
    assert provider.model == "gpt-6-luna"


def test_top_right_dialog_presets_save_and_reopen(world, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    assert not any(item.label == "Generation preferences" for item in app.expander)
    app.button(key="open-generation-preferences").click().run()
    assert not app.exception
    prefix = f"generation-choice-{world.project_id}-"
    assert [tab.label for tab in app.tabs] == ["OpenAI"]
    assert app.selectbox(key=prefix + "story").value == "gpt-6.1-sol"
    app.button(key="generation-economic").click().run()
    assert all(app.selectbox(key=prefix + process).value == "gpt-6-luna" for process in PROCESSES)
    app.selectbox(key=prefix + "canon").select("gpt-6-astra")
    next(button for button in app.button if button.label == "Save preferences").click().run()
    assert not app.exception
    assert preferences(world.manager, world.project_id)["canon"] == "gpt-6-astra"
    assert world.require_approved()
    app.button(key="open-generation-preferences").click().run()
    assert app.selectbox(key=prefix + "canon").value == "gpt-6-astra"
    app.button(key="generation-balance").click().run()
    assert app.selectbox(key=prefix + "canon").value == "gpt-6.1-sol"
    assert app.selectbox(key=prefix + "metadata").value == "gpt-6-luna"
