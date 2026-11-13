from .dialog_ui import dialog
"""Per-character help and prompt creation for ElevenLabs Voice Design."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import streamlit as st

from .action_labels import text_action_label
from .files import input_hash
from .pipeline import Pipeline
from .ui_tasks import busy, collect, launch, generation_busy, find_task, take_task_result
from .voice_design import generate_voice_design, load_voice_design, voice_design_context


def close_voice_design():
    st.session_state.pop("voice-design-target", None)


def voice_design_heading(project_id, character_id, *, narrator=False, rerun_app=False):
    name = "Narrator" if narrator else character_id
    subject_key = "_narrator" if narrator else character_id
    def open_dialog():
        from .dialog_ui import request_open
        request_open()
        st.session_state["voice-design-target"] = {"project_id": project_id, "character_id": character_id}
        if narrator:
            st.session_state["voice-design-target"]["narrator"] = True
        st.session_state.pop(f"voice-design-error-{project_id}-{subject_key}", None)
        st.session_state.pop("active-stage-browser", None)
        st.session_state.pop("modify-target", None)
        st.session_state.pop("outline-dialog-project", None)
        st.session_state["api-key-dialog-open"] = False
        if rerun_app:
            st.rerun(scope="app")

    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"**{name}**", width="content")
        st.button("", icon=":material/info:", help=f"Create an ElevenLabs voice for {name}",
                  key=f"voice-design-info-{project_id}-{subject_key}", on_click=open_dialog)


@dialog("Create a voice in ElevenLabs", width="large", on_dismiss=close_voice_design)
def voice_design_dialog(manager, project_id, character_id, *, narrator=False):
    st.subheader("Narrator" if narrator else character_id)
    subject = "narrator" if narrator else "character"
    field = "Narrator provider Voice ID" if narrator else "Provider voice ID"
    with st.container(horizontal=True):
        st.link_button("Open ElevenLabs · My Voices", "https://elevenlabs.io/app/voice-lab")
        st.link_button("Open Voice Design", "https://elevenlabs.io/app/voice-lab?create=true&creationType=voiceDesign")
    st.markdown(
        "1. In ElevenLabs, open **Voices → My Voices → Add a new voice → Voice Design**.\n"
        f"2. Generate a {subject} prompt below and paste it into the voice description. Add preview text in the project's language.\n"
        "3. Generate, listen to the options, then name and save your preferred voice.\n"
        f"4. In **My Voices → ⋯ → Copy voice ID**, copy the ID. Close this dialog, paste it into the **{field}** field, and save.\n\n"
        "[ElevenLabs Voice Design guide](https://elevenlabs.io/docs/eleven-creative/voices/voice-design)")
    st.caption(("Generate a narrator prompt from the project's language, outline and saved narration. " if narrator else "Generate from this character's Age, Personality and Speaking style, using the project's language. ")
               + "Generated prompts are less than 500 characters. OpenAI usage and costs are recorded, including failed requests. This action creates text; create and audition the voice in ElevenLabs.")
    voice_prompt_panel(manager, project_id, character_id, narrator=narrator)
    if st.button("Close", key="voice-design-close"):
        close_voice_design()
        st.rerun(scope="app")


@st.fragment(run_every=0.5)
def voice_prompt_panel(manager, project_id, character_id, *, narrator=False):
    pipeline = Pipeline(manager, project_id)
    subject_key = "_narrator" if narrator else character_id
    error_key = f"voice-design-error-{project_id}-{subject_key}"
    target = {"project_id": project_id, "character_id": character_id}
    if narrator:
        target["narrator"] = True
    owner = ("voice-design-target", target)
    task = find_task(lambda item: item.get("dialog_owner") == owner)
    owned = bool(task and task.get("dialog_owner") == owner)
    if owned and task["future"].done():
        collect(task)
        owned = False
    result = take_task_result(project_id, [f"voice-design-generate-{project_id}-{subject_key}"])
    if result and result[0][0] == "error":
        st.session_state[error_key] = result[0][1]

    def start_generation():
        st.session_state.pop(error_key, None)
        launch(lambda: generate_voice_design(pipeline, character_id, narrator=narrator), f"Generating voice prompt for {'Narrator' if narrator else character_id}",
               dialog_owner=owner, queue_enabled=True, concurrent_safe=True,
               resource=f"voice-design:{project_id}:{subject_key}", project_path=pipeline.path, service="openai")

    generation_button(text_action_label("Generate voice prompt", pipeline, "voice_design"),
              key=f"voice-design-generate-{project_id}-{subject_key}", type="primary", disabled=generation_busy(),
              on_click=start_generation)
    if st.session_state.get(error_key):
        render_error(st.session_state[error_key], project_path=pipeline.path,
                     task_key="voice_design:narrator:voice" if narrator else f"voice_design:{character_id}",
                     key=report_widget_key('voice_design_ui:86', locals()))
    if owned:
        st.code("Generating voice prompt...", language=None, wrap_lines=True)
        return
    try:
        saved = load_voice_design(pipeline, character_id, narrator=narrator)
        if saved:
            if saved.source_hash != input_hash(voice_design_context(pipeline, character_id, narrator=narrator)):
                st.warning("Narrator source or language changed. Generate a new prompt." if narrator else
                           "This saved prompt no longer matches the current inputs. Generate again using Age, Personality, Speaking style and project language.")
            st.caption(f"Saved prompt · {saved.provider} / {saved.model} · {len(saved.prompt.render())} characters. Use the copy icon to copy the full prompt.")
            st.code(saved.prompt.render(), language=None, wrap_lines=True)
        else:
            st.caption("Prompt format")
            st.code("Native <Language>. <Gender>, <Age range>. <Quality level>.\n"
                    "Persona: <2–5 words>. Emotion: <2–3 adjectives>.\n"
                    "<1–2 sentences about timbre, pacing, delivery>", language=None, wrap_lines=True)
    except (OSError, ValueError) as exc:
        st.warning(f"Cannot read the saved voice prompt: {exc}")


def show_voice_design(manager, project_id):
    target = st.session_state.get("voice-design-target")
    if (target and target["project_id"] == project_id and not st.session_state.get("api-key-dialog-open")
            and not st.session_state.get("modify-target") and not st.session_state.get("active-stage-browser")
            and not st.session_state.get("outline-dialog-project")):
        voice_design_dialog(manager, project_id, target["character_id"], narrator=target.get("narrator", False))
