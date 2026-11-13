"""Inspect a single panorama in a browser sphere; no derived image files."""
from .image_preview_ui import image_preview
import base64
from io import BytesIO

from PIL import Image, ImageChops
import streamlit as st


def seam_preview(path):
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((1600, 800))
        image = ImageChops.offset(image, image.width // 2, 0)
        data = BytesIO()
        image.save(data, "JPEG", quality=90)
    image_preview(data.getvalue(), caption="Start/end join is at the CENTER. Check that walls, shelves, floor and ceiling continue across it.")


def panorama_viewer(path):
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((2048, 1024))
        data = BytesIO()
        image.save(data, "JPEG", quality=90)
    encoded = base64.b64encode(data.getvalue()).decode("ascii")
    html = r'''<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#17191f;color:#eee;font:14px sans-serif}canvas{width:100%;height:330px;display:block;cursor:grab;touch-action:none}
.controls{display:flex;align-items:center;gap:12px;padding:10px;flex-wrap:wrap}input{flex:1;min-width:150px}button{color:#eee;background:#353842;border:0;border-radius:5px;padding:8px}
</style></head><body><canvas aria-label="360 degree panorama: drag to look around"></canvas>
<div class="controls"><label for="yaw">Rotate 360°</label><input id="yaw" type="range" min="-180" max="180" step="1" value="0"><span id="angle">0°</span><button id="seam">Look at start/end seam</button></div>
<script>
const canvas=document.querySelector('canvas'), slider=document.querySelector('#yaw'), angle=document.querySelector('#angle');
const gl=canvas.getContext('webgl');
if(!gl){canvas.replaceWith(document.createTextNode('360 preview needs WebGL. Use the centered seam preview below.'));}
else{
const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);return s;};
const program=gl.createProgram();
gl.attachShader(program,compile(gl.VERTEX_SHADER,'attribute vec2 p;varying vec2 uv;void main(){uv=p;gl_Position=vec4(p,0.,1.);}'));
gl.attachShader(program,compile(gl.FRAGMENT_SHADER,`precision highp float;varying vec2 uv;uniform sampler2D tex;uniform float yaw,pitch,aspect;
void main(){vec3 d=normalize(vec3(uv.x*aspect*.57735,uv.y*.57735,1.));float a=cos(pitch),b=sin(pitch);d=vec3(d.x,d.y*a+d.z*b,d.z*a-d.y*b);
a=cos(yaw);b=sin(yaw);d=vec3(d.x*a+d.z*b,d.y,d.z*a-d.x*b);vec2 q=vec2(fract(.5+atan(d.x,d.z)/6.2831853),.5-asin(clamp(d.y,-1.,1.))/3.14159265);gl_FragColor=texture2D(tex,q);}`));
gl.linkProgram(program);gl.useProgram(program);const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
const p=gl.getAttribLocation(program,'p');gl.enableVertexAttribArray(p);gl.vertexAttribPointer(p,2,gl.FLOAT,false,0,0);
const texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
let yaw=0,pitch=0,ready=false,drag=null;
function draw(){if(!ready)return;const w=canvas.clientWidth||800;canvas.width=w;canvas.height=330;gl.viewport(0,0,w,330);gl.uniform1f(gl.getUniformLocation(program,'yaw'),yaw*Math.PI/180);gl.uniform1f(gl.getUniformLocation(program,'pitch'),pitch*Math.PI/180);gl.uniform1f(gl.getUniformLocation(program,'aspect'),w/330);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);slider.value=yaw;angle.textContent=Math.round(yaw)+'°';}
const image=new Image();image.onload=()=>{gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,image);ready=true;draw();};image.src='data:image/jpeg;base64,__IMAGE__';
slider.oninput=()=>{yaw=+slider.value;draw();};document.querySelector('#seam').onclick=()=>{yaw=180;pitch=0;draw();};
canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY,yaw,pitch];canvas.setPointerCapture(e.pointerId);};canvas.onpointerup=()=>drag=null;
canvas.onpointermove=e=>{if(!drag)return;yaw=((drag[2]-(e.clientX-drag[0])*.25+540)%360)-180;pitch=Math.max(-80,Math.min(80,drag[3]+(e.clientY-drag[1])*.2));draw();};window.onresize=draw;
}
</script></body></html>'''
    document = html.replace("__IMAGE__", encoded).encode("utf-8")
    st.iframe("data:text/html;charset=utf-8;base64," + base64.b64encode(document).decode("ascii"),
              height=405, alt="Interactive panorama coverage and seam preview")
