import json
import shutil
import subprocess

import pytest
from streamlit.testing.v1 import AppTest


def display():
    import streamlit as st
    from story_channel.scene_video_timeline_ui import render
    target=st.session_state.setdefault("target",{"duration":120.,"token":"test"})
    interval=render(target)
    st.button("Confirm",disabled=interval is None)


def test_typed_and_dragged_times_synchronize_and_invalid_times_block_confirm(monkeypatch):
    import streamlit as st
    received=[]
    def component(**kwargs):
        received.append(kwargs["data"])
        def dragged():
            st.session_state[kwargs["key"]]={"range":{"start":60.,"end":110.}}
            kwargs["on_range_change"]()
        st.button("Simulate timeline drag",on_click=dragged)
    monkeypatch.setattr("story_channel.scene_video_timeline_ui.timeline",component)
    app=AppTest.from_function(display).run()
    assert not app.exception
    assert received[-1]=={"duration":120.,"start":0.,"end":120.}
    app.text_input(key="scene-region-start-test").set_value("00:00:20").run()
    assert received[-1]["start"]==20
    app.text_input(key="scene-region-end-test").set_value("00:00:70").run()
    assert app.button[1].disabled and app.warning
    app.text_input(key="scene-region-end-test").set_value("00:03:00").run()
    assert app.button[1].disabled
    app.button[0].click().run()
    assert not app.exception
    assert app.text_input(key="scene-region-start-test").value=="00:01:00"
    assert app.text_input(key="scene-region-end-test").value=="00:01:50"
    assert not app.button[1].disabled
    assert received[-1]=={"duration":120.,"start":60.,"end":110.}
    app.run()
    assert app.text_input(key="scene-region-start-test").value=="00:01:00"


def test_timeline_pointer_resize_move_and_keyboard(tmp_path):
    from story_channel.scene_video_timeline_ui import JS
    node=shutil.which("node")
    if not node:
        pytest.skip("Node is required for timeline pointer checks")
    script=tmp_path/"timeline.mjs"
    script.write_text(JS.replace("export default function(","function mount(")+"""
const assert=(value,message)=>{if(!value)throw Error(message);};
const nodes={};
for(const name of ['.timeline','.window','.start','.end','.selection','.duration'])nodes[name]={
 style:{},events:{},attrs:{},textContent:'',
 addEventListener(name,fn){this.events[name]=fn;},
 setAttribute(name,value){this.attrs[name]=value;},
 getBoundingClientRect(){return {left:0,width:1200};},
 setPointerCapture(){},hasPointerCapture(){return true;},releasePointerCapture(){}
};
const outputs=[];
// Load the exported function without a browser; exercise real event handlers.
const cleanup=mount({parentElement:{querySelector:s=>nodes[s]},data:{duration:120,start:0,end:120},
 setStateValue:(key,value)=>outputs.push(value)});
const event=x=>({button:0,clientX:x,pointerId:1,preventDefault(){}});
nodes['.start'].events.pointerdown(event(0));
nodes['.timeline'].events.pointermove(event(600));
assert(outputs.length===0,'Do not rerun server during drag');
nodes['.timeline'].events.pointerup(event(600));
assert(outputs.at(-1).start===60,'Start maps to clip time');
nodes['.end'].events.pointerdown(event(1200));nodes['.timeline'].events.pointerup(event(1100));
assert(outputs.at(-1).end===110,'End maps to clip time');
nodes['.window'].events.pointerdown(event(800));nodes['.timeline'].events.pointerup(event(700));
assert(outputs.at(-1).start===50&&outputs.at(-1).end===100,'Window moves retaining length');
nodes['.start'].events.pointerdown(event(500));nodes['.timeline'].events.pointerup(event(1200));
assert(outputs.at(-1).start<outputs.at(-1).end,'Handles cannot cross');
nodes['.start'].events.keydown({key:'Home',preventDefault(){}});
assert(outputs.at(-1).start===0,'Keyboard moves to clip start');
nodes['.end'].events.keydown({key:'End',preventDefault(){}});
assert(outputs.at(-1).end===120,'Keyboard moves to clip end');
cleanup();
console.log(JSON.stringify(outputs));
""",encoding="utf-8")
    result=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)[-1]=={"start":0,"end":120,"time":0}


