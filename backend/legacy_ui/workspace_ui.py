"""Workspace categories and project-scoped settings."""
from .error_report_ui import render_error, error_project, report_widget_key
import json

import streamlit as st

from .ui_tasks import busy, launch


def audio_library(manager, project_id):
    from .rendering import RenderPipeline
    engine = RenderPipeline(manager, project_id)
    uploaded = st.file_uploader("Add licensed sound effect", type=["wav", "mp3", "m4a", "ogg", "flac"], disabled=busy())
    source = st.text_input("Audio license / source", key=f"render-note-audio-source-{project_id}")
    if st.button("Save audio to project", disabled=busy() or uploaded is None or not source.strip()):
        try:
            relative = engine.import_audio(uploaded.getvalue(), uploaded.name, source)
            st.success("Saved audio. Use this path in the Final audio mixing plan:")
            st.code(relative)
        except (OSError, ValueError, RuntimeError) as exc:
            render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('workspace_ui:20', locals()))
    for receipt in sorted((engine.path / "audio" / "library").glob("*.source.json")):
        try:
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
            if metadata.get("kind") == "bgm":
                continue
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
    from .cost_estimates import project_usage_records
    records = project_usage_records(manager, project_id)
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
