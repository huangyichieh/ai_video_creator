"""Per-scene snapshot, editable direction suggestions and approved visual clips."""
from .image_preview_ui import image_preview
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import json
import re
import streamlit as st
from .scene_production import SceneProduction
from .rendering import RenderPipeline
from .ui_tasks import busy, launch, launch_render


def scene_direction_title(sid,ready,description):
    name=re.sub(r"^scene[_ -]*(\d+)",lambda match:"Scene "+match[1].zfill(2),sid,flags=re.IGNORECASE)
    return name+" · "+("Ready" if ready else "Not ready")+" · "+" ".join(description.split())


def direction_cards(manager, project_id):
    service = SceneProduction(manager, project_id)
    if not service.director.load():
        st.info("Build the animation plan to prepare each scene's direction.")
        return
    try:
        service.director.validate_plan(service.director.load().plan)
    except (OSError, ValueError):
        st.info("Direction is outdated. Build the current animation plan before reviewing scenes.")
        return
    for scene in service.assets.creative.artifact("storyboard").scenes:
        target = f"scene-direction-card-{project_id}-{scene.scene_id}"
        st.fragment(key=target)(direction_card)(manager,project_id,scene.scene_id)


def direction_card(manager,project_id,sid):
    def refresh_card():
        st.rerun(scope=f"scene-direction-card-{project_id}-{sid}")
    service = SceneProduction(manager,project_id)
    scene = next((item for item in service.assets.creative.artifact("storyboard").scenes if item.scene_id == sid),None)
    if scene is None:
        st.info("This scene no longer exists. Refresh the scene list.")
        return
    production = service.assets.load()
    try:
        direction=service.direction(sid)
        description=" / ".join(shot.action.strip() for shot in direction.shots if shot.action.strip())
    except (OSError,ValueError):
        description=""
    description=description or scene.action or scene.visual_description or "No direction yet"
    from .ui_panels import persistent_expander
    panel = persistent_expander(scene_direction_title(sid,service.direction_ready(sid),description),
        key=f"scene-direction-panel-{project_id}-{sid}",on_change=refresh_card)
    if not panel.open:
        return
    with panel:
        image = production.records.get("scene_images:" + sid)
        if image and image.asset and service.assets.local_path(image.asset.file_path).exists():
            image_preview(str(service.assets.local_path(image.asset.file_path)), width=500)
        try:
            current = service.direction(sid)
            expected = service.direction_hash(sid)
        except ValueError as exc:
            st.info(str(exc))
            return
        st.write(scene.visual_description)
        prompt = st.text_area("Direction request (optional)", key=f"studio-draft-scene-request-{project_id}-{sid}", disabled=busy(),on_change=refresh_card)
        generate, review, modify = st.columns(3)
        baseline=current.model_dump_json(by_alias=True,indent=2)
        manual_key=f"studio-draft-scene-direction-{project_id}-{sid}-{expected}"
        manual_changed=st.session_state.get(manual_key,baseline)!=baseline
        regenerate_key=f"scene-regenerate-direction-{project_id}-{sid}"
        def regenerate():
            task=launch(lambda:service.regenerate_direction(sid,expected=expected,instructions=prompt),
                "Regenerating animation direction "+sid,inline=True,rerun_on_start=False,queue_enabled=True,
                project_path=service.path,resource="scene-direction:"+sid,service="openai")
            if task:
                task.update(generation_button_project=project_id,generation_button_key=regenerate_key)
            refresh_card()
        generation_button("Regenerate direction",container=generate,key=regenerate_key,
            disabled=busy() or manual_changed,on_click=regenerate,
            help="Regenerate and apply only this scene's direction. Save manual edits first.")
        for column, label, purpose in ((review, "AI review", "Review direction and performance for clarity, continuity and typos."),
                                        (modify, "Modify with prompt", prompt)):
            if label == "AI review":
                from .generation_ui import review_button
                review_button(label,container=column,key=f"scene-direction-{label}-{project_id}-{sid}",disabled=busy(),
                    on_click=lambda sid=sid,purpose=purpose:launch(lambda:service.propose(sid,purpose),"Preparing scene direction suggestions"))
            elif column.button(label, key=f"scene-direction-{label}-{project_id}-{sid}", disabled=busy() or label == "Modify with prompt" and not prompt.strip()):
                launch(lambda sid=sid, purpose=purpose: service.propose(sid, purpose), "Preparing scene direction suggestions")
        proposal = service.proposals().get(sid)
        pending_proposal = bool(proposal and proposal["sourceHash"] == expected and not proposal.get("savedHash"))
        selected, resolved = None, False
        review_id = None
        choice_key = f"studio-review-choice-direction-{project_id}-{sid}-{expected[:8]}"
        if proposal and proposal["sourceHash"] == expected and not proposal.get("savedHash"):
            review_id = proposal.get("reviewId", "")
            choice_key += "-" + review_id
            left, right = st.columns(2)
            left.text_area("Original direction", value=json.dumps(proposal["original"], ensure_ascii=False, indent=2), disabled=True,
                           key=f"scene-original-{project_id}-{sid}-{review_id}", height=260)
            draft_key = f"studio-draft-scene-proposal-{project_id}-{sid}-{expected[:8]}-{review_id}"
            st.session_state.setdefault(draft_key, json.dumps(proposal["suggested"], ensure_ascii=False, indent=2))
            def suggestion_changed():
                st.session_state.pop(choice_key,None)
                refresh_card()
            selected = right.text_area("Suggested direction — editable", key=draft_key, height=260, disabled=busy(),
                on_change=suggestion_changed)
            apply, revert = st.columns(2)
            def choose(value):
                st.session_state[choice_key] = value
                refresh_card()
            apply.button("Apply changes", key=f"scene-apply-{project_id}-{sid}", disabled=busy(),
                on_click=choose,args=("apply",))
            revert.button("Revert changes", key=f"scene-revert-{project_id}-{sid}", disabled=busy(),
                on_click=choose,args=("revert",))
            resolved = st.session_state.get(choice_key) in ("apply", "revert")
        else:
            baseline = current.model_dump_json(by_alias=True,indent=2)
            draft_key = f"studio-draft-scene-direction-{project_id}-{sid}-{expected}"
            selected = st.text_area("Scene direction (editable)",value=baseline,key=draft_key,height=300,disabled=busy(),on_change=refresh_card)
            resolved = selected != baseline
        choice = st.session_state.get(choice_key,"apply") if pending_proposal else "apply"
        if st.button("Save direction",key=f"scene-save-direction-{project_id}-{sid}",disabled=busy() or not resolved):
            launch(lambda sid=sid,expected=expected,text=selected,choice=choice,rid=review_id:
                service.save_direction(sid,expected=expected,text=text,choice=choice,review_id=rid),"Saving scene direction")
        st.caption("Saved direction and performance are ready to use without approval. AI suggestions are optional and take effect only when saved.")


