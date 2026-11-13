"""Draw a fixed rectangle on a playable video, then queue a regional edit."""
from uuid import uuid4

import streamlit as st
import streamlit.components.v2 as components

from .dialog_ui import dialog,Deferred,request_open
from .files import input_hash
from .scene_video_timeline_ui import JS as TIMELINE_JS,HTML as TIMELINE_HTML,CSS as TIMELINE_CSS

JS = TIMELINE_JS.replace("export default function(","function mountTimeline(")+r"""
export default function({parentElement,data,setStateValue}) {
 const video=parentElement.querySelector('video'),canvas=parentElement.querySelector('canvas');
 const ctx=canvas.getContext('2d'),stage=parentElement.querySelector('.stage');
 const play=parentElement.querySelector('.timeline-play');
 const note=parentElement.querySelector('[role=status]');
 let selected=data.rectangle,previous=null,anchor=null,moveBox=false;
 const point=e=>{const r=canvas.getBoundingClientRect();return {
   x:Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),y:Math.min(1,Math.max(0,(e.clientY-r.top)/r.height))};};
 function draw(){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  if(!selected)return;
  const {left,top,right,bottom}=selected,w=canvas.width,h=canvas.height;
  ctx.fillStyle='rgba(0,0,0,.3)';ctx.fillRect(0,0,w,top*h);ctx.fillRect(0,bottom*h,w,(1-bottom)*h);
  ctx.fillRect(0,top*h,left*w,(bottom-top)*h);ctx.fillRect(right*w,top*h,(1-right)*w,(bottom-top)*h);
  ctx.strokeStyle='#ff4b4b';ctx.lineWidth=3;ctx.strokeRect(left*w,top*h,(right-left)*w,(bottom-top)*h);
 }
 function down(e){
  if(e.button!==0)return;e.preventDefault();video.pause();play.textContent='Play';anchor=point(e);previous=selected;
  moveBox=selected&&anchor.x>selected.left&&anchor.x<selected.right&&anchor.y>selected.top&&anchor.y<selected.bottom;
  if(!moveBox)selected={left:anchor.x,top:anchor.y,right:anchor.x,bottom:anchor.y};
  canvas.setPointerCapture(e.pointerId);draw();
 }
 function move(e){
  if(!anchor)return;e.preventDefault();const p=point(e);
  if(moveBox){const w=previous.right-previous.left,h=previous.bottom-previous.top;
   const x=Math.max(0,Math.min(1-w,previous.left+p.x-anchor.x)),y=Math.max(0,Math.min(1-h,previous.top+p.y-anchor.y));
   selected={left:x,top:y,right:x+w,bottom:y+h};
  }else selected={left:Math.min(anchor.x,p.x),top:Math.min(anchor.y,p.y),right:Math.max(anchor.x,p.x),bottom:Math.max(anchor.y,p.y)};
  draw();
 }
 function up(e){
  if(!anchor)return;move(e);anchor=null;
  if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);
  if(!selected||selected.right-selected.left<.005||selected.bottom-selected.top<.005){selected=previous;draw();return;}
  note.textContent='Selected rectangle ready. Drag inside to move it, or outside to draw a new one.';
  setStateValue('selection',{rectangle:selected,time:video.currentTime});
 }
 const events=new AbortController(),opts={signal:events.signal};
 video.addEventListener('loadedmetadata',()=>{
  canvas.width=video.videoWidth;canvas.height=video.videoHeight;
  stage.style.maxWidth=(48*video.videoWidth/video.videoHeight)+'vh';
  draw();
 },opts);
 for(const [name,handler] of [['pointerdown',down],['pointermove',move],['pointerup',up],['pointercancel',()=>{anchor=null;selected=previous;draw();}]])canvas.addEventListener(name,handler,opts);
 const cleanupTimeline=mountTimeline({parentElement,data,setStateValue});
 video.src=data.video;video.muted=true;video.playsInline=true;
 return ()=>{events.abort();cleanupTimeline();video.pause();video.removeAttribute('src');video.load();};
}
"""

picker = components.component("animated_scene_region_picker",
    html='<div class="stage"><video preload="auto"></video><canvas aria-label="Draw replacement rectangle on video"></canvas></div>'
         '<p role="status">Drag on the video to select the replacement area. Drag timeline edges to set the edit interval, or drag the white playhead to preview a frame.</p>'
         '<div class="controls"><button class="timeline-play" type="button">Play</button><span class="current-time"></span></div>'+TIMELINE_HTML,
    css=TIMELINE_CSS+'.stage{position:relative;width:100%;margin:auto}.stage video{display:block;width:100%;height:auto}.stage canvas{position:absolute;inset:0;width:100%;height:100%;touch-action:none;cursor:crosshair}.controls{display:flex;gap:8px;align-items:center;margin-top:8px}p,button{font:14px sans-serif;color:var(--st-text-color)}button{background:var(--st-secondary-background-color);padding:6px 12px;border:1px solid #666;border-radius:5px}',js=JS)


