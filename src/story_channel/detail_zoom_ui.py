"""Rectangle selection inside a deferred detail-image zoom dialog."""
import base64
import streamlit as st
import streamlit.components.v2 as components

from .dialog_ui import dialog,request_open,Deferred
from .files import input_hash

JS = r"""
export default function({parentElement,data,setStateValue}) {
  const canvas=parentElement.querySelector('canvas'),ctx=canvas.getContext('2d');
  const note=parentElement.querySelector('[role=status]'),image=new Image();
  let selected=data.rectangle,anchor=null,previous=null;
  const point=e=>{const r=canvas.getBoundingClientRect();return {
    x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),
    y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};};
  function draw() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    if(image.complete) ctx.drawImage(image,0,0,canvas.width,canvas.height);
    if(!selected) return;
    const {left,top,right,bottom}=selected,w=canvas.width,h=canvas.height;
    ctx.fillStyle='rgba(0,0,0,.45)';
    ctx.fillRect(0,0,w,top*h);ctx.fillRect(0,bottom*h,w,(1-bottom)*h);
    ctx.fillRect(0,top*h,left*w,(bottom-top)*h);ctx.fillRect(right*w,top*h,(1-right)*w,(bottom-top)*h);
    ctx.strokeStyle='#ff4b4b';ctx.lineWidth=3;ctx.strokeRect(left*w,top*h,(right-left)*w,(bottom-top)*h);
  }
  function move(e) {
    if(!anchor) return;
    e.preventDefault();const p=point(e);
    selected={left:anchor.x,top:anchor.y,right:Math.max(anchor.x,p.x),bottom:Math.max(anchor.y,p.y)};draw();
  }
  function down(e) {
    if(e.button!==0) return;e.preventDefault();previous=selected;anchor=point(e);
    selected={left:anchor.x,top:anchor.y,right:anchor.x,bottom:anchor.y};
    canvas.setPointerCapture(e.pointerId);draw();
  }
  function up(e) {
    if(!anchor) return;move(e);anchor=null;
    if(canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    if(selected.right<=selected.left || selected.bottom<=selected.top) {
      selected=previous;note.textContent='Drag from top-left to bottom-right to select an area.';draw();return;
    }
    note.textContent='Selected area ready. Confirm to regenerate this zoomed view.';
    setStateValue('rectangle',selected);
  }
  function cancel(){if(anchor){anchor=null;selected=previous;draw();}}
  image.onload=()=>{
    canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    canvas.style.width='min(100%, '+(55*image.naturalWidth/image.naturalHeight)+'vh)';draw();
  };
  image.src=data.image;
  const events=new AbortController();
  for(const [event,handler] of [['pointerdown',down],['pointermove',move],['pointerup',up],['pointercancel',cancel],['lostpointercapture',cancel]])
    canvas.addEventListener(event,handler,{signal:events.signal});
  return ()=>{events.abort();image.onload=null;};
}
"""

picker = components.component("detail_background_zoom",html='<canvas aria-label="Select zoom area: drag from top-left to bottom-right"></canvas><p role="status">Drag from top-left to bottom-right to select the zoom area.</p>',
    css='canvas{display:block;width:100%;height:auto;margin:0 auto;touch-action:none;cursor:crosshair;border-radius:8px}p{font:14px sans-serif;color:var(--st-text-color)}',js=JS)


def dismiss():
    st.session_state.pop("detail-zoom-target",None)


def open_zoom(engine,lid,identity,reference):
    from uuid import uuid4
    st.session_state["dialog-context-manager"] = engine.manager
    st.session_state["detail-zoom-target"] = {"project_id":engine.project_id,"lid":lid,"id":identity,
        "expected":input_hash(reference),"demo":engine.demo,"token":uuid4().hex}
    request_open()


@dialog("Zoom detail background",width="large",on_dismiss=dismiss)
def zoom_dialog(engine,target):
    from .detail_zoom import rectangle
    from .location_details import current,generate
    from .ui_tasks import generation_busy,asset_activity
    from .background_assets_ui import launch_background
    from PIL import Image
    state = engine.load()
    lid,identity = target["lid"],target["id"]
    reference = state.location_reference_sets.get(lid,{}).get("detail_references",{}).get(identity)
    usable = bool(reference and input_hash(reference) == target["expected"] and current(engine,state,lid,reference))
    selection = None
    if usable:
        path = engine.local_path(reference["file"])
        with Image.open(path) as image:
            size = image.size
        key = "detail-zoom-selection-"+target["token"]
        selected = target.get("rectangle")
        def save_selection():
            value = st.session_state.get(key,{}).get("rectangle")
            # Retain a separate dialog draft when the component is remounted.
            target["rectangle"] = dict(value) if value else None
        result = picker(key=key,data={"image":"data:image/png;base64,"+base64.b64encode(path.read_bytes()).decode(),
            "rectangle":selected},default={"rectangle":selected},on_rectangle_change=save_selection)
        selection = result.rectangle or selected
        if selection:
            try:
                box = rectangle(selection,size)
                st.caption(f"Selected region: {box[2]-box[0]} × {box[3]-box[1]} pixels. Regenerates with OpenAI at 1536 × 864.")
            except ValueError as exc:
                st.warning(str(exc))
                selection = None
        else:
            st.caption("Select an area and release the mouse to enable Confirm.")
        st.caption("The selected region will fill the new image as closely as possible. Fine detail may be added; the original remains in revision history.")
    else:
        st.warning("This detail changed or is no longer current. Close this dialog and regenerate or reopen Zoom on the latest image.")
    same_detail_active = asset_activity(engine,"backgrounds",lid+"__detail_"+identity) in ("queued","running")
    blocked = generation_busy() or same_detail_active
    if same_detail_active:
        st.caption("This detail already has a queued or running task. Wait for it to finish, then reopen Zoom.")
    elif blocked:
        st.caption("Generation is currently unavailable. Finish or stop the active task, then try again.")
    confirm,cancel = st.columns(2)
    if confirm.button("Confirm",key="confirm-detail-zoom",type="primary",disabled=not usable or selection is None or blocked):
        payload = {"expected":target["expected"],"rectangle":dict(selection)}
        task = launch_background(engine,lambda:generate(engine,lid,identity,reference.get("area",identity),
            reference.get("description",""),zoom=payload),"Zooming detail background "+identity,
            service="openai",subject=lid+"__detail_"+identity)
        if task:
            dismiss()
            st.rerun(scope="app")
    if cancel.button("Cancel",key="cancel-detail-zoom"):
        dismiss()
        st.rerun(scope="app")


def show(manager,target):
    from .assets import AssetPipeline
    zoom_dialog(Deferred(lambda:AssetPipeline(manager,target["project_id"],demo=target["demo"])),target)
