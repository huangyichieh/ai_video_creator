"""Shared prompts, review and revision controls for complete location reference sets."""
from .image_preview_ui import image_preview
from .revision_labels import revision_label
import streamlit as st

from .dialog_ui import dialog,Deferred,request_open
from .ui_tasks import busy,generation_busy,find_task
from .generation_ui import generation_button


def controls(engine,lid,group):
    from . import location_pairs as pairs
    from .dual_panorama import approved
    from .background_assets_ui import launch_background
    from .floor_plan_guides import current_guides
    state = engine.load()
    pair_names = pairs.names(group)
    prompt_key = f"location-pair-prompt-{engine.project_id}-{lid}-{group}"
    saved = state.location_pair_prompts.get(lid,{}).get(group,
        "Use one coherent visual style, with matching materials, palette and lighting across all views.")
    st.session_state.setdefault(prompt_key,saved)
    st.text_area("Bridge prompt" if group == "bridges" else "Master prompt",key=prompt_key,height=140)
    st.caption("One shared prompt guides the reference set. Camera regions and completed neighboring overlap crops are added automatically.")
    ready = pairs.current(engine,state,lid,group)
    confirmed = bool(ready and all(approved(engine,state,lid,name) for name in pair_names))
    st.markdown(":green[All approved]" if confirmed else ":orange[Reference set needs approval]" if ready else ":orange[Reference set incomplete or outdated]")
    ready_to_generate = bool(current_guides(engine,lid,state.location_reference_sets[lid]))
    import json
    prefix = f"location-reference-set-{engine.project_id}-{lid}"
    spec = state.location_reference_sets[lid]
    try:
        draft_dirty = (st.session_state.get(prefix+"-floor",spec.get("floor_plan","")) != spec.get("floor_plan","") or
            json.loads(st.session_state.get(prefix+"-layout",json.dumps(spec.get("object_layout",{})))) != spec.get("object_layout",{}))
    except ValueError:
        draft_dirty = True
    ready_to_generate = ready_to_generate and not draft_dirty
    if ready and group == "masters":
        from .directional_masters import review_ui
        review_ui(engine,state,lid)
    review = st.checkbox("All images and overlapping regions preserve object identity, layout and style",
        key=f"location-pair-review-{engine.project_id}-{lid}-{group}-"+
            ("-".join(record.asset.asset_id for record in ready.values()) if ready else "pending"),
        disabled=busy() or confirmed or not ready)
    expected = {name:record.asset.asset_id for name,record in ready.items()} if ready else {}
    approving = find_task(lambda job:job.get("stable_background_project") == engine.project_id and
        job.get("asset_subject") == lid and job["label"] == "Approving "+group+" pair" and not job["future"].done())
    with st.container(horizontal=True,gap="small"):
        st.button("Approving..." if approving else "Approved" if confirmed else "Approve bridge" if group == "bridges" else "Approve master",
            key=f"approve-location-pair-{lid}-{group}",disabled=busy() or confirmed or not ready or not review,
            on_click=lambda:launch_background(engine,lambda:pairs.approve(engine,lid,group,expected=expected),
                "Approving "+group+" pair",service="local",subject=lid))
        def browse():
            st.session_state["dialog-context-manager"] = engine.manager
            st.session_state["location-pair-revisions-target"] = {"project_id":engine.project_id,"lid":lid,"group":group,"demo":engine.demo}
            request_open()
        st.button("View / apply saved revisions",key=f"revisions-location-pair-{lid}-{group}",on_click=browse)
        generated = any(state.records.get(f"backgrounds:{lid}__{name}") and state.records[f"backgrounds:{lid}__{name}"].asset for name in pair_names)
        def regenerate():
            prompt = st.session_state[prompt_key]
            launch_background(engine,lambda:pairs.generate(engine,lid,group,prompt),
                "Generating "+group+" pair",service="openai",subject=lid)
        generation_button("Regenerate" if generated else "Generate",key=f"generate-location-pair-{lid}-{group}",
            disabled=generation_busy() or not ready_to_generate,on_click=regenerate)
    if not ready_to_generate:
        st.caption("Save the edited layout and regenerate camera guides first." if draft_dirty else
                   "Generate the current camera guides first.")
    if group == "masters":
        st.caption("Generate four 2560 × 1440 masters directly from their 110-degree directional camera regions, visible floor plans and style guide.")


def close():
    st.session_state.pop("location-pair-revisions-target",None)


def close_and_refresh():
    close()
    st.rerun(scope="app")