def animated_scenes(manager, project_id):
    service = SceneProduction(manager, project_id)
    engine = RenderPipeline(manager, project_id)
    service.stop_requested = lambda: engine.stop_requested()
    render = engine.load()
    settings_target = f"animated-video-settings-{project_id}"
    st.fragment(key=settings_target)(animated_video_settings)(manager,project_id,settings_target)
    from .animated_scene_cards_ui import batch_controls
    batch_controls(manager,project_id)
    try:
        service.director.validate_plan(service.director.load(required=True).plan)
    except (OSError, ValueError):
        st.info("Build the current direction and performance plan first.")
        return
    st.caption("Generate silent scene motion from current snapshots and saved direction. Current successful clips are automatically approved. Voices and inputs are added next; composition fits visual timing to recorded voices without another video API request.")
    from .animated_scene_cards_ui import render as scene_cards
    scene_cards(manager,project_id)


def animated_video_settings(manager,project_id,target):
    from .final_ui import video_settings
    engine = RenderPipeline(manager,project_id)
    video_settings(engine,engine.load(),project_id,animations=True,refresh_target=target)


def sound_effects(manager, project_id):
    engine = RenderPipeline(manager, project_id)
    render = engine.load()
    if not engine.assets.load().scenes:
        st.info("Generate and approve Voices & input first to set sound timing.")
        return
    board = engine.assets.creative.artifact("storyboard").scenes
    if any(scene.scene_id not in render.music_plan.scenes for scene in board):
        st.info("Approve scene inputs before configuring sound timing and effects.")
        return
    from .final_ui import sound_plan
    state = engine.finishing.load()
    provider = st.selectbox("Sound effect provider", ["local", "elevenlabs"], index=0 if state.plan.sound_provider == "local" else 1,
                            key=f"finishing-setting-{project_id}-sound-provider-separate", disabled=busy())
    if st.button("Save sound provider", key="sound-provider-save-" + project_id, disabled=busy() or provider == state.plan.sound_provider):
        plan = state.plan.model_copy(update={"sound_provider": provider})
        expected = state.plan.model_dump_json(by_alias=True)
        launch(lambda: engine.finishing.configure(plan, expected=expected), "Saving sound provider")
    sound_plan(engine, render, project_id + "-sound")
    tasks = {key: value for key, value in engine.finishing.tasks(engine.assets.load(), board, render).items() if key.startswith("sfx:")}
    rows = [{"Regenerate": False, "Item": key, "Scene": value[0], "Description": value[1],
             "Status": state.records[key].status if key in state.records else "pending"} for key, value in tasks.items()]
    if rows:
        selected = st.data_editor(rows, hide_index=True, key="sound-tasks-" + project_id,
                                  disabled=busy() or ["Item", "Scene", "Description", "Status"])
        keys = [row["Item"] for row in selected if row["Regenerate"]]
        if generation_button("Generate / regenerate selected sounds", key="sound-generate-" + project_id, disabled=busy() or not keys):
            launch_render(engine, lambda: engine.finishing.generate_selected(keys, force=True), "Generating selected sound effects")
    for key, record in state.records.items():
        if key.startswith("sfx:") and record.asset and engine.assets.local_path(record.asset.file_path).exists():
            with st.expander(key + " · revision " + str(record.revision)):
                st.audio(str(engine.assets.local_path(record.asset.file_path)))
                st.json(record.model_dump(mode="json", by_alias=True))
