"""Ordered scene input stages, with shared settings managed on Assets."""
import streamlit as st

from .assets import AssetPipeline
from .character_assets_ui import asset_cards
from .ui_tasks import busy, launch, launch_assets


def input_stage(manager, project_id, *, images, state=None):
    engine = AssetPipeline(manager, project_id)
    try:
        if state is None:
            state = engine.load() if busy() else engine.inspect()
    except (OSError, ValueError, RuntimeError) as exc:
        st.info(str(exc))
        return
    if images:
        st.subheader("1. Generate scene images")
        st.caption("Use approved character and location references to create each scene image. Review these images with the shot plan in step 2. Shared style and provider settings are saved on Assets.")
        if st.button("Generate / resume scene images", key=f"animation-input-images-{project_id}", disabled=busy() or not engine.can_generate("scene_images", state)):
            launch_assets(engine, lambda: engine.generate("scene_images"), "Generating scene images")
        stage = "scene_images"
    else:
        st.subheader("3. Generate voices and review scene inputs")
        st.caption("After approving direction and performance in step 2: generate dialogue tracks → align subtitles → run scene-input QA → review and approve the batch here. Voice casting and audition approval stay on Assets.")
        stages = {"voices": "Voice tracks", "subtitles": "Subtitle alignment", "consistency": "Scene-input QA"}
        stage = "voices"
        next_stage = state.next_stage
        from .animation import AnimationDirector
        try:
            AnimationDirector(manager, project_id).require_ready()
            direction_ready = True
        except (OSError, ValueError):
            direction_ready = False
        ready = direction_ready and next_stage in stages and engine.can_generate(next_stage, state)
        if not direction_ready:
            st.info("Complete the direction and performance approvals in step 2 before generating voice tracks.")
        if st.button("Generate next input: " + stages.get(next_stage, "Review inputs"), key=f"animation-input-next-{project_id}", disabled=busy() or not ready):
            launch_assets(engine, lambda: engine.generate(next_stage), "Generating " + stages[next_stage])
        st.markdown("**Scene-input QA**")
        markdown = engine.path / "asset_review.md"
        if markdown.exists():
            st.markdown(markdown.read_text(encoding="utf-8"))
        if state.consistency_report:
            st.json(state.consistency_report)
        if state.next_stage == "asset_review":
            with st.container(key=f"animation-input-approval-{project_id}"):
                st.markdown("**Approve scene inputs before step 4**")
                note = st.text_input("Asset review note (optional)", key=f"asset-note-{project_id}-assets")
                checked = st.checkbox("I reviewed these assets and approve continuing", key=f"asset-accept-{project_id}-assets-{engine._gate_hash('assets', state, engine.tasks(state))}")
                if st.button("Approve assets", key="approve-assets-assets", disabled=busy() or not checked):
                    launch(lambda: engine.approve("assets", note), "Approving scene inputs")
        elif state.next_stage == "complete":
            st.success("Scene inputs approved. Continue to step 4: Video & effects.")
    records = [r for r in state.records.values() if r.stage == stage]
    if records:
        rows = [{"Regenerate": False, "Subject": r.subject, "Status": r.status, "Revision": r.revision} for r in records]
        selected = st.data_editor(rows, key=f"animation-input-selection-{project_id}-{stage}", hide_index=True,
                                  disabled=busy() or ["Subject", "Status", "Revision"])
        subjects = [r["Subject"] for r in selected if r["Regenerate"]]
        if st.button("Regenerate selected " + ("images" if images else "voice tracks"), key=f"animation-input-regenerate-{project_id}-{stage}", disabled=busy() or not subjects):
            launch_assets(engine, lambda: engine.generate(stage, subjects=subjects, force=True), "Regenerating scene inputs")
    asset_cards(engine, state, stage)
