"""A draggable edit window synchronized with validated clip-relative time fields."""
import streamlit as st
import streamlit.components.v2 as components

from .scene_video_region import edit_interval,format_timecode,timecode


JS = r"""
export default function({parentElement,data,setStateValue}) {
 const track=parentElement.querySelector('.timeline'),windowEl=parentElement.querySelector('.window');
 const startHandle=parentElement.querySelector('.start'),endHandle=parentElement.querySelector('.end');
 const label=parentElement.querySelector('.selection'),endLabel=parentElement.querySelector('.duration');
 const video=parentElement.querySelector('video'),play=parentElement.querySelector('.timeline-play');
 const playhead=parentElement.querySelector('.playhead'),current=parentElement.querySelector('.current-time');
 const duration=data.duration,minGap=Math.min(.001,duration);
 let start=data.start,end=data.end,drag=null;
 const events=new AbortController(),opts={signal:events.signal};
 const stamp=value=>{let n=Math.round(value*1000),h=Math.floor(n/3600000);n%=3600000;
  const m=Math.floor(n/60000);n%=60000;return [h,m,Math.floor(n/1000)].map(v=>String(v).padStart(2,'0')).join(':')+'.'+String(n%1000).padStart(3,'0');};
 const time=e=>{const r=track.getBoundingClientRect();return Math.max(0,Math.min(duration,(e.clientX-r.left)/r.width*duration));};
 let frameId=null,seekFrame=null,pendingSeek=null;
 const clamp=t=>Math.max(start,Math.min(end,t));
 function pause(){if(video)video.pause();if(play)play.textContent='Play';
  if(frameId!==null&&video&&video.cancelVideoFrameCallback)video.cancelVideoFrameCallback(frameId);frameId=null;}
 function position(){
  if(!video)return;
  if(!video.seeking&&(video.currentTime<start-.001||video.currentTime>=end)){
   const t=clamp(video.currentTime);pause();if(Math.abs(video.currentTime-t)>.001)video.currentTime=t;
  }
  playhead.style.left=(clamp(video.currentTime)/duration*100)+'%';
  playhead.setAttribute('aria-valuenow',clamp(video.currentTime));
  playhead.setAttribute('aria-valuetext',stamp(clamp(video.currentTime)));
  current.textContent=stamp(clamp(video.currentTime));
 }
 function frames(){position();if(video&&!video.paused&&video.requestVideoFrameCallback)frameId=video.requestVideoFrameCallback(frames);}
 function seek(t){if(!video)return;pause();
  pendingSeek=clamp(t);
  if(!video.seeking){const destination=pendingSeek;pendingSeek=null;
   if(Math.abs(video.currentTime-destination)>.001)video.currentTime=destination;position();}
 }
 function scrub(t){pendingSeek=clamp(t);if(seekFrame!==null)return;
  seekFrame=requestAnimationFrame(()=>{seekFrame=null;if(pendingSeek!==null)seek(pendingSeek);});}
 function draw(){
  windowEl.style.left=(start/duration*100)+'%';windowEl.style.width=((end-start)/duration*100)+'%';
  startHandle.style.left=(start/duration*100)+'%';endHandle.style.left=(end/duration*100)+'%';
  label.textContent=stamp(start)+' ~ '+stamp(end);endLabel.textContent=stamp(duration);
  for(const [handle,value,low,high] of [[startHandle,start,0,end-minGap],[endHandle,end,start+minGap,duration]]){
   handle.setAttribute('aria-valuemin',low);handle.setAttribute('aria-valuemax',high);
   handle.setAttribute('aria-valuenow',value);handle.setAttribute('aria-valuetext',stamp(value));
  }
  if(playhead){playhead.setAttribute('aria-valuemin',start);playhead.setAttribute('aria-valuemax',end);}
  position();
 }
 function emit(){setStateValue('range',{start,end,time:video?video.currentTime:start});}
 function move(e){
  if(!drag)return;
  const t=time(e);
  if(drag.kind==='seek'){scrub(t);return;}
  if(!drag.moved&&Math.abs(e.clientX-drag.x)<4)return;
  drag.moved=true;
  if(drag.kind==='start')start=Math.max(0,Math.min(end-minGap,t));
  else if(drag.kind==='end')end=Math.min(duration,Math.max(start+minGap,t));
  else {const length=drag.end-drag.start;start=Math.max(0,Math.min(duration-length,drag.start+t-drag.time));end=start+length;}
  start=Math.round(start*1000)/1000;end=Math.round(end*1000)/1000;draw();
 }
 for(const [element,kind] of [[startHandle,'start'],[endHandle,'end'],[windowEl,'window']]){
  element.addEventListener('pointerdown',e=>{
   if(e.button!==0)return;e.preventDefault();drag={kind,start,end,time:time(e),x:e.clientX,moved:false};
   pause();
   track.setPointerCapture(e.pointerId);
  },opts);
 }
 track.addEventListener('pointermove',move,opts);
 track.addEventListener('pointerup',e=>{if(!drag)return;
  let changed=false;
  if(drag.kind==='seek'){if(seekFrame!==null)cancelAnimationFrame(seekFrame);seekFrame=null;seek(time(e));}
  else {move(e);changed=drag.moved;if(!changed)seek(time(e));}
  drag=null;
  if(track.hasPointerCapture(e.pointerId))track.releasePointerCapture(e.pointerId);
  if(changed)emit();},opts);
 track.addEventListener('pointercancel',()=>{if(drag){start=drag.start;end=drag.end;drag=null;draw();}},opts);
 for(const [handle,kind] of [[startHandle,'start'],[endHandle,'end']]){
  handle.addEventListener('keydown',e=>{
   if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();
   const delta=(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?1:.1);
   if(kind==='start')start=Math.max(0,Math.min(end-minGap,e.key==='Home'?0:e.key==='End'?end-minGap:start+delta));
   else end=Math.min(duration,Math.max(start+minGap,e.key==='Home'?start+minGap:e.key==='End'?duration:end+delta));
   start=Math.round(start*1000)/1000;end=Math.round(end*1000)/1000;draw();emit();
  },opts);
 }
 if(video){
  playhead.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();pause();
   drag={kind:'seek',start,end,time:time(e)};track.setPointerCapture(e.pointerId);},opts);
  track.addEventListener('pointerdown',e=>{if(e.target!==track||e.button!==0)return;
   seek(time(e));drag={kind:'seek',start,end,time:time(e)};track.setPointerCapture(e.pointerId);},opts);
  playhead.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
   e.preventDefault();seek(e.key==='Home'?start:e.key==='End'?end:video.currentTime+(e.key==='ArrowLeft'?-.1:.1));},opts);
  video.addEventListener('loadedmetadata',()=>seek(data.time>=start&&data.time<end?data.time:start),opts);
  video.addEventListener('seeked',()=>{if(pendingSeek!==null){const t=pendingSeek;pendingSeek=null;seek(t);}else position();},opts);
  video.addEventListener('timeupdate',position,opts);
  for(const name of ['pause','ended'])video.addEventListener(name,()=>{play.textContent='Play';},opts);
  video.addEventListener('waiting',()=>{if(!video.paused)play.textContent='Buffering...';},opts);
  video.addEventListener('playing',()=>{play.textContent='Pause';},opts);
  play.addEventListener('click',()=>{
   if(!video.paused){pause();return;}
   pendingSeek=null;
   if(video.currentTime<start||video.currentTime>=end)video.currentTime=start;
   play.textContent='Loading...';
   video.play().then(()=>{if(video.paused)return;play.textContent='Pause';frames();
   }).catch(()=>{pause();play.textContent='Retry play';});
  },opts);
 }
 draw();return ()=>{events.abort();pause();if(seekFrame!==null)cancelAnimationFrame(seekFrame);};
}
"""