@dialog("Location reference revisions",width="large",on_dismiss=close)
def revision_dialog(engine,lid,group):
    from .location_pairs import versions,apply,names,current as current_pair
    from .background_assets_ui import launch_background
    from .error_report_ui import render_error
    saved = versions(engine,lid,group)
    st.caption("Select one complete revision; applying it updates ALL images together and reopens approval.")
    if not saved:
        st.info("No complete saved reference sets yet.")
    else:
        selected = st.selectbox("Reference revision",range(len(saved)),
            format_func=lambda index:revision_label(saved[index],len(saved)-index)+" · "+" / ".join(
                f"{name}: r{saved[index]['records'][name].revision}" for name in names(group)),
            key=f"location-pair-revision-choice-{engine.project_id}-{lid}-{group}")
        item = saved[selected]
        columns = st.columns(2)
        for index,name in enumerate(names(group)):
            column = columns[index % 2]
            with column:
                record = item["records"][name]
                image_preview(str(engine.local_path(record.asset.file_path)),caption=name)
                with st.expander("Generation details",expanded=False):
                    st.json(record.asset.generation_params)
        state = engine.load()
        active = bool(current_pair(engine,state,lid,group)) and all(state.records.get(f"backgrounds:{lid}__{name}") and
            state.records[f"backgrounds:{lid}__{name}"].asset.asset_id == record.asset.asset_id
            for name,record in item["records"].items())
        message_key = f"location-pair-revision-message-{engine.project_id}-{lid}-{group}"
        def apply_selected():
            try:
                apply(engine,lid,group,item["id"])
                st.session_state[message_key] = ("success","All images applied. Review and approve the pair.")
            except (ValueError,OSError) as exc:
                st.session_state[message_key] = ("error",str(exc))
        st.button("Applied" if active else "Apply reference revision",key="apply-location-pair-revision",disabled=busy() or active,on_click=apply_selected)
        if message := st.session_state.get(message_key):
            if message[0] == "success":
                st.success(message[1])
            else:
                render_error(message[1],project_path=engine.path,key="location-pair-revision-error")
    if group == "masters":
        from .directional_masters import VERSION
        older = {name:[record for record in engine.revisions("backgrounds",lid+"__"+name)
            if record.asset and engine._intact(record)
            and record.asset.generation_params.get("config",{}).get("master_source") != VERSION]
            for name in ("front_panorama","back_panorama")}
        if any(older.values()):
            with st.expander("Historical two-master images · preview only",expanded=False):
                st.caption("Saved images are retained. Generate and review all four directional masters to use the new reference set.")
                for column,(name,records) in zip(st.columns(2),older.items()):
                    with column:
                        if not records:
                            continue
                        choice = st.selectbox(name,range(len(records)),
                            format_func=lambda i,records=records:revision_label(records[i]),key="historical-master-"+name)
                        image_preview(str(engine.local_path(records[choice].asset.file_path)),caption=name)
    legacy = {name:[record for record in engine.revisions("backgrounds",lid+"__"+name)
                    if record.asset and not record.asset.generation_params.get("locationPairId") and engine._intact(record)]
              for name in names(group)}
    if all(legacy.values()):
        with st.expander("Older independent revisions",expanded=False):
            st.caption("Select a matching older revision for each image. Apply updates all images together; review style consistency before approval.")
            selection = {}
            columns = st.columns(2)
            for index,name in enumerate(names(group)):
                column = columns[index % 2]
                with column:
                    records = legacy[name]
                    identity = st.selectbox(name,[record.asset.asset_id for record in records],
                        format_func=lambda identity,records=records:revision_label(next(record for record in records if record.asset.asset_id == identity)),
                        key=f"legacy-pair-choice-{engine.project_id}-{lid}-{name}")
                    selection[name] = identity
                    record = next(record for record in records if record.asset.asset_id == identity)
                    image_preview(str(engine.local_path(record.asset.file_path)),caption=name)
            legacy_message_key = f"legacy-pair-message-{engine.project_id}-{lid}-{group}"
            def apply_legacy_selected():
                from .location_pairs import apply_legacy
                try:
                    apply_legacy(engine,lid,group,selection)
                    st.session_state[legacy_message_key] = ("success","All legacy revisions applied. Review and approve the pair.")
                except (OSError,ValueError) as exc:
                    st.session_state[legacy_message_key] = ("error",str(exc))
            st.button("Apply legacy reference set",key="apply-legacy-location-pair",disabled=busy(),on_click=apply_legacy_selected)
            if message := st.session_state.get(legacy_message_key):
                (st.success if message[0] == "success" else st.error)(message[1])
    st.button("Close",key="close-location-pair-revisions",on_click=close_and_refresh)


def show(manager,target):
    from .assets import AssetPipeline
    revision_dialog(Deferred(lambda:AssetPipeline(manager,target["project_id"],demo=target["demo"])),target["lid"],target["group"])
