"""Ordered scene input stages, with shared settings managed on Assets."""
from .generation_ui import generation_button
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
        st.caption("Scene images use the selected character model sheets, location/detail images and prop visuals from Assets. Character identity and rendering style stay anchored to the model sheets. Shared selections refresh automatically before generation; review the result with the shot plan in step 2.")
        if (engine.path/"scenes.json").exists():
            panel = st.expander("Storyboard",expanded=False,key=f"animation-storyboard-{project_id}",on_change="rerun")
            if panel.open:
                with panel:
                    st.caption("Background choices include generated detail images from Assets. Refresh shared assets to use newly selected or updated details in this episode. Saving edits reopens storyboard review and affected generation stages.")
                    from .storyboard_editor_ui import render
                    render(engine.creative,generated_details_only=True)
        generation_button("Generate / resume scene images", key=f"animation-input-images-{project_id}", disabled=busy() or not engine.can_generate("scene_images", state),
            on_click=lambda:launch_assets(engine,lambda:engine.generate("scene_images"),"Generating scene images",rerun_on_start=False))
        stage = "scene_images"
    else:
        st.subheader("4. Generate voices and review scene inputs")
        st.caption("After approving direction and animated scenes: generate dialogue tracks → align subtitles → run scene-input QA → review and approve the batch here. Voice casting and audition approval stay on Assets > Character References > Voice.")
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
            st.info("Complete Direction & performance and approve every Animated scene before generating voice tracks.")
        if generation_button("Generate next input: " + stages.get(next_stage, "Review inputs"), key=f"animation-input-next-{project_id}", disabled=busy() or not ready):
            launch_assets(engine, lambda: engine.generate(next_stage), "Generating " + stages[next_stage])
        st.markdown("**Scene-input QA**")
        markdown = engine.path / "asset_review.md"
        if markdown.exists():
            st.markdown(markdown.read_text(encoding="utf-8"))
        if state.consistency_report:
            st.json(state.consistency_report)
        if state.next_stage == "asset_review":
            with st.container(key=f"animation-input-approval-{project_id}"):
                st.markdown("**Approve scene inputs before generating effects and composing scenes**")
                note = st.text_input("Asset review note (optional)", key=f"asset-note-{project_id}-assets")
                checked = st.checkbox("I reviewed these assets and approve continuing", key=f"asset-accept-{project_id}-assets-{engine._gate_hash('assets', state, engine.tasks(state))}")
                if st.button("Approve assets", key="approve-assets-assets", disabled=busy() or not checked):
                    launch(lambda: engine.approve("assets", note), "Approving scene inputs")
        elif state.next_stage == "complete":
            st.success("Scene inputs approved. Continue to Video effects, Sound effects, then Scene review & continuity.")
    records = [r for r in state.records.values() if r.stage == stage]
    if records:
        rows = [{"Regenerate": False, "Subject": r.subject, "Status": r.status, "Revision": r.revision} for r in records]
        selected = st.data_editor(rows, key=f"animation-input-selection-{project_id}-{stage}", hide_index=True,
                                  disabled=busy() or ["Subject", "Status", "Revision"])
        subjects = [r["Subject"] for r in selected if r["Regenerate"]]
        def regenerate_selected():
            launch_assets(engine,lambda:engine.generate(stage,subjects=subjects,force=True),"Regenerating scene inputs",rerun_on_start=False)
        clicked = generation_button("Regenerate selected " + ("images" if images else "voice tracks"), key=f"animation-input-regenerate-{project_id}-{stage}", disabled=busy() or not subjects,
            **({"on_click":regenerate_selected} if images else {}))
        if clicked and not images:
            launch_assets(engine, lambda: engine.generate(stage, subjects=subjects, force=True), "Regenerating scene inputs")
    asset_cards(engine, state, stage)
