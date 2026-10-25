"""Workspace categories and project-scoped settings."""
import json

import streamlit as st

from .ui_tasks import busy, launch


def show_json(path):
    try:
        st.json(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        st.warning(f"Cannot display saved JSON: {exc}")


def story_settings(manager, project_id, pipeline, project, workflow, outline):
    tabs = st.tabs(["Story workflow", "Project settings", "World / Canon", "Characters", "Story outline",
                    "Script / Dialogue", "Storyboard", "Performance intent", "Generation settings & Providers"],
                   key=f"page-tab-story-{project_id}", on_change="rerun")
    with tabs[0]:
        workflow(manager, project_id, pipeline, project)
    with tabs[1]:
        baseline = json.dumps({"title": project.title, "language": project.language,
                               "targetDurationSec": project.target_duration_sec}, ensure_ascii=False, indent=2)
        prefix = f"project-setting-{project_id}-"
        st.session_state.setdefault(prefix + "title", project.title)
        st.session_state.setdefault(prefix + "language", project.language)
        title = st.text_input("Project title", key=prefix + "title", disabled=busy())
        language = st.text_input("Language", key=prefix + "language", disabled=busy())
        hours, minutes, seconds = st.columns(3)
        h, remainder = divmod(project.target_duration_sec, 3600)
        m, sec = divmod(remainder, 60)
        for name, value in (("hours", h), ("minutes", m), ("seconds", sec)):
            st.session_state.setdefault(prefix + name, value)
        h = hours.number_input("Hours", min_value=0, step=1, key=prefix + "hours", disabled=busy())
        m = minutes.number_input("Minutes", min_value=0, max_value=59, step=1, key=prefix + "minutes", disabled=busy())
        sec = seconds.number_input("Seconds", min_value=0, max_value=59, step=1, key=prefix + "seconds", disabled=busy())
        updated = json.dumps({"title": title, "language": language, "targetDurationSec": h*3600+m*60+sec}, ensure_ascii=False, indent=2)
        if updated != baseline:
            st.caption("Modified — not saved")
        save, revert = st.columns(2)
        if save.button("Save project settings", disabled=busy() or updated == baseline):
            launch(lambda: pipeline.edit_bundle({"project": updated}, expected={"project": baseline}), "Saving project settings")
        def reset():
            for name, value in (("title", project.title), ("language", project.language), ("hours", project.target_duration_sec//3600),
                                ("minutes", project.target_duration_sec//60 % 60), ("seconds", project.target_duration_sec % 60)):
                st.session_state[prefix + name] = value
        revert.button("Revert project settings", disabled=busy() or updated == baseline, on_click=reset)
        st.caption("Changing project inputs invalidates dependent story content and approvals.")
    from .modules import MODULES
    for tab, step in ((tabs[2], "world"), (tabs[3], "characters"), (tabs[5], "script"), (tabs[6], "storyboard")):
        with tab:
            markdown = pipeline.path / ("character_bible.md" if step == "characters" else f"{step}.md")
            if markdown.exists():
                from .ui import script_line_breaks
                text = markdown.read_text(encoding="utf-8")
                st.markdown(script_line_breaks(text) if step == "script" else text)
            path = pipeline.path / MODULES[step].filename
            if path.exists():
                show_json(path)
            else:
                st.info("Generate this content from Story workflow first.")
            def browse(name=step):
                st.session_state["active-stage-browser"] = (project_id, name)
            st.button("Browse / edit " + step.title(), key=f"story-category-{step}", on_click=browse)
            if step == "world":
                st.caption("World defines the current project's canon. Cross-project canon versioning is not implemented yet.")
    with tabs[4]:
        outline(manager, project_id, pipeline, project)
    with tabs[7]:
        path = pipeline.path / "performance_intent.json"
        if path.exists():
            show_json(path)
        st.caption("Script generation exports performance intent. Edit and approve the production performance plan on Animations → 2. Direction & performance.")
    with tabs[8]:
        st.markdown("**Story generation**")
        st.caption("Minimum balanced stack: GPT-6.1 Sol → Gen-4 references → Kling O3 Pro; selected Act-Two close-ups; Eleven v4 voices / ElevenLabs SFX; FFmpeg composition. Runway and ElevenLabs keys are available under the top-right key icon.")
        mode_key = f"project-setting-{project_id}-generation-mode"
        mode = st.selectbox("Generation mode", ("OpenAI", "Demo (offline)"), key=mode_key, disabled=busy())
        st.text_input("LLM model", key=f"project-setting-{project_id}-llm-model", disabled=busy() or mode.startswith("Demo"))
        st.caption("Asset provider mode and shared style are on Assets; video, lip-sync and sound provider settings are on Animations. These story preferences apply to the next generation call.")
        st.json({key: value.model_dump(mode="json", by_alias=True) for key, value in project.provider_config.items()})
        st.caption("Default models and generation limits come from config / STORY_* environment settings. Credentials stay in the local .env file.")


def audio_library(manager, project_id):
    from .rendering import RenderPipeline
    engine = RenderPipeline(manager, project_id)
    uploaded = st.file_uploader("Add licensed music or sound effect", type=["wav", "mp3", "m4a", "ogg", "flac"], disabled=busy())
    source = st.text_input("Audio license / source", key=f"render-note-audio-source-{project_id}")
    if st.button("Save audio to project", disabled=busy() or uploaded is None or not source.strip()):
        try:
            relative = engine.import_audio(uploaded.getvalue(), uploaded.name, source)
            st.success("Saved audio. Use this path in the Final audio mixing plan:")
            st.code(relative)
        except (OSError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
    for receipt in sorted((engine.path / "audio" / "library").glob("*.source.json")):
        try:
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
            audio = engine.assets.local_path(metadata["file"])
            if audio.exists():
                st.code(metadata["file"], language=None)
                st.caption(metadata["source"])
                st.audio(str(audio))
        except (OSError, ValueError, KeyError) as exc:
            st.warning(f"Cannot read library entry {receipt.name}: {exc}")
    st.caption("Generate scene-specific SFX and ambience on Animations. Set music placement and mix levels through the Final sound plan.")


def analytics_overview(manager, project_id):
    st.header("Analytics")
    st.markdown("**Audience & Revenue**")
    st.info("Views, CTR, average view duration, completion rate, subscriber conversion and revenue are unavailable until a publishing analytics source is connected. Performance by genre, character and story type also requires that data.")
    st.markdown("**Generation cost & Provider performance**")
    from .costs import CostTracker
    records = CostTracker(manager, project_id).records()
    from .cost_estimates import project_cost_estimate
    _, entries = project_cost_estimate(manager, project_id)
    groups = {}
    for record in records:
        group = groups.setdefault((record.provider, record.model), {"Calls": 0, "Failed": 0, "Priced USD": 0., "Unknown cost": 0})
        group["Calls"] += 1
        group["Failed"] += int(record.outcome == "failed")
    for entry in entries:
        record = entry["record"]
        group = groups[record.provider, record.model]
        if entry["amount"] is None:
            group["Unknown cost"] += 1
        else:
            group["Priced USD"] += float(entry["amount"])
    if groups:
        st.dataframe([{"Provider": provider, "Model": model, "Calls": row["Calls"], "Failed": row["Failed"], "Estimated USD": f"{row['Priced USD']:.2f}", "Unpriced records": row["Unknown cost"]}
                      for (provider, model), row in groups.items()], hide_index=True, width="stretch")
    st.caption("Rough USD estimates use recorded costs, configured rates, then default rates. Repeated polls of the same generation job count once. Recorded calls and failures are available below.")
