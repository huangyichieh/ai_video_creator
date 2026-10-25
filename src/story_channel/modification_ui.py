"""One prompt dialog shared by text, structured sources, images and speech."""
import streamlit as st

from .assets import AssetPipeline
from .asset_sources import modify_source_with_openai
from .pipeline import Pipeline
from .ui_tasks import busy, launch, launch_assets, launch_render
from .action_labels import action_label, text_action_label, asset_modify_label


def open_modification(project_id, kind, stage, *, subject=None, expected=None):
    st.session_state["modify-target"] = {"project_id": project_id, "kind": kind, "stage": stage,
                                         "subject": subject, "expected": expected,
                                         "return_browser": st.session_state.pop("active-stage-browser", None)}
    st.rerun(scope="app")


def close_modification():
    target = st.session_state.pop("modify-target", None)
    if target and target.get("return_browser"):
        st.session_state["active-stage-browser"] = target["return_browser"]


@st.dialog("Modify", width="large", on_dismiss=close_modification)
def modification_dialog(manager, target):
    project_id, kind, stage, subject = (target[k] for k in ("project_id", "kind", "stage", "subject"))
    pipeline = Pipeline(manager, project_id)
    label = text_action_label("Modify", pipeline)
    st.subheader((subject or stage).replace("_", " ").title())
    st.caption("Describe the changes you want. The selected service uses the current saved content as the starting point. A new version and API usage are recorded; unrelated details should be preserved.")
    if kind == "vfx":
        from .rendering import RenderPipeline
        engine = RenderPipeline(manager, project_id)
        record = engine.finishing.load().records.get(f"vfx:{subject}")
        if record and record.asset:
            label = action_label("Modify", record.asset.provider, record.asset.model)
            st.video(str(engine.assets.local_path(record.asset.file_path)))
        st.caption("Modify the existing isolated VFX layer. The base animation and approved audio are preserved.")
    elif kind == "finishing_plan":
        path = manager.project_path(project_id) / "finishing_plan.md"
        if path.exists():
            st.markdown(path.read_text(encoding="utf-8"))
        st.code(target["expected"], language="json")
    elif kind == "animation_shot":
        from .animation import AnimationDirector
        director = AnimationDirector(manager, project_id)
        record = director.load().shots.get(subject)
        if record and record.asset:
            label = action_label("Modify", record.asset.provider, record.asset.model)
            st.video(str(director.assets.local_path(record.asset.file_path)))
    elif kind == "video":
        from .rendering import RenderPipeline
        engine = RenderPipeline(manager, project_id)
        record = engine.load().scenes.get(subject)
        if record and record.clip:
            label = action_label("Modify", record.clip.provider, record.clip.model)
        if record and record.file:
            st.video(str(engine.assets.local_path(record.file)))
        st.caption("Edit the current OpenAI clip. Approved narration and subtitle timing are retained when the scene is recomposed.")
    elif kind == "asset":
        engine = AssetPipeline(manager, project_id)
        label = asset_modify_label(engine, stage)
        record = engine.load().records.get(f"{stage}:{subject}")
        if record and record.asset:
            path = engine.local_path(record.asset.file_path)
            if path.is_file():
                if stage == "voices":
                    st.audio(str(path))
                    st.caption("Voice edits re-synthesize the current words with your requested delivery. Edit source JSON to change the spoken words.")
                else:
                    st.image(str(path), width="stretch")
    else:
        with st.expander("Current saved content"):
            if stage == "outline":
                st.markdown(target["expected"])
            else:
                st.code(target["expected"], language="json")
    prompt_key = f"modify-draft-{project_id}-{kind}-{stage}-{subject or ''}"
    instructions = st.text_area("Modification prompt", key=prompt_key, height=180, disabled=busy(),
                                placeholder="e.g. Make the lighting warmer while keeping the character and composition unchanged.")
    apply, cancel = st.columns(2)
    if apply.button(label, key="apply-openai-modification", type="primary", disabled=busy() or not instructions.strip()):

        def finished(_):
            if kind == "stage" and stage == "outline":
                st.session_state[f"outline-{project_id}"] = manager.read_outline(project_id)
                st.session_state.pop(f"outline-typos-{project_id}", None)
            elif kind == "stage":
                prefix = f"browser-draft-{project_id}-{stage}"
                for key in list(st.session_state):
                    if key == prefix or key.startswith(prefix + "-"):
                        del st.session_state[key]
            elif kind == "source":
                prefix = (f"asset-character-{project_id}-{subject}" if stage == "character_references"
                          else f"asset-editor-{stage}-{project_id}-{subject}")
                for suffix in ("baseline", "draft", "typos", "error"):
                    st.session_state.pop(prefix + "-" + suffix, None)
            close_modification()

        if kind == "vfx":
            from .rendering import RenderPipeline
            engine = RenderPipeline(manager, project_id)
            close_modification()
            launch_render(engine, lambda: engine.finishing.modify_effect(subject, instructions, expected=target["expected"]), "Modifying VFX with OpenAI")
        elif kind == "finishing_plan":
            from .rendering import RenderPipeline
            engine = RenderPipeline(manager, project_id)
            def finished_finishing(_):
                st.session_state.pop(f"finishing-draft-{project_id}", None)
                for name in ("lip", "model", "sound"):
                    st.session_state.pop(f"finishing-setting-{project_id}-{name}", None)
                close_modification()
            launch(lambda: engine.finishing.modify_plan(instructions, expected=target["expected"]), "Modifying finishing plan", finished_finishing)
        elif kind == "animation_shot":
            from .rendering import RenderPipeline
            engine = RenderPipeline(manager, project_id)
            close_modification()
            launch_render(engine, lambda: engine.retry_animation_shot(subject, modification=instructions, expected=target["expected"]),
                          f"Modifying animated shot {subject}")
        elif kind == "animation":
            from .animation import AnimationDirector
            director = AnimationDirector(manager, project_id)
            def finished_animation(_):
                st.session_state.pop(f"animation-plan-draft-{project_id}", None)
                close_modification()
            launch(lambda: director.modify_with_openai(instructions, target["expected"]), "Directing animation with OpenAI", finished_animation)
        elif kind == "video":
            from .rendering import RenderPipeline
            engine = RenderPipeline(manager, project_id)
            close_modification()
            launch_render(engine, lambda: engine.modify_with_openai(subject, instructions, expected=target["expected"]),
                          f"Modifying {subject} video with OpenAI")
        elif kind == "asset":
            # Capture the request before closing the modal; the worker never reads session state.
            engine = AssetPipeline(manager, project_id)
            close_modification()
            launch_assets(engine, lambda: engine.modify_with_openai(stage, subject, instructions,
                          expected_asset_id=target["expected"]), f"Modifying {subject} with OpenAI")
        elif kind == "source":
            launch(lambda: modify_source_with_openai(pipeline, stage, subject, instructions, expected=target["expected"]),
                   f"Modifying {subject} JSON with OpenAI", finished)
        else:
            launch(lambda: pipeline.modify_with_openai(stage, instructions, expected=target["expected"]),
                   f"Modifying {stage} with OpenAI", finished)
    if cancel.button("Cancel", key="cancel-openai-modification", disabled=busy()):
        close_modification()
        st.rerun(scope="app")


def show_modification(manager, project_id):
    target = st.session_state.get("modify-target")
    if target and target["project_id"] == project_id:
        modification_dialog(manager, target)
