"""Drag-to-aim floor-plan component; Python receives one update on release."""
import streamlit as st
import streamlit.components.v2 as components

from .detail_camera import geometry,validate
from .files import input_hash

JS = r"""
export default function(component) {
  const {parentElement, data, setStateValue} = component;
  const canvas = parentElement.querySelector('canvas'), ctx = canvas.getContext('2d');
  const note = parentElement.querySelector('[role=status]');
  const t = data.transform;
  let camera = data.camera, dragging = false, previous = camera;
  const xy = p => ({x:50+(p.x-t.minX)*t.scale,y:550-(p.y-t.minY)*t.scale});
  const world = p => ({x:t.minX+(p.x-50)/t.scale,y:t.minY+(550-p.y)/t.scale});
  const cursor = e => {const r=canvas.getBoundingClientRect(); return {x:(e.clientX-r.left)*800/r.width,y:(e.clientY-r.top)*600/r.height};};
  function inside(p) {
    let hit=false; const pts=data.boundary;
    for(let i=0,j=pts.length-1;i<pts.length;j=i++) {
      const a=pts[i],b=pts[j];
      if((a.y>p.y)!=(b.y>p.y) && p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x) hit=!hit;
    } return hit;
  }
  function polygon(points,fill) {
    ctx.beginPath(); points.forEach((p,i)=>{p=xy(p); i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);});
    ctx.closePath();ctx.fillStyle=fill;ctx.fill();ctx.strokeStyle='#37475c';ctx.lineWidth=2;ctx.stroke();
  }
  function draw() {
    ctx.clearRect(0,0,800,600);ctx.fillStyle='#f5f6f8';ctx.fillRect(0,0,800,600);
    polygon(data.boundary,'#ffffff');ctx.font='12px sans-serif';ctx.textAlign='center';
    for(const item of data.objects) {
      polygon(item.footprint,item.color);
      const center=item.footprint.reduce((p,q)=>({x:p.x+q.x/item.footprint.length,y:p.y+q.y/item.footprint.length}),{x:0,y:0});
      const p=xy(center); ctx.strokeStyle='white';ctx.lineWidth=3;ctx.strokeText(item.label,p.x,p.y);
      ctx.fillStyle='#152536';ctx.fillText(item.label,p.x,p.y);
      if(item.frontYaw!==null && item.frontYaw!==undefined) {
        const a=item.frontYaw*Math.PI/180,dx=Math.sin(a),dy=-Math.cos(a);
        const cx=Math.min(783,p.x+ctx.measureText(item.label).width/2+12),cy=p.y;
        const ex=cx+7*dx,ey=cy+7*dy,bx=ex-6*dx,by=ey-6*dy;
        ctx.strokeStyle='#285ca8';ctx.fillStyle='#285ca8';ctx.lineWidth=3;
        ctx.beginPath();ctx.moveTo(cx-7*dx,cy-7*dy);ctx.lineTo(ex,ey);ctx.stroke();
        ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(bx-4*dy,by+4*dx);ctx.lineTo(bx+4*dy,by-4*dx);ctx.closePath();ctx.fill();
      }
    }
    ctx.textAlign='left';ctx.fillStyle='#243447';ctx.fillText('NORTH ↑',12,20);
    if(!camera) return;
    const p=xy(camera.position), a=(camera.yaw-90)*Math.PI/180;
    const half=Number(data.fov??70)*Math.PI/360;
    const radius=120*800/(canvas.getBoundingClientRect().width||800);
    ctx.strokeStyle='#f02b3c';ctx.fillStyle='rgba(240,43,60,0.16)';ctx.lineWidth=2;
    ctx.beginPath();ctx.moveTo(p.x,p.y);
    ctx.arc(p.x,p.y,radius,a-half,a+half);ctx.closePath();ctx.fill();ctx.stroke();
    ctx.fillStyle='#f02b3c';
    ctx.beginPath();ctx.arc(p.x,p.y,6,0,2*Math.PI);ctx.fill();
  }
  function aim(e) {
    const mouse=cursor(e), p=xy(camera.position), dx=mouse.x-p.x,dy=mouse.y-p.y;
    if(Math.hypot(dx,dy)>2) camera={...camera,yaw:(Math.atan2(dx,-dy)*180/Math.PI+360)%360};
    draw();
  }
  function down(e) {
    if(data.disabled || e.button!==0) return;
    const position=world(cursor(e));
    if(!inside(position)) {note.textContent='Place the camera inside the room boundary.';return;}
    e.preventDefault(); previous=camera;
    camera={...camera,position,yaw:camera?.yaw??0};dragging=true;canvas.setPointerCapture(e.pointerId);
    note.textContent='Drag to aim; release to save.';draw();
  }
  function move(e) {if(dragging){e.preventDefault();aim(e);}}
  function up(e) {
    if(!dragging) return;aim(e);dragging=false;
    if(canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    note.textContent='Camera saved. Press and drag again to reposition or aim.';
    setStateValue('camera',camera);
  }
  function cancel() {if(dragging){dragging=false;camera=previous;draw();}}
  const events = new AbortController();
  canvas.addEventListener('pointerdown',down,{signal:events.signal});
  canvas.addEventListener('pointermove',move,{signal:events.signal});
  canvas.addEventListener('pointerup',up,{signal:events.signal});
  canvas.addEventListener('pointercancel',cancel,{signal:events.signal});
  canvas.addEventListener('lostpointercapture',cancel,{signal:events.signal});
  const resize = new ResizeObserver(draw);resize.observe(canvas);draw();
  return ()=>{events.abort();resize.disconnect();};
}
"""

