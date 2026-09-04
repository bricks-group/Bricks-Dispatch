"""Compile path-confined 3D tour from equirect stations."""
from __future__ import annotations
import json
from html import escape as html_escape
from uuid import uuid4

class TourBuildError(RuntimeError):
    pass


def normalize_tag(raw, *, station_index=None, mint_id=False):
    """Amenity/feature tag. Empty label → None (never invent amenities)."""
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label") or raw.get("title") or "").strip()
    if not label:
        return None
    note = str(raw.get("note") or raw.get("description") or "").strip()
    try:
        yaw = float(raw["yaw"]) if raw.get("yaw") is not None and raw.get("yaw") != "" else 0.0
    except (TypeError, ValueError):
        yaw = 0.0
    try:
        pitch = float(raw["pitch"]) if raw.get("pitch") is not None and raw.get("pitch") != "" else 0.0
    except (TypeError, ValueError):
        pitch = 0.0
    while yaw > 180:
        yaw -= 360
    while yaw < -180:
        yaw += 360
    pitch = max(-80.0, min(80.0, pitch))
    tid = str(raw.get("id") or "").strip()
    if not tid or mint_id:
        tid = "tag-" + uuid4().hex[:10]
    idx = raw.get("station_index") if raw.get("station_index") is not None else station_index
    out = {
        "id": tid[:40],
        "label": label[:80],
        "yaw": round(yaw, 2),
        "pitch": round(pitch, 2),
    }
    if note:
        out["note"] = note[:240]
    if idx is not None:
        try:
            out["station_index"] = int(idx)
        except (TypeError, ValueError):
            pass
    return out


def normalize_tags(raw, *, station_index=None):
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for item in raw:
        tag = normalize_tag(item, station_index=station_index)
        if not tag or tag["id"] in seen:
            continue
        seen.add(tag["id"])
        out.append(tag)
    return out


def _scenes(stations):
    scenes = []
    for i, st in enumerate(stations):
        idx = st.get("station_index", i)
        scenes.append({
            "id": st["id"],
            "title": st.get("label") or st.get("room") or ("Station %s" % i),
            "room": st.get("room") or "Room",
            "panorama": st.get("file") or ("scenes/%s.jpg" % i),
            "heading": st.get("heading"),
            "next": stations[i + 1]["id"] if i + 1 < len(stations) else None,
            "prev": stations[i - 1]["id"] if i > 0 else None,
            "station_index": idx,
            "tags": normalize_tags(st.get("tags"), station_index=idx),
        })
    return scenes


def build_tour_json(stations):
    return json.dumps({"version": 2, "kind": "equirect-walkthrough", "scenes": _scenes(stations)}, indent=2)


