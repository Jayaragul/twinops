"""A self-contained HTML replay of a finished simulation run.

No server, no external JS, works offline: open the file and watch the run
happen again. The engine stays ignorant of this module entirely — Recorder
only uses the public Signal API already described in the README's
"Extending it" section, so rendering is a consumer of the model, never a
prerequisite for it.
"""

from __future__ import annotations

import json
import webbrowser
from html import escape as esc_html
from pathlib import Path
from typing import Any

from .core import TwinObject
from .objects import Buffer, Sink, Source, Station
from .sim import fmt_time


class Recorder:
    """Listens to every signal in the tree and builds a replay-able event log.

    Attach it *before* creating the SimulationEngine: binding calls setup()
    immediately, and a machine fed by an empty buffer emits its first
    "starved" signal right there. Connecting later would miss it.

    Events are appended in the order the engine fires them, which is already
    non-decreasing in time — no separate sort is needed.
    """

    STATION_SIGNALS = ("cycle_started", "cycle_completed", "blocked", "starved", "failed", "repaired")

    def __init__(self, root: TwinObject) -> None:
        self.root = root
        self.events: list[dict[str, Any]] = []
        self._levels: dict[str, int] = {}
        for obj in root.walk():
            if isinstance(obj, Buffer):
                self._levels[obj.path] = 0
                obj.signal("received").connect(lambda part, o=obj: self._buffer(o, "received", part))
                obj.signal("released").connect(lambda part, o=obj: self._buffer(o, "released", part))
            elif isinstance(obj, Source):
                obj.signal("created").connect(lambda part, o=obj: self._simple(o, "created", part))
            elif isinstance(obj, Sink):
                obj.signal("completed").connect(lambda part, o=obj: self._simple(o, "completed", part))
            elif isinstance(obj, Station):
                for name in self.STATION_SIGNALS:
                    obj.signal(name).connect(lambda *_a, o=obj, n=name: self._simple(o, n))

    def _buffer(self, obj: Buffer, kind: str, part: Any) -> None:
        self._levels[obj.path] += 1 if kind == "received" else -1
        self.events.append({
            "t": obj.engine.now, "path": obj.path, "type": kind,
            "level": self._levels[obj.path], "part": part.id,
        })

    def _simple(self, obj: TwinObject, kind: str, part: Any = None) -> None:
        event: dict[str, Any] = {"t": obj.engine.now, "path": obj.path, "type": kind}
        if part is not None:
            event["part"] = part.id
        self.events.append(event)

    def timeline(self) -> list[dict[str, Any]]:
        return self.events