HTML = ('<div class="selection" aria-live="polite"></div><div class="timeline" aria-label="Video edit time window">'
    '<div class="window"></div><div class="playhead" role="slider" tabindex="0" aria-label="Current video frame"></div>'
    '<div class="start handle" role="slider" tabindex="0" aria-label="Edit start time"></div>'
    '<div class="end handle" role="slider" tabindex="0" aria-label="Edit end time"></div></div>'
    '<div class="limits"><span>00:00:00</span><span class="duration"></span></div>')

CSS = ('.timeline{position:relative;height:38px;margin:12px 12px 4px;background:var(--st-secondary-background-color);border:1px solid #777;border-radius:5px;touch-action:none}'
    '.window{position:absolute;top:0;bottom:0;background:rgba(255,75,75,.25);border-block:2px solid #ff4b4b;box-sizing:border-box;cursor:grab}'
    '.handle{position:absolute;top:-4px;bottom:-4px;width:14px;transform:translateX(-50%);background:#ff4b4b;border:2px solid white;border-radius:4px;box-sizing:border-box;cursor:ew-resize;touch-action:none}'
    '.playhead{position:absolute;top:0;bottom:0;width:16px;transform:translateX(-50%);cursor:ew-resize;touch-action:none}'
    '.playhead:after{content:"";position:absolute;left:7px;top:0;bottom:0;width:2px;background:white;box-shadow:0 0 2px black}'
    '.playhead:before{content:"";position:absolute;left:3px;top:0;border-top:8px solid white;border-left:5px solid transparent;border-right:5px solid transparent}'
    '.handle:focus-visible,.playhead:focus-visible{outline:3px solid #70baff;outline-offset:3px}'
    '.selection,.limits,.current-time{font:13px sans-serif;color:var(--st-text-color)}'
    '.limits{display:flex;justify-content:space-between;margin:0 12px}.selection{text-align:center}')