_TOUR_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__ — __ADDRESS__</title>
<style>
html,body{margin:0;height:100%;background:#0c0a09;color:#fafaf9;font-family:Georgia,serif}
body{display:flex;flex-direction:column}
header{display:flex;justify-content:space-between;align-items:baseline;padding:12px 16px;border-bottom:1px solid #292524;flex-shrink:0}
.kicker{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.16em;color:#a8a29e}
h1{margin:4px 0 0;font-size:18px;font-weight:500}
.stage{flex:1;min-height:0;position:relative;background:#000;touch-action:none;cursor:grab;overflow:hidden}
.stage:active{cursor:grabbing}
canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
.fb{position:absolute;inset:0;overflow:hidden}
.strip{position:relative;height:100%;width:100%;overflow:hidden}
.strip img{position:absolute;top:0;left:0;height:100%;width:auto;max-width:none;display:block;user-select:none;-webkit-user-drag:none}
.hs{position:absolute;left:50%;top:58%;transform:translate(-50%,-50%) scale(.92);z-index:4;border:0;padding:0;background:transparent;cursor:pointer;display:none;flex-direction:column;align-items:center;gap:6px;filter:drop-shadow(0 8px 18px rgba(0,0,0,.45));transition:opacity .15s,transform .15s;opacity:0;pointer-events:none;-webkit-tap-highlight-color:transparent}
.hs.on{display:flex;opacity:1;pointer-events:auto;transform:translate(-50%,-50%) scale(1)}
.hs:focus-visible{outline:2px solid #fafaf9;outline-offset:4px}
.hs-disc{width:52px;height:52px;border-radius:50%;background:rgba(250,250,249,.92);border:2px solid rgba(255,255,255,.95);display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 1px rgba(139,58,42,.35)}
.hs-disc svg{width:22px;height:22px;display:block}
.hs-lab{font:500 12px/1.2 Georgia,serif;color:#fafaf9;background:rgba(12,10,9,.72);padding:5px 10px;border-radius:2px;white-space:nowrap;max-width:160px;overflow:hidden;text-overflow:ellipsis;letter-spacing:.02em}
.hs.pulse .hs-disc{animation:hsPulse 1.8s ease-in-out infinite}
@keyframes hsPulse{0%,100%{box-shadow:0 0 0 0 rgba(250,250,249,.35)}50%{box-shadow:0 0 0 10px rgba(250,250,249,0)}}
.tg{position:absolute;z-index:5;transform:translate(-50%,-50%);border:0;padding:0;background:transparent;cursor:pointer;display:none;flex-direction:column;align-items:center;gap:4px;filter:drop-shadow(0 6px 14px rgba(0,0,0,.45));opacity:0;pointer-events:none;-webkit-tap-highlight-color:transparent}
.tg.on{display:flex;opacity:1;pointer-events:auto}
.tg-dot{width:18px;height:18px;border-radius:50%;background:#8B3A2A;border:2px solid #fafaf9;box-shadow:0 0 0 1px rgba(12,10,9,.35)}
.tg-lab{font:500 11px/1.2 Georgia,serif;color:#fafaf9;background:rgba(12,10,9,.78);padding:3px 8px;border-radius:2px;white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis}
.callout{position:absolute;left:50%;bottom:16px;transform:translateX(-50%);z-index:6;min-width:180px;max-width:min(320px,calc(100% - 24px));background:rgba(12,10,9,.92);border:1px solid #44403c;border-radius:2px;padding:12px 14px;display:none}
.callout.on{display:block}
.call-k{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.16em;color:#a8a29e;text-transform:uppercase}
.call-lab{margin:6px 0 0;font-size:16px}
.call-note{margin:6px 0 0;font-size:13px;color:#d6d3d1}
.call-x{margin-top:10px;border:1px solid #44403c;background:transparent;color:#fafaf9;font:500 12px/1 Georgia,serif;padding:6px 10px;cursor:pointer;border-radius:2px}
.bar{flex-shrink:0;display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 16px}
.cap{font-size:15px}
.nav{display:flex;gap:8px}
.btn,.dot{border:1px solid #44403c;padding:8px 14px;cursor:pointer;font:500 13px/1 Georgia,serif;background:transparent;color:#fafaf9;border-radius:2px}
.btn.go{border-color:#8B3A2A;background:#8B3A2A}
.btn.off{opacity:.35;cursor:default}
.dots{display:flex;gap:6px;padding:0 16px 8px;overflow-x:auto;flex-shrink:0}
.dot{padding:6px 10px;white-space:nowrap;font-size:12px}
.dot.on{border-color:#fafaf9}
.hint{font-family:ui-monospace,monospace;font-size:11px;color:#a8a29e;padding:4px 16px 12px;flex-shrink:0}
</style></head><body>
<header><div><div class="kicker">Bricks · 3D tour</div><h1>__TITLE__</h1></div><div class="kicker">__ADDRESS__</div></header>
<div class="stage" id="stage">
<canvas id="view"></canvas>
<div id="fb" class="fb" hidden><div class="strip"><img id="fbimg" alt="360 panorama"/><img id="fbimg2" alt="" aria-hidden="true"/></div></div>
<button type="button" class="hs pulse" id="hs" aria-label="Go to next station" hidden>
<span class="hs-disc" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M12 4v12M6 12l6 6 6-6" stroke="#8B3A2A" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
<span class="hs-lab" id="hslab">Next station</span>
</button>
<div id="taglayer"></div>
<div id="callout" class="callout" hidden>
<div class="call-k">Feature</div>
<div class="call-lab" id="callab"></div>
<div class="call-note" id="callnote"></div>
<button type="button" class="call-x" id="callx">Close</button>
</div>
</div>
<div class="bar"><div class="cap" id="cap"></div><div class="nav"><button type="button" class="btn" id="prev">Previous</button><button type="button" class="btn go" id="next">Next station</button></div></div>
<div class="dots" id="dots"></div>
<div class="hint">Path-confined 360 stations · drag to look · tap a feature tag for the amenity · tap the hotspot or ←/→ for the next station</div>
<script>
(function(){
var S=__SCENES__,i=0,yaw=0,pitch=0,drag=0,lx=0,ly=0,texs=[],off=0,mode="gl",fw=0,gl=null,moved=0;
var cv=document.getElementById("view");
var fb=document.getElementById("fb");
var img=document.getElementById("fbimg");
var img2=document.getElementById("fbimg2");
var cap=document.getElementById("cap");
var prev=document.getElementById("prev");
var next=document.getElementById("next");
var dots=document.getElementById("dots");
var stage=document.getElementById("stage");
var hs=document.getElementById("hs");
var hslab=document.getElementById("hslab");
var taglayer=document.getElementById("taglayer");
var callout=document.getElementById("callout");
var callab=document.getElementById("callab");
var callnote=document.getElementById("callnote");
var callx=document.getElementById("callx");

var HALF=80*Math.PI/360;
function heading(s){var h=s&&s.heading;if(h==null||h==="")return 0;var n=Number(h);return isFinite(n)?n*Math.PI/180:0}
function wrap(a){while(a>Math.PI)a-=2*Math.PI;while(a<-Math.PI)a+=2*Math.PI;return a}
function nextIdx(){return i<S.length-1?i+1:-1}
function hotYaw(){return heading(S[i])}
function fallback(){
  mode="fb";cv.hidden=true;fb.hidden=false;setFb();placeHs();
}
function setFb(){
  var src=S[i].panorama;
  img.alt=S[i].title||S[i].room||"360 panorama";
  img.src=src;img2.src=src;off=0;
  function place(){
    var h=fb.clientHeight||1,nh=img.naturalHeight||1;
    fw=img.naturalWidth*(h/nh);
    img.style.transform="translateX("+(-off)+"px)";
    img2.style.transform="translateX("+(-off+fw)+"px)";
    placeHs();
  }
  img.onload=place;place();
}
function panFb(dx){
  if(!fw){var h=fb.clientHeight||1,nh=img.naturalHeight||1;fw=img.naturalWidth*(h/nh)}
  if(!fw)return;
  off=((off-dx)%fw+fw)%fw;
  img.style.transform="translateX("+(-off)+"px)";
  img2.style.transform="translateX("+(-off+fw)+"px)";
  placeHs();
}
function bind(){if(gl&&texs[i])gl.bindTexture(gl.TEXTURE_2D,texs[i])}
function go(n){
  if(n<0||n>=S.length)return;
  i=n;yaw=heading(S[i]);pitch=0;
  cap.textContent=S[i].title||S[i].room||("Station "+i);
  prev.className="btn"+(i?"":" off");prev.disabled=!i;
  next.className="btn go"+(i<S.length-1?"":" off");next.disabled=i>=S.length-1;
  var ds=dots.children;
  for(var k=0;k<ds.length;k++)ds[k].className="dot"+(k===i?" on":"");
  var ni=nextIdx();
  if(ni<0){hs.hidden=true;hs.classList.remove("on")}
  else{
    hs.hidden=false;
    var lab=(S[ni].title||S[ni].room||("Station "+ni));
    hslab.textContent=lab;
    hs.setAttribute("aria-label","Go to next station: "+lab);
  }
  hideCall();
  if(mode==="gl")bind();else setFb();
  placeHs();
}
function tagsOf(){return (S[i]&&S[i].tags)||[]}
function tagYaw(t){var n=Number(t&&t.yaw);return isFinite(n)?n*Math.PI/180:0}
function tagPitch(t){var n=Number(t&&t.pitch);return isFinite(n)?n*Math.PI/180:0}
function project(hy,hp){
  var w=stage.clientWidth||1,h=stage.clientHeight||1,nx,ny,vis=false;
  if(mode==="gl"){
    var dy=wrap(hy-yaw),dp=hp-pitch;
    var th=Math.tan(HALF),aspect=w/h;
    var cz=Math.cos(dp)*Math.cos(dy);
    if(cz>0.12){
      nx=(Math.cos(dp)*Math.sin(dy))/(cz*th*aspect);
      ny=-(Math.sin(dp))/(cz*th);
      if(nx>-1.15&&nx<1.15&&ny>-1.25&&ny<1.25)vis=true;
    }
  }else{
    if(!fw){var fh=fb.clientHeight||1,nh=img.naturalHeight||1;fw=img.naturalWidth*(fh/nh)}
    if(fw){
      var hx=((hy/(2*Math.PI))*fw+fw)%fw;
      var cx=(off+w/2+fw)%fw;
      var dx=((hx-cx+fw/2)%fw)-fw/2;
      nx=dx/(w/2);
      ny=hp?-(hp)/1.2:0.28;
      if(Math.abs(nx)<1.2&&Math.abs(ny)<1.25)vis=true;
    }
  }
  if(!vis)return null;
  return {x:(nx*0.5+0.5)*w,y:(ny*0.5+0.5)*h};
}
function hideCall(){callout.hidden=true;callout.classList.remove("on")}
function showCall(t){
  callab.textContent=t.label||"Feature";
  callnote.textContent=t.note||"";
  callnote.hidden=!t.note;
  callout.hidden=false;callout.classList.add("on");
}
function syncTags(){
  var list=tagsOf();
  var existing=taglayer.querySelectorAll(".tg");
  if(existing.length!==list.length){
    taglayer.innerHTML="";
    list.forEach(function(t,n){
      var b=document.createElement("button");
      b.type="button";b.className="tg";b.dataset.n=String(n);
      b.setAttribute("aria-label",t.label||"Feature");
      b.innerHTML='<span class="tg-dot" aria-hidden="true"></span><span class="tg-lab"></span>';
      b.querySelector(".tg-lab").textContent=t.label||"Feature";
      b.addEventListener("pointerdown",function(e){e.stopPropagation()});
      b.onclick=function(e){e.preventDefault();e.stopPropagation();showCall(t)};
      taglayer.appendChild(b);
    });
  }else{
    list.forEach(function(t,n){
      var b=existing[n];
      b.setAttribute("aria-label",t.label||"Feature");
      var lab=b.querySelector(".tg-lab");
      if(lab)lab.textContent=t.label||"Feature";
      b.onclick=function(e){e.preventDefault();e.stopPropagation();showCall(t)};
    });
  }
}
function placeTags(){
  syncTags();
  var list=tagsOf();
  var nodes=taglayer.querySelectorAll(".tg");
  for(var n=0;n<nodes.length;n++){
    var t=list[n],pt=t?project(tagYaw(t),tagPitch(t)):null;
    if(!pt){nodes[n].classList.remove("on");continue}
    nodes[n].style.left=pt.x+"px";nodes[n].style.top=pt.y+"px";
    nodes[n].classList.add("on");
  }
}
function placeHs(){
  var ni=nextIdx();
  if(ni<0){hs.classList.remove("on")}
  else{
    var pt=project(hotYaw(),-0.22);
    if(!pt){hs.classList.remove("on")}
    else{hs.style.left=pt.x+"px";hs.style.top=pt.y+"px";hs.classList.add("on")}
  }
  placeTags();
}
prev.onclick=function(){if(i>0)go(i-1)};
next.onclick=function(){if(i<S.length-1)go(i+1)};
hs.addEventListener("pointerdown",function(e){e.stopPropagation()});
hs.onclick=function(e){e.preventDefault();e.stopPropagation();if(i<S.length-1)go(i+1)};
callout.addEventListener("pointerdown",function(e){e.stopPropagation()});
callx.onclick=function(e){e.preventDefault();e.stopPropagation();hideCall()};
document.addEventListener("keydown",function(e){
  if(e.target&&(e.target.tagName==="INPUT"||e.target.tagName==="TEXTAREA"))return;
  if(e.key==="ArrowRight"||e.key==="."){e.preventDefault();if(i<S.length-1)go(i+1)}
  else if(e.key==="ArrowLeft"||e.key===","){e.preventDefault();if(i>0)go(i-1)}
});
S.forEach(function(s,k){
  var b=document.createElement("button");
  b.type="button";b.className="dot"+(k?"":" on");
  b.textContent=s.title||s.room||("Station "+k);
  b.onclick=function(){go(k)};
  dots.appendChild(b);
});
try{
  gl=cv.getContext("webgl",{alpha:false,antialias:true})||cv.getContext("experimental-webgl",{alpha:false});
  if(!gl)throw new Error("no webgl");
  function sh(type,src){
    var s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);
    if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s)||"shader");
    return s;
  }
  var vs=sh(gl.VERTEX_SHADER,"attribute vec2 a;varying vec2 n;void main(){n=a;gl_Position=vec4(a,0.0,1.0);}");
  var fs=sh(gl.FRAGMENT_SHADER,[
    "precision mediump float;varying vec2 n;uniform sampler2D t;uniform vec2 yp,fa;",
    "void main(){",
    "vec3 d=normalize(vec3(n.x*fa.y*fa.x,n.y*fa.x,-1.0));",
    "float cp=cos(yp.y),sp=sin(yp.y),cy=cos(yp.x),sy=sin(yp.x);",
    "vec3 p=vec3(d.x,d.y*cp-d.z*sp,d.y*sp+d.z*cp);",
    "vec3 r=vec3(p.x*cy-p.z*sy,p.y,p.x*sy+p.z*cy);",
    "float lon=atan(r.x,-r.z),lat=asin(clamp(r.y,-1.0,1.0));",
    "gl_FragColor=texture2D(t,vec2(fract(lon*0.159154943+0.5),clamp(lat*0.318309886+0.5,0.0,1.0)));",
    "}"
  ].join(""));
  var pr=gl.createProgram();gl.attachShader(pr,vs);gl.attachShader(pr,fs);gl.linkProgram(pr);
  if(!gl.getProgramParameter(pr,gl.LINK_STATUS))throw new Error("webgl program");
  gl.useProgram(pr);
  var buf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buf);
  gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
  var loc=gl.getAttribLocation(pr,"a");gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
  var uyp=gl.getUniformLocation(pr,"yp"),ufa=gl.getUniformLocation(pr,"fa");
  gl.uniform1i(gl.getUniformLocation(pr,"t"),0);
  function load(k){
    var im=new Image();
    im.onload=function(){
      var tx=gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D,tx);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,1);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,im);
      texs[k]=tx;if(k===i)bind();
    };
    im.onerror=function(){if(k===i)fallback()};
    im.src=S[k].panorama;
  }
  S.forEach(function(_,k){load(k)});
  function size(){
    var d=Math.min(window.devicePixelRatio||1,2);
    var w=cv.clientWidth,h=cv.clientHeight;
    if(cv.width!==(w*d|0)||cv.height!==(h*d|0)){cv.width=w*d;cv.height=h*d;gl.viewport(0,0,cv.width,cv.height)}
    gl.uniform2f(ufa,Math.tan(HALF),(w||1)/(h||1));
  }
  function draw(){
    if(mode!=="gl")return;
    size();gl.uniform2f(uyp,yaw,pitch);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    placeHs();
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
}catch(err){fallback()}
stage.addEventListener("pointerdown",function(e){drag=1;moved=0;lx=e.clientX;ly=e.clientY;try{stage.setPointerCapture(e.pointerId)}catch(x){}});
stage.addEventListener("pointerup",function(){drag=0});
stage.addEventListener("pointercancel",function(){drag=0});
stage.addEventListener("pointermove",function(e){
  if(!drag)return;
  var dx=e.clientX-lx,dy=e.clientY-ly;lx=e.clientX;ly=e.clientY;
  moved+=Math.abs(dx)+Math.abs(dy);
  if(mode==="gl"){yaw+=dx*0.005;pitch-=dy*0.005;pitch=Math.max(-1.2,Math.min(1.2,pitch));placeHs()}
  else panFb(dx);
});
stage.addEventListener("contextmenu",function(e){e.preventDefault()});
window.addEventListener("resize",function(){placeHs()});
go(0);
})();
</script>
</body></html>
"""


def build_tour_html(stations, *, address="", title="3D tour"):
    if not stations:
        raise TourBuildError("No equirect stations to compile")
    addr = html_escape(address or "Property")
    ttl = html_escape(title or "3D tour")
    scenes = json.dumps(_scenes(stations), separators=(",", ":"))
    return (
        _TOUR_HTML
        .replace("__TITLE__", ttl)
        .replace("__ADDRESS__", addr)
        .replace("__SCENES__", scenes)
    )
