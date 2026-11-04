"""Side-view camera pose: draggable height and optical-axis tilt lines."""
import streamlit as st
import streamlit.components.v2 as components

from .detail_camera import geometry,validate
from .files import input_hash

JS = r"""
export default function(component) {
  const {parentElement,data,setStateValue}=component;
  const canvas=parentElement.querySelector('canvas'),ctx=canvas.getContext('2d'),note=parentElement.querySelector('[role=status]');
  const floor=250,ceiling=40,cx=130,scale=(floor-ceiling)/data.roomHeight;
  const margin=Math.min(.05,data.roomHeight*.05);
  let pose={...data.pose},previous={...pose},drag=null,startY=0;
  const mouse=e=>{const r=canvas.getBoundingClientRect();return{x:(e.clientX-r.left)*760/r.width,y:(e.clientY-r.top)*300/r.height};};
  function points(){const y=floor-pose.heightMeters*scale,a=pose.pitch*Math.PI/180;return{y,ex:cx+140*Math.cos(a),ey:y-140*Math.sin(a)};}
  function line(x,y,xx,yy,color,width=3){ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(xx,yy);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();}
  function draw(){
    ctx.clearRect(0,0,760,300);ctx.fillStyle='#f5f6f8';ctx.fillRect(0,0,760,300);
    const p=points();line(35,floor,725,floor,'#7c8796',2);line(35,ceiling,725,ceiling,'#7c8796',2);
    ctx.font='14px sans-serif';ctx.fillStyle='#243447';ctx.fillText('Ceiling',40,30);ctx.fillText('Floor',40,273);
    line(cx,floor,cx,p.y,'#285ca8',4);line(cx,p.y,p.ex,p.ey,'#d97706',4);
    ctx.fillStyle='#243447';ctx.fillRect(cx-10,p.y-7,20,14);
    ctx.fillStyle='#285ca8';ctx.fillText('Height: '+pose.heightMeters.toFixed(2)+' m',350,115);
    ctx.fillStyle='#d97706';ctx.fillText('Tilt: '+pose.pitch.toFixed(1)+' deg (positive = up)',350,145);
    ctx.beginPath();ctx.arc(cx,(floor+p.y)/2,5,0,2*Math.PI);ctx.fillStyle='#285ca8';ctx.fill();
    ctx.beginPath();ctx.arc(p.ex,p.ey,6,0,2*Math.PI);ctx.fillStyle='#d97706';ctx.fill();
  }
  function distance(p,a,b){const dx=b.x-a.x,dy=b.y-a.y,t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);}
  function down(e){
    if(data.disabled||e.button!==0)return;
    const m=mouse(e),p=points();
    if(Math.abs(m.x-cx)<12&&m.y>=p.y-12&&m.y<=floor+8)drag='height';
    else if(distance(m,{x:cx,y:p.y},{x:p.ex,y:p.ey})<12)drag='tilt';
    else return;
    previous={...pose};startY=m.y;e.preventDefault();canvas.setPointerCapture(e.pointerId);
    note.textContent='Adjusting '+drag+'; release to save.';
  }
  function move(e){if(!drag)return;e.preventDefault();const m=mouse(e),p=points();
    if(drag==='height')pose={...pose,heightMeters:Math.max(margin,Math.min(data.roomHeight-margin,previous.heightMeters+(startY-m.y)/scale))};
    else pose={...pose,pitch:Math.max(-60,Math.min(60,Math.atan2(p.y-m.y,m.x-cx)*180/Math.PI))};
    draw();
  }
  function up(e){if(!drag)return;move(e);drag=null;if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);setStateValue('pose',{...pose});note.textContent='Camera pose saved.';}
  function cancel(){if(drag){drag=null;pose={...previous};draw();note.textContent='Change cancelled.';}}
  const events=new AbortController();
  for(const [name,fn] of Object.entries({pointerdown:down,pointermove:move,pointerup:up,pointercancel:cancel,lostpointercapture:cancel}))canvas.addEventListener(name,fn,{signal:events.signal});
  const resize=new ResizeObserver(draw);resize.observe(canvas);draw();
  return()=>{events.abort();resize.disconnect();};
}
"""

picker = components.component("detail_camera_vertical_pose",html='<canvas width="760" height="300" aria-label="Drag camera height and tilt lines"></canvas><p role="status">Drag the blue vertical line to adjust height. Drag the orange line to adjust tilt. Release to save.</p>',
    css='canvas{display:block;width:100%;max-width:760px;height:auto;touch-action:none;border-radius:8px;cursor:grab}p{font:14px sans-serif;color:var(--st-text-color);margin:6px 0}',js=JS)


def render(spec,key,setup,*,disabled=False,on_save=None):
    plan,_,_ = geometry(spec)
    cache = "studio-draft-detail-pose-"+key
    base = validate(spec,setup)
    if on_save is None:
        base = validate(spec,{**base,**st.session_state.get(cache,{})})
    component_key = "detail-pose-"+input_hash(key)[:20]
    def save(pose):
        if not isinstance(pose,dict):
            raise ValueError("Choose a valid camera height and tilt.")
        normalized = validate(spec,{**base,**pose})
        if on_save is not None:
            on_save(normalized)
        st.session_state[cache] = {"heightMeters":normalized["heightMeters"],"pitch":normalized["pitch"]}
    def changed():
        try:
            save(st.session_state.get(component_key,{}).get("pose",{}))
        except (OSError,ValueError,RuntimeError) as exc:
            st.error("Camera pose could not be saved: "+str(exc))
    st.markdown("**Camera height & tilt · side view**")
    picker(key=component_key,data={"roomHeight":plan.room_height_m,"pose":{"heightMeters":base["heightMeters"],"pitch":base["pitch"]},"disabled":disabled},
        default={"pose":{"heightMeters":base["heightMeters"],"pitch":base["pitch"]}},on_pose_change=changed)
    def reset():
        try:
            save({"heightMeters":plan.room_height_m/2,"pitch":0})
        except (OSError,ValueError,RuntimeError) as exc:
            st.error("Camera pose could not be reset: "+str(exc))
    st.button("Reset camera pose",key=component_key+"-reset",disabled=disabled,
        help="Tilt 0° and height at the room's vertical center. Floor-plan position, yaw and FOV stay the same.",
        on_click=reset)
    return base if on_save is not None else validate(spec,{**base,**st.session_state.get(cache,{})})