def dismiss():
    st.session_state.pop("scene-video-modify-target",None)


def close(target):
    dismiss()
    st.rerun(scope="app")


def open_modify(service,sid,options,card_target):
    st.session_state["dialog-context-manager"] = service.manager
    st.session_state["scene-video-modify-target"] = {"project_id":service.project_id,"sid":sid,"token":uuid4().hex,
        "card_target":card_target}
    request_open()
    st.rerun(scope=card_target)


@dialog("Modify animated scene · selected region",width="large",on_dismiss=dismiss)
def modify_dialog(service,target):
    from .rendering import RenderPipeline
    from .scene_video_region import playback_preview,region
    from .animated_scene_cards_ui import request,phase
    from .ui_tasks import busy,can_queue_work
    sid = target["sid"]
    record = service.visuals().get(sid,{})
    options = RenderPipeline(service.manager,service.project_id).load().options
    try:
        current_inputs = service.visual_hash(sid,options)
        target.setdefault("expected",input_hash(record))
        target.setdefault("expected_input_hash",current_inputs)
        usable = input_hash(record) == target["expected"] and current_inputs == target["expected_input_hash"]
    except (OSError,ValueError) as exc:
        st.warning(str(exc))
        usable = False
    selection = None
    start_time,end_time=None,None
    valid_range=False
    if usable:
        try:
            if "playback_preview" not in target:
                with st.spinner("Preparing smooth video preview..."):
                    source,size,duration=playback_preview(service,sid,record)
                    target["playback_preview"]=(str(source),size)
                    target["duration"]=duration
            source,size=target["playback_preview"]
            from streamlit.runtime import get_instance
            url = get_instance().media_file_mgr.add(str(source),"video/mp4","scene-edit-"+target["token"])
            key = "scene-region-"+target["token"]
            def selected():
                value = st.session_state.get(key,{}).get("selection") or {}
                target["rectangle"] = value.get("rectangle")
                target["time"] = value.get("time",0)
            def changed_range():
                from .scene_video_timeline_ui import accept_range
                accept_range(target,st.session_state.get(key,{}).get("range") or {})
            start,end=target.setdefault("edit_range",(0.,target["duration"]))
            result = picker(key=key,data={"video":url,"rectangle":target.get("rectangle"),"time":target.get("time",0),
                "duration":target["duration"],"start":start,"end":end},
                default={"selection":None,"range":None},on_selection_change=selected,on_range_change=changed_range)
            selection = (result.selection or {}).get("rectangle") or target.get("rectangle")
            if selection:
                box = region(selection,size)
                st.caption(f"Replacement rectangle: ({box[0]}, {box[1]}) to ({box[2]}, {box[3]}). Coordinates align to video chroma blocks.")
        except (OSError,ValueError,RuntimeError) as exc:
            st.warning(str(exc))
            usable,selection = False,None
    else:
        st.warning("The selected video, direction or settings changed. Reopen Modify on the current scene.")
    if usable and "duration" in target:
        from .scene_video_timeline_ui import render as time_range
        interval=time_range(target,show_timeline=False)
        if interval is not None:
            start_time,end_time=interval
            valid_range=True
    prompt_key = "scene-region-prompt-"+target["token"]
    text = st.text_area("Replacement prompt",key=prompt_key,height=140,
        placeholder="Describe what should appear or happen inside the selected rectangle.")
    st.caption("Runway / Aleph 2.0 edits the cropped video; FFmpeg composites it back. The rectangle is fixed on screen, not object-tracked. Areas outside the effective rectangle use the original footage. Generates a new revision, with recorded API usage; successful current revisions are automatically approved.")
    st.caption("Runway inputs are 2–30 seconds at up to 30 FPS. Short clips use a two-second padded input and are trimmed back; longer scenes are split into parts. The source video outside the rectangle keeps its original timeline.")
    blocked = bool(phase(service.project_id,sid,record)) or (busy() and not can_queue_work())
    def confirm():
        dismiss()
        request(service.project_id,sid,"modify",expected=target["expected"],expected_input_hash=target["expected_input_hash"],
            rectangle=dict(selection),prompt=st.session_state[prompt_key],start_time=start_time,end_time=end_time,
            refresh_scope="app")
    confirm_col,cancel_col = st.columns(2)
    confirm_col.button("Confirm modification",key="confirm-scene-video-region",type="primary",
        disabled=not usable or not valid_range or selection is None or not text.strip() or blocked,on_click=confirm)
    cancel_col.button("Cancel",key="cancel-scene-video-region",on_click=close,args=(target,))


def show(manager,target):
    from .scene_production import SceneProduction
    modify_dialog(Deferred(lambda:SceneProduction(manager,target["project_id"])),target)
