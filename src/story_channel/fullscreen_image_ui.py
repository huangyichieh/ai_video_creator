"""Client-side full-resolution image viewer that can open above an existing dialog."""
import mimetypes
from pathlib import Path
import base64
from io import BytesIO

import streamlit.components.v2 as components
import streamlit as st
from PIL import Image,ImageOps


JS=r"""
export default function({parentElement,data}) {
 const launch=parentElement.querySelector('.launch'),viewer=parentElement.querySelector('dialog');
 const stage=parentElement.querySelector('.stage'),image=parentElement.querySelector('.full-image');
 const thumbnail=parentElement.querySelector('.thumbnail'),hint=parentElement.querySelector('.hint');
 if(data.thumbnail){thumbnail.src=data.thumbnail;thumbnail.alt=data.title;
  launch.style.width=data.thumbnailWidth+'px';hint.textContent='Click to open original';
 }else{thumbnail.hidden=true;hint.textContent='Open original';}
 const closeButton=parentElement.querySelector('.close'),title=parentElement.querySelector('.title');
 const note=parentElement.querySelector('[role=status]');
 const zoom=parentElement.querySelector('.zoom');
 const events=new AbortController(),opts={signal:events.signal};
 function close(){
  if(viewer.open)viewer.close();
  image.removeAttribute('src');
 }
 launch.addEventListener('click',()=>{
  stage.classList.remove('actual-size');zoom.textContent='100%';
  title.textContent=data.title;image.alt=data.title;note.textContent='Loading image...';
  image.src=data.url;viewer.showModal();
 },opts);
 image.addEventListener('load',()=>{note.textContent='';},opts);
 image.addEventListener('error',()=>{note.textContent='Could not load the full-size image.';},opts);
 closeButton.addEventListener('click',close,opts);
 viewer.addEventListener('cancel',e=>{e.preventDefault();close();},opts);
 stage.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}},opts);
 zoom.addEventListener('click',()=>{const actual=stage.classList.toggle('actual-size');zoom.textContent=actual?'Fit':'100%';},opts);
 return ()=>{events.abort();close();};
}
"""

viewer=components.component("scene_image_fullscreen",js=JS,
    html='<button class="launch" type="button" aria-label="Open original image dialog"><img class="thumbnail" /><span class="hint"></span></button>'
         '<dialog aria-label="Full-size scene image"><div class="stage"><div class="toolbar">'
         '<span class="title"></span><button class="zoom" type="button">100%</button><button class="close" type="button">Close · Esc</button></div>'
         '<p role="status"></p><div class="canvas"><img class="full-image" /></div></div></dialog>',
    css='\n.launch{font:13px sans-serif;color:var(--st-text-color);background:transparent;border:1px solid #777;'
        'border-radius:6px;padding:3px;cursor:zoom-in;max-width:100%}'
        '\n.thumbnail{display:block;width:100%;max-height:200px;object-fit:contain}.thumbnail[hidden]{display:none}'
        '.hint{display:block;font:11px sans-serif;margin-top:3px}.launch:focus-visible{outline:2px solid #ff4b4b}'
        'dialog{position:fixed;inset:0;margin:auto;width:min(90vw,1100px);height:min(88vh,900px);max-width:90vw;max-height:88vh;'
        'padding:0;border:1px solid #666;border-radius:10px;background:#111;color:#fff;box-shadow:0 12px 50px #000a}'
        'dialog::backdrop{background:#0009}'
        '.stage{box-sizing:border-box;width:100%;height:100%;display:flex;flex-direction:column;padding:12px;gap:8px;background:#111}'
        '.toolbar{display:flex;align-items:center;gap:12px;font:14px sans-serif}.title{flex:1}'
        '.close,.zoom{background:#333;color:#fff;border:1px solid #777;padding:7px 12px;border-radius:6px;cursor:pointer}'
        '.canvas{display:flex;overflow:auto;flex:1;min-height:0;min-width:0}'
        '.full-image{display:block;flex:none;width:auto;height:auto;max-width:100%;max-height:100%;object-fit:contain;margin:auto}'
        '.actual-size .full-image{max-width:none;max-height:none;margin:0}p{font:14px sans-serif;margin:0}')


@st.cache_data(show_spinner=False,max_entries=96)
def thumbnail_data(path,stamp,width):
    with Image.open(path) as source:
        image=ImageOps.exif_transpose(source)
        image.thumbnail((width,round(width*.75)),Image.Resampling.LANCZOS)
        buffer=BytesIO()
        image.save(buffer,format="PNG")
    return "data:image/png;base64,"+base64.b64encode(buffer.getvalue()).decode("ascii")


def fullscreen_image(path,*,key,title,thumbnail_width=None):
    from streamlit.runtime import get_instance
    path=Path(path)
    mime=mimetypes.guess_type(path.name)[0] or "image/png"
    url=get_instance().media_file_mgr.add(str(path),mime,key)
    thumbnail=None
    if thumbnail_width:
        stamp=path.stat()
        thumbnail=thumbnail_data(str(path),(stamp.st_mtime_ns,stamp.st_size),thumbnail_width)
    viewer(key=key,data={"url":url,"title":title,"thumbnail":thumbnail,"thumbnailWidth":thumbnail_width})
