"""Canonical geometry / appearance pairs and reference-pack review."""
import streamlit as st

from .generation_ui import generation_button
from .ui_tasks import busy, generation_busy, asset_activity


def review_key(engine,lid,digest):
    return f"canonical-review-{engine.project_id}-{lid}-{digest}"


def outputs(engine,lid):
    from .room_canonical import ANGLES, records, tasks, type_task
    from .shared_room import current
    from .background_assets_ui import launch_background, loading_result, background_image_title
    from .background_library import BackgroundLibrary
    from .error_report_ui import render_error
    state = engine.load()
    spec = state.location_reference_sets[lid]
    graph = tasks(lid,spec,type_task())
    raw_jobs = [t for t in graph if t.operation == "room_canonical_raw"]
    enhanced_jobs = [t for t in graph if t.operation == "room_canonical_enhanced"]
    st.markdown("**Canonical views & AI enhancement**")
    st.caption(f"Raw geometry: {len(records(engine,state,lid,enhanced=False))}/8 · Enhanced appearance: {len(records(engine,state,lid))}/8. Fixed camera center/height, pitch 0°, FOV 70°. Enhancement uses OpenAI; no independent AI layout generation.")
    generation_button("Generate 8 canonical 3D views",key="render-room-canonical-"+lid,
        disabled=generation_busy() or not current(engine,state,lid),on_click=lambda:
        launch_background(engine,lambda:engine.generate("backgrounds",subjects=[t.subject for t in raw_jobs]),
                          "Rendering canonical 3D views",service="local",subject=lid))
    generation_button("Generate 8 enhanced canonical views",key="enhance-room-canonical-"+lid,
        disabled=generation_busy() or len(records(engine,state,lid,enhanced=False)) != 8,on_click=lambda:
        launch_background(engine,lambda:engine.generate("backgrounds",subjects=[t.subject for t in enhanced_jobs]),
                          "Enhancing canonical views",service="openai",subject=lid))
    columns = st.columns(3,vertical_alignment="top")
    for index,yaw in enumerate(ANGLES):
        with columns[index%3]:
            st.markdown(f"**Yaw {yaw:03d}°**")
            for kind in ("raw","enhanced"):
                subject = f"{lid}__{kind}_{yaw:03d}"
                record = state.records.get("backgrounds:"+subject)
                job = next(t for t in graph if t.subject == subject)
                background_image_title(engine,"Raw 3D" if kind == "raw" else "AI enhanced",record,subject)
                activity = asset_activity(engine,"backgrounds",subject)
                if activity == "running":
                    loading_result("Generating…")
                elif activity == "queued":
                    st.caption("Queuing…")
                if record and engine._intact(record):
                    st.image(str(engine.local_path(record.asset.file_path)),caption=f"{record.status} · revision {record.revision}")
                else:
                    st.caption("Pending")
                if record and record.error:
                    render_error(record.error,project_path=engine.path,task_key="assets:backgrounds:"+subject,key="canonical-error-"+subject)
                if kind == "enhanced":
                    from .background_revisions_ui import actions
                    actions(engine,subject,allow_generate=False)
                    from .action_labels import action_label
                    config = engine._config(job,state)
                    generation_button(action_label("Regenerate",config.provider,config.model),key="regenerate-canonical-"+subject,
                        activity=activity,disabled=generation_busy() or not all(engine._intact(state.records.get(ref)) for ref in job.refs),
                        on_click=lambda subject=subject:launch_background(engine,
                            lambda:engine.generate("backgrounds",subject=subject,force=True),
                            "Enhancing canonical view",service="openai",subject=lid))
    library = BackgroundLibrary(engine)
    digest = library.digest(lid,state,engine.tasks(state))
    approved = library.approved(lid,state)
    st.caption("Compare each enhanced image to its raw render: fixed objects must not move, duplicate, disappear or change size. Check shared objects and lighting across neighboring views. AI prompt constraints are not a geometry guarantee; regenerate conflicting views before approval.")
    checked = st.checkbox("All eight enhanced views preserve the shared room geometry and agree in overlapping areas",
                          key=review_key(engine,lid,digest),disabled=not digest or busy() or approved)
    st.button("Approved" if approved else "Approve location reference pack",key="approve-shared-room-"+lid,
        disabled=busy() or approved or not digest or not checked,on_click=lambda:
        launch_background(engine,lambda:library.approve(lid,review_choices={lid:{"signature":digest,
            "canonicalGeometryConfirmed":True}}),"Approving background reference pack",service="local",subject=lid))
