import shutil
import subprocess

import pytest

from story_channel.fullscreen_image_ui import JS


def test_original_image_dialog_pixel_zoom_close_and_escape_without_browser_fullscreen(tmp_path):
    node=shutil.which("node")
    if not node:
        pytest.skip("Node is required for viewer interaction checks")
    script=tmp_path/"viewer.mjs"
    script.write_text(JS.replace("export default function(","function mount(")+"""
const assert=(value,message)=>{if(!value)throw Error(message);};
const nodes={};
for(const name of ['.launch','dialog','.stage','.full-image','.thumbnail','.hint','.close','.zoom','.title','[role=status]'])nodes[name]={
 events:{},style:{},open:false,textContent:'',src:'',
 addEventListener(name,fn){this.events[name]=fn;},
 showModal(){this.open=true;},close(){this.open=false;},
 removeAttribute(name){delete this[name];}
};
const classes=new Set();
nodes['.stage'].classList={remove:name=>classes.delete(name),toggle(name){if(classes.has(name)){classes.delete(name);return false;}classes.add(name);return true;}};
nodes['.stage'].requestFullscreen=()=>{throw Error('Must not enter browser fullscreen');};
const cleanup=mount({parentElement:{querySelector:s=>nodes[s]},data:{url:'/media/original.png',title:'Image 1',thumbnail:'/media/small.png',thumbnailWidth:160}});
assert(nodes['.thumbnail'].src==='/media/small.png','Display clickable compact image');
assert(!nodes['.full-image'].src,'Do not load full-size pixels until opened');
nodes['.launch'].events.click();await Promise.resolve();
assert(nodes.dialog.open&&nodes['.full-image'].src==='/media/original.png','Click image to open original above parent dialog');
nodes['.zoom'].events.click();assert(classes.has('actual-size')&&nodes['.zoom'].textContent==='Fit','Show original pixels at 100%');
nodes['.zoom'].events.click();assert(!classes.has('actual-size'),'Return to fit view');
nodes['.close'].events.click();assert(!nodes.dialog.open,'Close restores parent dialog');
nodes['.launch'].events.click();await Promise.resolve();
assert(nodes.dialog.open,'Reopen original image in a dialog');
let stopped=false;
nodes['.stage'].events.keydown({key:'Escape',preventDefault(){},stopPropagation(){stopped=true;}});
assert(stopped&&!nodes.dialog.open,'Escape closes viewer without bubbling into parent dialog');
cleanup();
""",encoding="utf-8")
    result=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