picker = components.component("detail_floor_plan_camera",
    html='<canvas width="800" height="600" aria-label="Camera setup: press to place the camera and drag to aim"></canvas><p role="status">Press inside the room, drag to aim, and release to save.</p>',
    css='canvas{display:block;width:100%;max-width:800px;height:auto;touch-action:none;cursor:crosshair;border-radius:8px}p{font:14px sans-serif;color:var(--st-text-color);margin:6px 0}',js=JS)


def render(spec,key,camera=None,disabled=False,*,fov=70,on_save=None):
    plan,kind,transform = geometry(spec)
    signature = input_hash(plan.model_dump(mode="json"))[:12]
    cache_key = "studio-draft-detail-camera-"+key+signature
    initial = (camera or {}).get("floorPlanSetup")
    if camera and not initial:
        initial = {"position":plan.camera.model_dump(),"yaw":float(camera.get("yaw",0))}
    saved = initial if on_save is not None else st.session_state.get(cache_key,initial)
    component_key = key+signature
    if on_save is None:
        saved = st.session_state.get(component_key,{}).get("camera",saved)
    if saved:
        try:
            saved = validate(spec,saved)
        except ValueError:
            saved = None
    def commit_camera():
        if on_save is not None:
            selection = st.session_state.get(component_key,{}).get("camera")
            try:
                on_save(validate(spec,selection))
            except (OSError,ValueError,RuntimeError) as exc:
                st.error("Camera setup could not be saved: "+str(exc))
    result = picker(key=component_key,data={"transform":transform,"boundary":[p.model_dump() for p in plan.boundary],
        "objects":[{"footprint":[p.model_dump() for p in item.footprint],"color":item.material_color,"label":item.object_id or item.source_name,"frontYaw":item.front_yaw} for item in plan.objects],
        "camera":saved,"disabled":disabled,"fov":float(fov)},default={"camera":saved},on_camera_change=commit_camera)
    # Durable owners reload the committed value before rendering. Uncommitted
    # widget state must not override it after a failed save or another session's edit.
    selected = saved if on_save is not None else result.camera
    if selected:
        try:
            selected = validate(spec,selected)
        except ValueError as exc:
            st.warning(str(exc)+" Choose the camera again on the updated plan.")
            return None
        from .detail_camera_pose_ui import render as pose_editor
        selected = pose_editor(spec,component_key,selected,disabled=disabled,on_save=on_save)
        st.session_state[cache_key] = selected
        st.caption(f"Camera x={selected['position']['x']:.2f}, y={selected['position']['y']:.2f} · direction {selected['yaw']:.1f}° · height {selected['heightMeters']:.2f} m · tilt {selected['pitch']:.1f}°")
    if kind != "metric":
        st.caption("Schematic floor plan: positions are relative, not measured distances.")
    return selected