def test_timeline_video_playhead_and_range_limited_playback(tmp_path):
    from story_channel.scene_video_timeline_ui import JS
    node=shutil.which("node")
    if not node:
        pytest.skip("Node is required for playback checks")
    script=tmp_path/"playback.mjs"
    script.write_text(JS.replace("export default function(","function mount(")+"""
const assert=(value,message)=>{if(!value)throw Error(message);};
const nodes={};
for(const name of ['.timeline','.window','.start','.end','.selection','.duration','.timeline-play','.playhead','.current-time','video'])nodes[name]={
 style:{},events:{},attrs:{},textContent:'',
 addEventListener(name,fn){(this.events[name]??=[]).push(fn);},
 setAttribute(name,value){this.attrs[name]=value;},
 getBoundingClientRect(){return {left:0,width:1200};},
 setPointerCapture(){},hasPointerCapture(){return true;},releasePointerCapture(){}
};
const fire=(name,event,value={})=>{for(const fn of nodes[name].events[event]||[])fn(value);};
const video=nodes.video;Object.assign(video,{currentTime:0,paused:true,playbackRate:1,
 pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}});
globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);
globalThis.cancelAnimationFrame=id=>clearTimeout(id);
const outputs=[];
const cleanup=mount({parentElement:{querySelector:s=>nodes[s]},data:{duration:120,start:60,end:110,time:0},
 setStateValue:(key,value)=>outputs.push(value)});
fire('video','loadedmetadata');assert(video.currentTime===60,'Start at selected window');
fire('.timeline-play','click');await Promise.resolve();assert(!video.paused,'Play selected window');
video.currentTime=80;fire('video','timeupdate');
assert(nodes['.playhead'].attrs['aria-valuenow']===80,'Playhead follows current frame');
assert(nodes['.current-time'].textContent==='00:01:20.000','Display frame time');
video.currentTime=111;fire('video','timeupdate');
assert(video.paused&&video.currentTime===110,'Stop and clamp at selection end');
fire('.timeline-play','click');await Promise.resolve();
assert(!video.paused&&video.currentTime===60,'Replay starts at selection start');
const event=x=>({button:0,clientX:x,pointerId:1,preventDefault(){}});
fire('.playhead','pointerdown',event(600));fire('.timeline','pointermove',event(900));
await new Promise(resolve=>setTimeout(resolve,5));
assert(video.paused&&video.currentTime===90,'Drag playhead to preview frame');
fire('.timeline','pointerup',event(1200));assert(video.currentTime===110,'Seek cannot leave window');
fire('.playhead','keydown',{key:'Home',preventDefault(){}});assert(video.currentTime===60,'Keyboard seek clamped');
assert(outputs.length===0,'Playback and seeking do not trigger server rerenders');
fire('.window','pointerdown',event(850));fire('.timeline','pointerup',event(851));
assert(video.currentTime===85.1,'Short click inside red window seeks to clicked time');
assert(nodes['.start'].attrs['aria-valuenow']===60&&nodes['.end'].attrs['aria-valuenow']===110,'Click does not move interval');
assert(outputs.length===0,'Short click stays local without a refresh');
fire('.timeline','pointerdown',{...event(400),target:nodes['.timeline']});fire('.timeline','pointerup',event(400));
assert(video.currentTime===60,'Click outside interval clamps to selected start');
fire('.window','pointerdown',event(850));fire('.timeline','pointermove',event(800));fire('.timeline','pointerup',event(800));
assert(outputs.length===1&&outputs[0].start===55&&outputs[0].end===105,'Dragging still moves selection');
cleanup();
const stalled=mount({parentElement:{querySelector:s=>nodes[s]},data:{duration:120,start:60,end:60.03,time:60},setStateValue(){}});
video.currentTime=60;nodes['.timeline-play'].events.click.at(-1)();await Promise.resolve();
// A buffering player has not advanced its media clock: do not stop on wall time.
await new Promise(resolve=>setTimeout(resolve,60));
assert(!video.paused&&video.currentTime===60,'Buffering must not expire the selected interval');
stalled();
""",encoding="utf-8")
    result=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
