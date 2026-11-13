"""Prompt-based layout edits stay in a dialog until applied to the draft."""
import json
from uuid import uuid4

import streamlit as st

from .dialog_ui import dialog,Deferred,request_open
from .files import input_hash
from .floor_plan_guides import source_signature


def close():
    st.session_state.pop("layout-modify-target",None)


def dismiss():
    close()
    st.rerun(scope="app")


def controls(engine,lid,spec,prefix):
    def open_dialog():
        try:
            floor = st.session_state[prefix+"-floor"]
            objects = json.loads(st.session_state[prefix+"-layout"])
            from .location_references import LocationReferenceSet
            LocationReferenceSet(floor_plan=floor,object_layout=objects)
            if not floor.strip() or not objects:
                raise ValueError("Provide a floor plan and object layout first.")
        except ValueError as exc:
            st.session_state[prefix+"-modify-error"] = str(exc)
            return
        from .floor_plan_revisions import signature
        camera = st.session_state.get(prefix+"-camera-plan",spec.get("camera_plan"))
        if st.session_state.get(prefix+"-camera-signature",spec.get("camera_plan_signature")) != source_signature(floor,objects):
            camera = None
        st.session_state["dialog-context-manager"] = engine.manager
        st.session_state["layout-modify-target"] = {"project_id":engine.project_id,"lid":lid,"demo":engine.demo,
            "token":uuid4().hex,"prefix":prefix,"floor":floor,"objects":objects,"camera":camera,
            "saved_signature":signature(spec),"draft_signature":input_hash([floor,objects,camera])}
        st.session_state.pop(prefix+"-modify-error",None)
        request_open()
    st.button("Modify object layout with prompt",key=prefix+"-modify-prompt",on_click=open_dialog)
    if error := st.session_state.get(prefix+"-modify-error"):
        st.error(error)


@dialog("Modify object layout",width="large",on_dismiss=close)
def modification_dialog(engine,target):
    active = st.session_state.get("layout-modify-target")
    if not active or active["token"] != target["token"]:
        st.rerun(scope="app")
    st.caption("Describe object moves, facing directions, additions or removals. The floor plan and camera geometry will be updated together. Review the result, then Apply and save.")
    panel(engine,target)
    st.button("Cancel",key="cancel-layout-modification",on_click=dismiss)


@st.fragment
def panel(engine,target):
    from .ui_tasks import launch,find_task,collect,take_task_result,generation_busy
    from .generation_ui import generation_button
    from .action_labels import text_action_label
    key = "layout-modify-"+target["token"]
    owner = ("layout-modify-target",target)
    task = find_task(lambda item:item.get("dialog_owner") == owner)
    if task and task["future"].done():
        collect(task)
        task = None
    if response := take_task_result(engine.project_id,[key+"-generate"]):
        if response[0][0] == "error":
            st.session_state[key+"-error"] = response[0][1]
    prompt = st.text_area("Modification prompt",key=key+"-prompt",height=180,
        placeholder="Move the printer beside the east window and turn the desk to face south.")
    def start():
        from .location_layout import generate_location_layout
        instructions = st.session_state[key+"-prompt"].strip()
        st.session_state.pop(key+"-error",None)
        st.session_state.pop(key+"-result",None)
        launch(lambda:generate_location_layout(engine,target["lid"],"KEY_360",target["floor"],target["objects"],
            camera_plan=target["camera"],update="modify",instructions=instructions,render_guides=False),
            "Modifying object layout",success=lambda result:st.session_state.__setitem__(key+"-result",result),
            dialog_owner=owner,queue_enabled=True,concurrent_safe=True,resource=f"location-layout:{engine.project_id}:{target['lid']}",
            project_path=engine.path,service="openai")
    generation_button(text_action_label("Modify",engine.creative,"location_layout"),key=key+"-generate",
        active_label="Modifying…",activity=("queued" if task.get("phase") in ("queued","waiting") else "running") if task else None,
        disabled=generation_busy() or bool(task) or not prompt.strip(),on_click=start)
    if task:
        # Timer reruns must not redraw text inputs or action buttons. Only the
        # small status fragment polls; completion refreshes the result once.
        watch_task(target,task)
    if error := st.session_state.get(key+"-error"):
        from .error_report_ui import render_error
        render_error(error,project_path=engine.path,task_key=f"assets:backgrounds:{target['lid']}:layout",key=key+"-report")
    if result := st.session_state.get(key+"-result"):
        st.markdown("**Modified floor plan**")
        st.write(result.floor_plan)
        st.markdown("**Modified object layout**")
        st.json(result.object_layout())
        def apply():
            from .floor_plan_revisions import signature
            prefix = target["prefix"]
            try:
                current_objects = json.loads(st.session_state[prefix+"-layout"])
                saved = engine.load().location_reference_sets.get(target["lid"])
                if not saved or signature(saved) != target["saved_signature"]:
                    raise ValueError("Saved reference settings changed. Reload and reopen Modify.")
                current_floor = st.session_state[prefix+"-floor"]
                current_camera = st.session_state.get(prefix+"-camera-plan",saved.get("camera_plan"))
                if st.session_state.get(prefix+"-camera-signature",saved.get("camera_plan_signature")) != source_signature(current_floor,current_objects):
                    current_camera = None
                if input_hash([current_floor,current_objects,current_camera]) != target["draft_signature"]:
                    raise ValueError("The layout draft changed. Reopen Modify to use the latest draft.")
                from .location_layout import save_layout_result
                save_layout_result(engine,target["lid"],result,expected_layout=target["saved_signature"])
                st.session_state[prefix+"-floor"] = result.floor_plan
                st.session_state[prefix+"-layout"] = json.dumps(result.object_layout(),ensure_ascii=False,indent=2)
                st.session_state[prefix+"-camera-plan"] = result.camera_plan.model_dump(mode="json",by_alias=True)
                st.session_state[prefix+"-camera-signature"] = source_signature(result.floor_plan,result.object_layout())
                st.session_state.pop(prefix+"-guides",None)
                st.session_state.pop(prefix+"-check-result",None)
            except (OSError,RuntimeError,TypeError,ValueError) as exc:
                st.session_state[key+"-error"] = str(exc)
                return
            dismiss()
        st.button("Apply and save",key=key+"-apply",on_click=apply,disabled=bool(task))
        st.caption("Applying saves the modified layout automatically. Generate floor plan image to update drawings and camera guides. Existing master images are retained; changed layouts require master review again.")


@st.fragment(run_every=1)
def watch_task(target,task):
    active = st.session_state.get("layout-modify-target")
    if not active or active["token"] != target["token"]:
        return
    if task["future"].done():
        # The main dialog collects the result before rebuilding its controls.
        # Removing this child fragment also stops its timer after completion.
        st.rerun(scope="app")
    st.caption("Queuing…" if task.get("phase") in ("queued","waiting") else "Modifying…")


def show(manager,target):
    from .assets import AssetPipeline
    modification_dialog(Deferred(lambda:AssetPipeline(manager,target["project_id"],demo=target["demo"])),target)