timeline = components.component("animated_scene_edit_timeline",js=JS,html=HTML,css=CSS)


def accept_range(target,value):
    try:
        first,last=edit_interval(value["start"],value["end"],target["duration"])
    except (KeyError,TypeError,ValueError):
        return
    target["edit_range"]=(first,last)
    target["time"]=max(first,min(last,value.get("time",first)))
    st.session_state["scene-region-start-"+target["token"]]=format_timecode(first)
    st.session_state["scene-region-end-"+target["token"]]=format_timecode(last)


def render(target,*,show_timeline=True):
    duration=target["duration"]
    token=target["token"]
    start_key,end_key="scene-region-start-"+token,"scene-region-end-"+token
    key="scene-region-timeline-"+token
    start,end=target.setdefault("edit_range",(0.,duration))
    st.session_state.setdefault(start_key,format_timecode(start))
    st.session_state.setdefault(end_key,format_timecode(end))

    def dragged():
        value=st.session_state.get(key,{}).get("range") or {}
        accept_range(target,value)

    def typed():
        try:
            target["edit_range"]=edit_interval(timecode(st.session_state[start_key]),timecode(st.session_state[end_key]),duration)
        except ValueError:
            pass  # Keep invalid text visible; confirmation remains disabled.

    if show_timeline:
        st.caption("Drag either edge of the highlighted time window, or drag its middle to move the whole interval. "
        "The full bar represents the entire scene video, from start to end.")
    if show_timeline:
        timeline(key=key,data={"duration":duration,"start":start,"end":end},default={"range":None},on_range_change=dragged)
    first,last=st.columns(2)
    start_text=first.text_input("Edit start (HH:MM:SS)",key=start_key,on_change=typed)
    end_text=last.text_input("Edit end (HH:MM:SS)",key=end_key,on_change=typed)
    st.caption("Times are relative to this scene video. Fractional seconds are supported (00:00:01.500). "
        "Only the selected rectangle during this interval is replaced; the remaining footage and original audio are preserved.")
    try:
        return edit_interval(timecode(start_text),timecode(end_text),duration)
    except ValueError as exc:
        st.warning(str(exc))
        return None