def export_layout(root: TwinObject) -> dict[str, Any]:
    """The static flow graph the UI lays out once, before replay starts."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for obj in root.walk():
        if isinstance(obj, Buffer):
            nodes.append({
                "path": obj.path, "name": obj.name, "type": obj.type_name,
                "capacity": obj.capacity,
            })
        elif isinstance(obj, (Source, Sink, Station)):
            nodes.append({"path": obj.path, "name": obj.name, "type": obj.type_name})

        for buf in getattr(obj, "outputs", None) or []:
            edges.append({"from": obj.path, "to": buf.path})
        for buf in getattr(obj, "inputs", None) or []:
            edges.append({"from": buf.path, "to": obj.path})
    return {"nodes": nodes, "edges": edges}


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#05070f; --panel:#0d1120; --panel2:#121830;
  --border:rgba(255,255,255,.08); --border-strong:rgba(255,255,255,.18);
  --text:#e7ebf5; --text-dim:#8791ab;
  --accent:#00c2d1; --accent-dim:#0a5d66;
  --idle:#5b6478; --running:#22c55e; --blocked:#f59e0b; --starved:#38bdf8; --down:#ef4444;
  --mono: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:var(--sans);}
body{
  background-image:
    linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px);
  background-size:28px 28px;
}
.app{max-width:1180px;margin:0 auto;padding:28px 20px 60px;}
header{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid var(--border-strong);padding-bottom:16px;margin-bottom:20px;flex-wrap:wrap;gap:10px;}
header .name{font-size:22px;font-weight:600;letter-spacing:.3px;}
header .tag{font-family:var(--mono);font-size:11px;color:var(--accent);letter-spacing:2px;margin-left:10px;}
header .meta{font-family:var(--mono);font-size:12px;color:var(--text-dim);text-align:right;}
header .meta span{margin-left:14px;}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1px;background:var(--border);border:1px solid var(--border);margin-bottom:18px;}
.kpi{background:var(--panel);padding:14px 16px;min-width:0;}
.kpi .label{font-family:var(--mono);font-size:10px;letter-spacing:1.2px;color:var(--text-dim);text-transform:uppercase;line-height:1.4;}
.kpi .value{font-family:var(--mono);font-size:20px;margin-top:4px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.kpi .value.accent{color:var(--accent);}
.kpi .value.warn{color:var(--blocked);}

.legend{display:flex;gap:16px;font-family:var(--mono);font-size:11px;color:var(--text-dim);margin:0 0 14px;flex-wrap:wrap;}
.legend .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;}

.stage{background:var(--panel);border:1px solid var(--border);padding:16px;margin-bottom:18px;overflow-x:auto;}
svg#diagram{display:block;}
.node .card{fill:var(--panel2);stroke:var(--border-strong);stroke-width:1.4;transition:stroke .15s,filter .15s;}
.node text{font-family:var(--sans);fill:var(--text);}
.node .type{font-family:var(--mono);fill:var(--text-dim);font-size:9px;letter-spacing:1px;}
.node .stat{font-family:var(--mono);fill:var(--text-dim);font-size:9px;}
.node.flash .card{filter:drop-shadow(0 0 7px var(--accent));}
.node.flash .shell{filter:drop-shadow(0 0 7px var(--accent));}
.state-dot{transition:fill .15s;}
.buffer .shell{fill:var(--panel2);stroke:var(--border-strong);stroke-width:1.4;}
.buffer .fill{fill:var(--accent-dim);transition:height .2s,y .2s,fill .2s;}
.buffer .fill.full{fill:#7a3b12;}
.edge{stroke:var(--border-strong);stroke-width:1.4;fill:none;marker-end:url(#arrow);}

.transport{display:flex;align-items:center;gap:14px;background:var(--panel);border:1px solid var(--border);padding:12px 16px;margin-bottom:26px;font-family:var(--mono);flex-wrap:wrap;}
.transport button{background:var(--panel2);border:1px solid var(--border-strong);color:var(--text);font-family:var(--mono);font-size:14px;padding:6px 14px;cursor:pointer;border-radius:3px;}
.transport button:hover{border-color:var(--accent);color:var(--accent);}
.transport input[type=range]{flex:1;min-width:160px;accent-color:var(--accent);}
.transport select{background:var(--panel2);color:var(--text);border:1px solid var(--border-strong);font-family:var(--mono);padding:5px 8px;border-radius:3px;}
.transport .clock{min-width:110px;text-align:right;font-variant-numeric:tabular-nums;}

.section-title{font-family:var(--mono);font-size:11px;color:var(--text-dim);letter-spacing:1.5px;text-transform:uppercase;margin:26px 0 8px;}
table.report{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;background:var(--panel);border:1px solid var(--border);}
table.report th,table.report td{padding:8px 14px;text-align:right;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums;}
table.report th:first-child,table.report td:first-child{text-align:left;}
table.report th{color:var(--text-dim);font-weight:500;font-size:10px;text-transform:uppercase;letter-spacing:1px;}
table.report tr.bottleneck td{color:var(--blocked);}
table.report tr.saturated td{color:var(--blocked);}
footer{margin-top:30px;font-family:var(--mono);font-size:11px;color:var(--text-dim);text-align:center;}
</style>
</head>
<body>
<div class="app">
  <header>
    <div><span class="name">__NAME__</span><span class="tag">DIGITAL TWIN REPLAY</span></div>
    <div class="meta"><span>SEED __SEED__</span><span>DURATION __DURATION__</span></div>
  </header>

  <div class="kpis">
    <div class="kpi"><div class="label">Live throughput</div><div class="value accent" id="kpi-throughput">0</div></div>
    <div class="kpi"><div class="label">Live WIP</div><div class="value" id="kpi-wip">0</div></div>
    <div class="kpi"><div class="label">Final throughput</div><div class="value" id="kpi-final-throughput">0</div></div>
    <div class="kpi"><div class="label">Avg lead time</div><div class="value" id="kpi-lead">-</div></div>
    <div class="kpi"><div class="label">Bottleneck</div><div class="value warn" id="kpi-bottleneck">-</div></div>
  </div>

  <div class="legend">
    <span><span class="dot" style="background:var(--running)"></span>running</span>
    <span><span class="dot" style="background:var(--blocked)"></span>blocked</span>
    <span><span class="dot" style="background:var(--starved)"></span>starved</span>
    <span><span class="dot" style="background:var(--down)"></span>down</span>
    <span><span class="dot" style="background:var(--idle)"></span>idle</span>
  </div>

  <div class="stage"><svg id="diagram"></svg></div>

  <div class="transport">
    <button id="restartBtn" title="restart">&#8634;</button>
    <button id="playBtn" title="play / pause">&#9654;</button>
    <input type="range" id="scrubber" min="0" value="0">
    <span class="clock" id="clock">00:00:00</span>
    <select id="speed" title="playback speed"></select>
  </div>

  <div class="section-title">Station report — final</div>
  <table class="report">
    <thead><tr><th>Station</th><th>Processed</th><th>Busy</th><th>Blocked</th><th>Starved</th><th>Down</th><th>Fail</th></tr></thead>
    <tbody id="stations-body"></tbody>
  </table>

  <div class="section-title">Buffer report — final</div>
  <table class="report">
    <thead><tr><th>Buffer</th><th>Avg</th><th>Peak</th><th>Capacity</th><th></th></tr></thead>
    <tbody id="buffers-body"></tbody>
  </table>

  <footer>generated by TwinOps &mdash; twin ui</footer>
</div>

<script>
const DATA = __DATA__;

function fmtTime(s){
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s/86400); s%=86400;
  const h = Math.floor(s/3600); s%=3600;
  const m = Math.floor(s/60); s%=60;
  const pad = n => String(n).padStart(2,'0');
  return (d ? d+'d ' : '') + pad(h)+':'+pad(m)+':'+pad(s);
}
function esc(str){
  return String(str).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function layoutGraph(nodes, edges){
  const outMap = new Map(nodes.map(n => [n.path, []]));
  const inDeg = new Map(nodes.map(n => [n.path, 0]));
  edges.forEach(e => {
    if(!outMap.has(e.from) || !inDeg.has(e.to)) return;
    outMap.get(e.from).push(e.to);
    inDeg.set(e.to, inDeg.get(e.to)+1);
  });
  const depth = new Map(nodes.map(n => [n.path, 0]));
  const indegLeft = new Map(inDeg);
  let queue = nodes.filter(n => inDeg.get(n.path) === 0).map(n => n.path);
  const seen = new Set(queue);
  while(queue.length){
    const p = queue.shift();
    for(const q of (outMap.get(p) || [])){
      depth.set(q, Math.max(depth.get(q), depth.get(p)+1));
      indegLeft.set(q, indegLeft.get(q)-1);
      if(indegLeft.get(q) === 0 && !seen.has(q)){ seen.add(q); queue.push(q); }
    }
  }
  const columns = new Map();
  nodes.forEach(n => {
    const d = depth.get(n.path) || 0;
    if(!columns.has(d)) columns.set(d, []);
    columns.get(d).push(n);
  });
  const COLW = 190, ROWH = 96;
  const maxRows = Math.max(1, ...Array.from(columns.values()).map(c => c.length));
  const pos = new Map();
  columns.forEach((list, d) => {
    const totalH = list.length*ROWH;
    const offsetY = (maxRows*ROWH - totalH)/2;
    list.forEach((n,i) => pos.set(n.path, {x: 40 + d*COLW, y: 40 + offsetY + i*ROWH}));
  });
  const width = 80 + columns.size*COLW;
  const height = 80 + maxRows*ROWH;
  return {pos, width, height};
}

const NS = 'http://www.w3.org/2000/svg';
function el(tag, attrs, parent){
  const e = document.createElementNS(NS, tag);
  for(const k in attrs) e.setAttribute(k, attrs[k]);
  if(parent) parent.appendChild(e);
  return e;
}

const svg = document.getElementById('diagram');
const {pos, width, height} = layoutGraph(DATA.layout.nodes, DATA.layout.edges);
svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
svg.setAttribute('width', width);
svg.setAttribute('height', height);

const defs = el('defs', {}, svg);
const marker = el('marker', {id:'arrow', viewBox:'0 0 10 10', refX:'9', refY:'5', markerWidth:'7', markerHeight:'7', orient:'auto-start-reverse'}, defs);
el('path', {d:'M0,0 L10,5 L0,10 z', fill:'var(--border-strong)'}, marker);

const CARD_W = 118, CARD_H = 64, BUF_W = 46, BUF_H = 96;

const edgeLayer = el('g', {}, svg);
DATA.layout.edges.forEach(e => {
  const a = pos.get(e.from), b = pos.get(e.to);
  if(!a || !b) return;
  const aIsBuffer = (DATA.layout.nodes.find(n=>n.path===e.from)||{}).type === 'Buffer';
  const bIsBuffer = (DATA.layout.nodes.find(n=>n.path===e.to)||{}).type === 'Buffer';
  const aw = aIsBuffer ? BUF_W : CARD_W, ah = aIsBuffer ? BUF_H : CARD_H;
  const bh = bIsBuffer ? BUF_H : CARD_H;
  const x1 = a.x + aw, y1 = a.y + ah/2;
  const x2 = b.x, y2 = b.y + bh/2;
  const mx = (x1+x2)/2;
  el('path', {class:'edge', d:`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}, edgeLayer);
});

const nodeLayer = el('g', {}, svg);
const nodeEls = new Map();

DATA.layout.nodes.forEach(n => {
  const p = pos.get(n.path);
  if(!p) return;
  const isBuffer = n.type === 'Buffer';
  const g = el('g', {class:'node' + (isBuffer?' buffer':''), transform:`translate(${p.x},${p.y})`}, nodeLayer);

  if(isBuffer){
    el('rect', {class:'shell', width:BUF_W, height:BUF_H, rx:4}, g);
    const fill = el('rect', {class:'fill', x:2, width:BUF_W-4, y:BUF_H-2, height:0}, g);
    el('text', {x:BUF_W/2, y:-10, 'text-anchor':'middle', class:'type'}, g).textContent = 'BUFFER';
    el('text', {x:BUF_W/2, y:BUF_H+16, 'text-anchor':'middle', 'font-size':11}, g).textContent = n.name;
    const stat = el('text', {x:BUF_W/2, y:BUF_H+30, 'text-anchor':'middle', class:'stat'}, g);
    nodeEls.set(n.path, {g, fill, stat, capacity:n.capacity});
  } else {
    const card = el('rect', {class:'card', width:CARD_W, height:CARD_H, rx:6}, g);
    const dot = el('circle', {class:'state-dot', cx:14, cy:16, r:4, fill:'var(--idle)'}, g);
    el('text', {x:26, y:20, 'font-size':12, 'font-weight':600}, g).textContent = n.name;
    el('text', {x:12, y:36, class:'type'}, g).textContent = n.type.toUpperCase();
    const stat = el('text', {x:12, y:52, class:'stat'}, g);
    nodeEls.set(n.path, {g, card, dot, stat});
  }
});

const state = {}, levels = {}, counts = {created:0, completed:0};
let idx = 0, simTime = 0;
const events = DATA.events;
const flashUntil = new Map();

function resetState(){
  DATA.layout.nodes.forEach(n => {
    if(n.type === 'Buffer') levels[n.path] = 0;
    else state[n.path] = 'idle';
  });
  counts.created = 0; counts.completed = 0;
  idx = 0;
}

function applyEvent(e){
  if(e.type === 'received' || e.type === 'released'){
    levels[e.path] = e.level;
  } else if(e.type === 'created'){
    counts.created++;
  } else if(e.type === 'completed'){
    counts.completed++;
  } else if(e.type === 'cycle_started'){
    state[e.path] = 'running';
  } else if(e.type === 'blocked'){
    state[e.path] = 'blocked';
  } else if(e.type === 'starved'){
    state[e.path] = 'starved';
  } else if(e.type === 'failed'){
    state[e.path] = 'down';
  } else if(e.type === 'cycle_completed' || e.type === 'repaired'){
    state[e.path] = 'idle';
  }
  flashUntil.set(e.path, performance.now() + 260);
}

function seekTo(t){
  if(t < simTime) resetState();
  simTime = t;
  while(idx < events.length && events[idx].t <= simTime){
    applyEvent(events[idx]); idx++;
  }
}
resetState();

function draw(){
  const now = performance.now();
  for(const [path, els] of nodeEls){
    if('fill' in els){
      const cap = els.capacity || 1;
      const level = levels[path] || 0;
      const frac = Math.min(1, level/cap);
      const h = frac*(BUF_H-4);
      els.fill.setAttribute('y', BUF_H-2-h);
      els.fill.setAttribute('height', h);
      els.fill.classList.toggle('full', level >= cap);
      els.stat.textContent = `${level} / ${cap}`;
    } else {
      const st = state[path] || 'idle';
      els.dot.setAttribute('fill', `var(--${st})`);
      els.card.setAttribute('stroke', st === 'idle' ? 'var(--border-strong)' : `var(--${st})`);
      els.stat.textContent = st.toUpperCase();
    }
    els.g.classList.toggle('flash', (flashUntil.get(path)||0) > now);
  }
  document.getElementById('kpi-throughput').textContent = counts.completed.toLocaleString();
  document.getElementById('kpi-wip').textContent = (counts.created - counts.completed).toLocaleString();
  document.getElementById('clock').textContent = fmtTime(simTime);
  scrubber.value = simTime;
}

const scrubber = document.getElementById('scrubber');
scrubber.max = DATA.meta.duration;
const playBtn = document.getElementById('playBtn');
const speedSel = document.getElementById('speed');
let playing = false, lastFrame = null;

const SPEED_OPTIONS = [10, 30, 60, 120, 300, 600, 1800, 3600];
SPEED_OPTIONS.forEach(v => {
  const o = document.createElement('option'); o.value = v; o.textContent = v+'x'; speedSel.appendChild(o);
});
speedSel.value = SPEED_OPTIONS.reduce((a,b) => {
  const target = Math.max(10, Math.round(DATA.meta.duration/90));
  return Math.abs(b-target) < Math.abs(a-target) ? b : a;
});

function tick(ts){
  if(!playing) return;
  if(lastFrame == null) lastFrame = ts;
  const dtReal = (ts-lastFrame)/1000;
  lastFrame = ts;
  seekTo(Math.min(DATA.meta.duration, simTime + dtReal*Number(speedSel.value)));
  draw();
  if(simTime >= DATA.meta.duration){ playing=false; playBtn.innerHTML='&#9654;'; return; }
  requestAnimationFrame(tick);
}
playBtn.addEventListener('click', () => {
  playing = !playing;
  playBtn.innerHTML = playing ? '&#10074;&#10074;' : '&#9654;';
  lastFrame = null;
  if(playing) requestAnimationFrame(tick);
});
scrubber.addEventListener('input', () => {
  playing = false; playBtn.innerHTML = '&#9654;';
  seekTo(Number(scrubber.value)); draw();
});
document.getElementById('restartBtn').addEventListener('click', () => {
  playing = false; playBtn.innerHTML = '&#9654;';
  seekTo(0); draw();
});
draw();

(function renderReport(){
  const r = DATA.report;
  document.getElementById('kpi-final-throughput').textContent = r.throughput.toLocaleString();
  document.getElementById('kpi-lead').textContent = fmtTime(r.avg_lead_time_s);
  document.getElementById('kpi-bottleneck').textContent = r.bottleneck || '—';

  const stationsBody = document.getElementById('stations-body');
  r.stations.forEach(s => {
    const tr = document.createElement('tr');
    if(s.name === r.bottleneck) tr.className = 'bottleneck';
    tr.innerHTML = `<td>${esc(s.name)}</td><td>${s.processed.toLocaleString()}</td>` +
      `<td>${(s.utilisation*100).toFixed(1)}%</td><td>${(s.blocked*100).toFixed(1)}%</td>` +
      `<td>${(s.starved*100).toFixed(1)}%</td><td>${(s.down*100).toFixed(1)}%</td><td>${s.failures}</td>`;
    stationsBody.appendChild(tr);
  });

  const buffersBody = document.getElementById('buffers-body');
  r.buffers.forEach(b => {
    const tr = document.createElement('tr');
    const sat = b.capacity && b.peak >= b.capacity;
    if(sat) tr.className = 'saturated';
    tr.innerHTML = `<td>${esc(b.name)}</td><td>${b.avg_level.toFixed(1)}</td><td>${b.peak}</td>` +
      `<td>${b.capacity}</td><td>${sat ? 'saturated' : ''}</td>`;
    buffersBody.appendChild(tr);
  });
})();
</script>
</body>
</html>
"""


def render(payload: dict[str, Any], title: str) -> str:
    data_json = json.dumps(payload).replace("</", "<\\/")
    page = _TEMPLATE
    page = page.replace("__TITLE__", esc_html(title))
    page = page.replace("__NAME__", esc_html(payload["meta"]["name"]))
    page = page.replace("__SEED__", str(payload["meta"]["seed"]))
    page = page.replace("__DURATION__", fmt_time(payload["meta"]["duration"]))
    page = page.replace("__DATA__", data_json)
    return page


def write_and_open(
    payload: dict[str, Any], out_path: Path, title: str, open_browser: bool = True
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(payload, title), encoding="utf-8")
    if open_browser:
        webbrowser.open(out_path.resolve().as_uri())
    return out_path
