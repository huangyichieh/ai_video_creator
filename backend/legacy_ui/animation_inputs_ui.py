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
        from .scene_image_references import SCENE_IMAGE_MODEL
        st.caption("Scene-image provider: "+("Demo (offline)" if engine.demo else "OpenAI / "+SCENE_IMAGE_MODEL))
        st.caption("Scene images use the selected character model sheets, location/detail images and prop visuals from Assets. Character identity and rendering style stay anchored to the model sheets. Shared selections refresh automatically before generation; review the result with the shot plan in step 2.")
        def generate_scene_images():
            task = launch_assets(engine,lambda:engine.generate("scene_images"),"Generating scene images",rerun_on_start=False,service="openai")
            if task:
                task.update(scene_image_project=project_id,scene_image_subjects=[scene.scene_id
                    for scene in engine.creative.artifact("storyboard").scenes
                    if not state.records.get("scene_images:"+scene.scene_id)
                    or state.records["scene_images:"+scene.scene_id].status != "succeeded"])
                task["scene_image_baselines"] = {sid:(state.records["scene_images:"+sid].revision,
                    state.records["scene_images:"+sid].attempts) if "scene_images:"+sid in state.records else (0,0)
                    for sid in task["scene_image_subjects"]}
        generation_button("Generate / resume scene images", key=f"animation-input-images-{project_id}", disabled=busy() or not engine.can_generate("scene_images", state),
            on_click=generate_scene_images)
        stage = "scene_images"
    else:
        st.subheader("4. Generate voices and review scene inputs")
        st.caption("After preparing direction and creating animated scenes: generate dialogue tracks → align subtitles → run scene-input QA → review and approve the batch here. Created current animated scenes are automatically approved. Voice casting and audition approval stay on Assets > Character References > Voice.")
        stages = {"voices": "Voice tracks", "subtitles": "Subtitle alignment", "consistency": "Scene-input QA"}
        stage = "voices"
        next_stage = state.next_stage
        # Use the generation engine's actual gates. require_ready() also checks
        # legacy whole-library approvals, which do not gate episode voice tracks.
        voice_blocker=engine.generation_blocker("voices",state)
        def generate_voices():
            launch_assets(engine,lambda:engine.generate("voices"),"Generating all voice tracks",
                service=engine.settings.tts_provider)
        generation_button("Generate all voice tracks",key=f"animation-input-all-voices-{project_id}",
            disabled=busy() or voice_blocker is not None,on_click=generate_voices,
            help="Generate missing or outdated dialogue tracks for this episode. Current successful tracks are kept.")
        blocker=engine.generation_blocker(next_stage,state) if next_stage in stages else None
        ready=next_stage in stages and blocker is None
        if message:=voice_blocker or blocker:
            st.info(message)
        def generate_next():
            launch_assets(engine,lambda:engine.generate(next_stage),"Generating "+stages[next_stage],
                service=engine.settings.tts_provider if next_stage=="voices" else "openai")
        generation_button("Generate next input: " + stages.get(next_stage, "Review inputs"),
            key=f"animation-input-next-{project_id}",disabled=busy() or not ready,on_click=generate_next)
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
            st.success("Scene inputs approved. Continue to VFX, SFX, then Scene review & continuity.")
    records = [r for r in state.records.values() if r.stage == stage]
    if records:
        rows = [{"Regenerate": False, "Subject": r.subject, "Status": r.status, "Revision": r.revision} for r in records]
        selected = st.data_editor(rows, key=f"animation-input-selection-{project_id}-{stage}", hide_index=True,
                                  disabled=busy() or ["Subject", "Status", "Revision"])
        subjects = [r["Subject"] for r in selected if r["Regenerate"]]
        def regenerate_selected():
            task = launch_assets(engine,lambda:engine.generate(stage,subjects=subjects,force=True),"Regenerating scene inputs",rerun_on_start=False,
                service="openai" if images else None)
            if task and images:
                task.update(scene_image_project=project_id,scene_image_subjects=list(subjects),
                    scene_image_baselines={sid:(state.records["scene_images:"+sid].revision,state.records["scene_images:"+sid].attempts)
                        if "scene_images:"+sid in state.records else (0,0) for sid in subjects})
        clicked = generation_button("Regenerate selected " + ("images" if images else "voice tracks"), key=f"animation-input-regenerate-{project_id}-{stage}", disabled=busy() or not subjects,
            **({"on_click":regenerate_selected} if images else {}))
        if clicked and not images:
            launch_assets(engine, lambda: engine.generate(stage, subjects=subjects, force=True), "Regenerating scene inputs")
    asset_cards(engine, state, stage)
