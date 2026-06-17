"""
Elite Network Visualizer
Reads jet_locations.db from ./data/data_out/jet_locations.db,
finds ALL airport co-locations within a 5-day window (maximum),
and embeds all data in a self-contained HTML file.

Filtering (time window + airport) happens live in the browser —
no need to re-run Python when changing filters.

Dependencies: pip install pandas
"""

import json
import sqlite3
import pandas as pd
from itertools import combinations

DB_PATH     = "./data/data_out/jet_locations.db"
OUTPUT_HTML = "network.html"
MAX_WINDOW  = 5  # compute at maximum window; JS filters down from here

# ── Load ───────────────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
cols = [r[1] for r in conn.execute("PRAGMA table_info(locations)").fetchall()]
if "airport" in cols:
    df = pd.read_sql_query(
        "SELECT timestamp, tail, owner, airport, airport_name, lat, lon FROM locations", conn)
else:
    df = pd.read_sql_query(
        "SELECT timestamp, tail, owner, lat, lon FROM locations", conn)
    df["airport"]      = "UNK"
    df["airport_name"] = "Unknown"
conn.close()

df["date"] = pd.to_datetime(df["timestamp"], errors="coerce")
df = df.dropna(subset=["date"])
df = df[(df["lat"] != 0.0) | (df["lon"] != 0.0)]
df["lat_r"] = df["lat"].round(2)
df["lon_r"] = df["lon"].round(2)

owners = sorted(df["owner"].unique().tolist())
print(f"People: {owners}")
print(f"Records: {len(df)}")

# ── Find all co-locations at MAX_WINDOW ────────────────────────────────────────
# Each overlap stores day_diff so JS can filter by time window client-side.
edges_raw = {}

for owner_a, owner_b in combinations(owners, 2):
    df_a = df[df["owner"] == owner_a].copy()
    df_b = df[df["owner"] == owner_b].copy()

    merged = df_a.merge(df_b, on=["lat_r", "lon_r"], suffixes=("_a", "_b"))
    if merged.empty:
        continue

    merged["day_diff"] = (merged["date_a"] - merged["date_b"]).abs().dt.days
    hits = merged[merged["day_diff"] <= MAX_WINDOW].copy()
    if hits.empty:
        continue

    overlaps = []
    seen = set()
    for _, row in hits.iterrows():
        key = (str(row["date_a"].date()), str(row["date_b"].date()), row["lat_r"], row["lon_r"])
        if key in seen:
            continue
        seen.add(key)
        overlaps.append({
            "date_a":       str(row["date_a"].date()),
            "date_b":       str(row["date_b"].date()),
            "day_diff":     int(row["day_diff"]),
            "airport":      str(row.get("airport_a",      row.get("airport",      "?"))),
            "airport_name": str(row.get("airport_name_a", row.get("airport_name", "Unknown"))),
            "lat":          float(row["lat_a"]),
            "lon":          float(row["lon_a"]),
            "tail_a":       str(row["tail_a"]),
            "tail_b":       str(row["tail_b"]),
        })

    if overlaps:
        edges_raw[(owner_a, owner_b)] = overlaps
        print(f"  {owner_a} ↔ {owner_b}: {len(overlaps)} overlap(s)")

# ── Build edge list with full overlap data ─────────────────────────────────────
edges_list = []
for (a, b), ov in edges_raw.items():
    edges_list.append({
        "source":   a,
        "target":   b,
        "overlaps": ov,   # JS will filter these by window + airport
    })

nodes_json = json.dumps([{"id": o, "label": o} for o in owners])
edges_json = json.dumps(edges_list)

# ── Build sorted airport list for the dropdown ─────────────────────────────────
airport_map = {}  # code -> name
for ov_list in edges_raw.values():
    for o in ov_list:
        airport_map[o["airport"]] = o["airport_name"]
airports_json = json.dumps(
    sorted([{"code": k, "name": v} for k, v in airport_map.items()],
           key=lambda x: x["name"])
)

# ── HTML ───────────────────────────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Elite Jet Network</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f0f2f5;
    overflow: hidden;
  }}
  svg {{ width: 100vw; height: 100vh; cursor: grab; display: block; }}
  svg:active {{ cursor: grabbing; }}

  .node circle {{
    fill: #ffffff;
    stroke: #2c3e50;
    stroke-width: 2.5px;
    filter: drop-shadow(0 3px 8px rgba(0,0,0,0.13));
    cursor: pointer;
    transition: stroke 0.15s;
  }}
  .node circle:hover {{ stroke: #c0392b; stroke-width: 3.5px; }}
  .node circle.dimmed {{ opacity: 0.18; }}
  .node text {{
    font-size: 12px;
    font-weight: 700;
    fill: #1a252f;
    pointer-events: none;
    text-anchor: middle;
    dominant-baseline: middle;
  }}
  .node.dimmed text {{ opacity: 0.18; }}

  .edge {{
    stroke: #2980b9;
    stroke-opacity: 0.5;
    cursor: pointer;
    transition: stroke 0.15s, stroke-opacity 0.15s, opacity 0.2s;
  }}
  .edge:hover {{ stroke: #c0392b; stroke-opacity: 1; }}
  .edge.hidden {{ display: none; }}
  .edge-hit {{
    stroke: transparent;
    fill: none;
    cursor: pointer;
  }}
  .edge-hit.hidden {{ display: none; }}

  /* ── Filter panel ── */
  #filters {{
    position: fixed;
    top: 20px;
    left: 20px;
    background: rgba(255,255,255,0.97);
    border-radius: 12px;
    padding: 16px 18px;
    box-shadow: 0 2px 16px rgba(0,0,0,0.13);
    z-index: 50;
    min-width: 230px;
  }}
  #filters h3 {{
    font-size: 12px;
    font-weight: 700;
    color: #1a252f;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin-bottom: 14px;
  }}
  .filter-group {{
    margin-bottom: 14px;
  }}
  .filter-group:last-child {{ margin-bottom: 0; }}
  .filter-label {{
    font-size: 11px;
    font-weight: 600;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    margin-bottom: 7px;
  }}
  .window-btns {{
    display: flex;
    gap: 6px;
  }}
  .window-btn {{
    flex: 1;
    padding: 6px 0;
    border: 1.5px solid #d0d7de;
    border-radius: 6px;
    background: #fff;
    font-size: 12px;
    font-weight: 600;
    color: #444;
    cursor: pointer;
    transition: all 0.15s;
    text-align: center;
  }}
  .window-btn:hover {{ border-color: #2980b9; color: #2980b9; }}
  .window-btn.active {{
    background: #2980b9;
    border-color: #2980b9;
    color: #fff;
  }}

  /* Airport search */
  .airport-search-wrap {{ position: relative; }}
  #airport-input {{
    width: 100%;
    padding: 7px 10px;
    border: 1.5px solid #d0d7de;
    border-radius: 6px;
    font-size: 12px;
    color: #1a252f;
    outline: none;
    transition: border-color 0.15s;
  }}
  #airport-input:focus {{ border-color: #2980b9; }}
  #airport-dropdown {{
    display: none;
    position: absolute;
    top: calc(100% + 4px);
    left: 0; right: 0;
    background: #fff;
    border: 1.5px solid #d0d7de;
    border-radius: 6px;
    max-height: 200px;
    overflow-y: auto;
    z-index: 200;
    box-shadow: 0 4px 16px rgba(0,0,0,0.12);
  }}
  .airport-option {{
    padding: 8px 10px;
    font-size: 12px;
    cursor: pointer;
    color: #1a252f;
    border-bottom: 1px solid #f0f0f0;
  }}
  .airport-option:last-child {{ border-bottom: none; }}
  .airport-option:hover {{ background: #eaf2fb; }}
  .airport-option.selected {{ opacity: 0.38; pointer-events: none; }}
  .airport-option .code {{
    display: inline-block;
    background: #2980b9;
    color: #fff;
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 10px;
    font-weight: 700;
    margin-right: 6px;
  }}
  /* Selected airport chips */
  #airport-chips {{
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 7px;
  }}
  .airport-chip {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: #eaf2fb;
    border: 1px solid #aed6f1;
    border-radius: 4px;
    padding: 3px 7px 3px 8px;
    font-size: 11px;
    font-weight: 600;
    color: #1a5276;
  }}
  .chip-remove {{
    cursor: pointer;
    font-size: 13px;
    color: #7fb3d3;
    line-height: 1;
    font-weight: 400;
  }}
  .chip-remove:hover {{ color: #c0392b; }}
  #clear-airports {{
    display: none;
    margin-top: 6px;
    font-size: 11px;
    color: #2980b9;
    cursor: pointer;
    text-decoration: underline;
  }}
  #clear-airports:hover {{ color: #c0392b; }}

  #filter-summary {{
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid #eee;
    font-size: 11px;
    color: #888;
    line-height: 1.7;
  }}
  #filter-summary b {{ color: #1a252f; }}

  /* ── Popup ── */
  #popup {{
    display: none;
    position: fixed;
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.2);
    max-width: 460px;
    min-width: 330px;
    max-height: 75vh;
    z-index: 200;
    flex-direction: column;
    overflow: hidden;
  }}
  #popup-header {{
    background: #1a252f;
    color: #fff;
    padding: 14px 18px 11px;
    font-size: 14px;
    font-weight: 700;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-shrink: 0;
  }}
  #popup-subtitle {{
    font-size: 11px;
    font-weight: 400;
    opacity: 0.7;
    margin-top: 3px;
  }}
  #popup-close {{
    cursor: pointer;
    font-size: 18px;
    opacity: 0.6;
    margin-left: 12px;
    flex-shrink: 0;
    line-height: 1;
  }}
  #popup-close:hover {{ opacity: 1; }}
  #popup-body {{ overflow-y: auto; padding: 13px 17px 17px; }}
  .summary-row {{
    display: flex;
    gap: 8px;
    margin-bottom: 13px;
    flex-wrap: wrap;
  }}
  .summary-pill {{
    background: #eaf2fb;
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 600;
    color: #1a5276;
  }}
  .overlap-card {{
    background: #f4f7fb;
    border-left: 4px solid #2980b9;
    border-radius: 0 7px 7px 0;
    padding: 9px 13px;
    margin-bottom: 8px;
    font-size: 12px;
    line-height: 1.75;
  }}
  .overlap-card .ap-name {{ font-weight: 700; color: #1a252f; font-size: 13px; }}
  .airport-badge {{
    display: inline-block;
    background: #2980b9;
    color: #fff;
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 10px;
    font-weight: 700;
    margin-left: 5px;
    vertical-align: middle;
  }}
  .overlap-card .detail {{ color: #555; }}

  #title {{
    position: fixed;
    top: 20px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 16px;
    font-weight: 700;
    color: #1a252f;
    background: rgba(255,255,255,0.93);
    padding: 9px 22px;
    border-radius: 22px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.1);
    pointer-events: none;
    white-space: nowrap;
  }}

  #legend {{
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: rgba(255,255,255,0.93);
    border-radius: 10px;
    padding: 10px 15px;
    font-size: 11px;
    color: #666;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    line-height: 1.9;
    pointer-events: none;
  }}
  #legend b {{ color: #1a252f; }}
</style>
</head>
<body>

<div id="title">Elite Jet Co-location Network</div>

<!-- Filter panel -->
<div id="filters">
  <h3>Filters</h3>

  <div class="filter-group">
    <div class="filter-label">Overlap window</div>
    <div class="window-btns">
      <div class="window-btn" data-days="1">1 day</div>
      <div class="window-btn active" data-days="3">3 days</div>
      <div class="window-btn" data-days="5">5 days</div>
    </div>
  </div>

  <div class="filter-group">
    <div class="filter-label">Airports</div>
    <div class="airport-search-wrap">
      <input id="airport-input" type="text" placeholder="Search airport name or code…" autocomplete="off">
      <div id="airport-dropdown"></div>
    </div>
    <div id="airport-chips"></div>
    <div id="clear-airports">Clear all airports</div>
  </div>

  <div id="filter-summary"></div>
</div>

<svg id="svg"></svg>

<!-- Popup -->
<div id="popup">
  <div id="popup-header">
    <div>
      <div id="popup-title"></div>
      <div id="popup-subtitle"></div>
    </div>
    <span id="popup-close">✕</span>
  </div>
  <div id="popup-body"></div>
</div>

<div id="legend">
  <b>How to read</b><br>
  Larger node = more total overlaps<br>
  Thicker line = more shared visits<br>
  Click a line for details<br>
  Drag nodes · Scroll to zoom
</div>

<script>
const ALL_NODES   = {nodes_json};
const ALL_EDGES   = {edges_json};
const ALL_AIRPORTS = {airports_json};

// ── State ──────────────────────────────────────────────────────────────────────
let activeWindow   = 3;
let activeAirports = new Set();  // empty = no filter

// ── Filter logic ───────────────────────────────────────────────────────────────
function filteredEdges() {{
  return ALL_EDGES.map(e => {{
    const ov = e.overlaps.filter(o =>
      o.day_diff <= activeWindow &&
      (activeAirports.size === 0 || activeAirports.has(o.airport))
    );
    return ov.length ? {{ ...e, overlaps: ov, count: ov.length }} : null;
  }}).filter(Boolean);
}}

function computeNodeSizes(fEdges) {{
  const totals = {{}};
  ALL_NODES.forEach(n => totals[n.id] = 0);
  fEdges.forEach(e => {{
    totals[e.source] = (totals[e.source] || 0) + e.count;
    totals[e.target] = (totals[e.target] || 0) + e.count;
  }});
  const maxVal = Math.max(1, ...Object.values(totals));
  return {{ totals, maxVal }};
}}

// ── SVG setup ──────────────────────────────────────────────────────────────────
const svg = document.getElementById("svg");
const W   = () => window.innerWidth;
const H   = () => window.innerHeight;

let pan = {{x:0,y:0}}, zoom = 1;
let draggingNode = null, panStart = null;
const ROOT = document.createElementNS("http://www.w3.org/2000/svg","g");
svg.appendChild(ROOT);

function applyTransform() {{
  ROOT.setAttribute("transform", `translate(${{pan.x}},${{pan.y}}) scale(${{zoom}})`);
}}

// ── Node state (positions persist across filter changes) ───────────────────────
let nodes = ALL_NODES.map((n, i) => ({{
  ...n,
  x:  W()/2 + 240 * Math.cos(2*Math.PI*i/ALL_NODES.length),
  y:  H()/2 + 240 * Math.sin(2*Math.PI*i/ALL_NODES.length),
  vx: 0, vy: 0,
  r:  38,
}}));

// ── Build persistent SVG elements for all edges and nodes ─────────────────────
// We show/hide them rather than recreating, so positions are preserved.
const edgeEls  = {{}};  // key -> {{line, hit, edgeData}}
const nodeEls  = {{}};  // id  -> {{g, circle}}

ALL_EDGES.forEach(e => {{
  const line = document.createElementNS("http://www.w3.org/2000/svg","line");
  line.classList.add("edge");
  const hit  = document.createElementNS("http://www.w3.org/2000/svg","line");
  hit.setAttribute("stroke", "transparent");
  hit.classList.add("edge-hit");
  ROOT.appendChild(line);
  ROOT.appendChild(hit);
  edgeEls[e.source+"|"+e.target] = {{line, hit, edgeData: e}};
}});

ALL_NODES.forEach(n => {{
  const g      = document.createElementNS("http://www.w3.org/2000/svg","g");
  g.classList.add("node");
  const circle = document.createElementNS("http://www.w3.org/2000/svg","circle");
  g.appendChild(circle);
  const words  = n.label.split(" ");
  const lineH  = 14;
  const startY = -((words.length - 1) * lineH) / 2;
  words.forEach((w, i) => {{
    const t = document.createElementNS("http://www.w3.org/2000/svg","text");
    t.setAttribute("y", startY + i * lineH);
    t.textContent = w;
    g.appendChild(t);
  }});
  circle.addEventListener("mousedown", ev => {{ draggingNode = nodes.find(x=>x.id===n.id); ev.stopPropagation(); }});
  ROOT.appendChild(g);
  nodeEls[n.id] = {{g, circle}};
}});

// ── Apply filters: update visibility + thickness ───────────────────────────────
function applyFilters() {{
  const fEdges  = filteredEdges();
  const fKeys   = new Set(fEdges.map(e => e.source+"|"+e.target));
  const maxCount = Math.max(1, ...fEdges.map(e => e.count));

  // Nodes involved in any visible edge
  const activeNodes = new Set();
  fEdges.forEach(e => {{ activeNodes.add(e.source); activeNodes.add(e.target); }});

  // Update edges
  Object.entries(edgeEls).forEach(([key, {{line, hit, edgeData}}]) => {{
    if (fKeys.has(key)) {{
      const fEdge   = fEdges.find(e => e.source+"|"+e.target === key);
      const thickness = 1.5 + (fEdge.count / maxCount) * 16;
      line.setAttribute("stroke-width", thickness);
      hit.setAttribute("stroke-width", Math.max(thickness, 14));
      line.classList.remove("hidden");
      hit.classList.remove("hidden");
      // Attach current filtered overlaps to click handler
      const clickFn = ev => showPopup(fEdge, ev);
      line.onclick = clickFn;
      hit.onclick  = clickFn;
    }} else {{
      line.classList.add("hidden");
      hit.classList.add("hidden");
    }}
  }});

  // Update nodes: resize + dim inactive
  const {{ totals, maxVal }} = computeNodeSizes(fEdges);
  nodes.forEach(n => {{
    const r = 30 + (totals[n.id] / maxVal) * 28;
    n.r = r;
    const {{g, circle}} = nodeEls[n.id];
    circle.setAttribute("r", r);
    if (activeNodes.has(n.id) || fEdges.length === 0) {{
      circle.classList.remove("dimmed");
      g.classList.remove("dimmed");
    }} else {{
      circle.classList.add("dimmed");
      g.classList.add("dimmed");
    }}
  }});

  // Update summary
  const totalOv = fEdges.reduce((s,e)=>s+e.count,0);
  const airportStr = activeAirports.size > 0
    ? `<br><b>Airports:</b> ${{[...activeAirports].join(", ")}}`
    : "";
  document.getElementById("filter-summary").innerHTML =
    `<b>Visible connections:</b> ${{fEdges.length}}<br>` +
    `<b>Visible overlaps:</b> ${{totalOv}}` + airportStr;
}}

// ── Redraw positions ───────────────────────────────────────────────────────────
function redraw() {{
  Object.entries(edgeEls).forEach(([key, {{line, hit}}]) => {{
    const [src, tgt] = key.split("|");
    const na = nodes.find(n=>n.id===src);
    const nb = nodes.find(n=>n.id===tgt);
    if (!na||!nb) return;
    [line, hit].forEach(l => {{
      l.setAttribute("x1", na.x); l.setAttribute("y1", na.y);
      l.setAttribute("x2", nb.x); l.setAttribute("y2", nb.y);
    }});
  }});
  nodes.forEach(n => {{
    const el = nodeEls[n.id];
    if (el) el.g.setAttribute("transform", `translate(${{n.x}},${{n.y}})`);
  }});
}}

// ── Force simulation ───────────────────────────────────────────────────────────
const REPEL=11000, ATTRACT=0.013, CENTER=0.007, DAMPING=0.80, IDEAL=240;

function tick() {{
  const fEdges = filteredEdges();
  nodes.forEach(a => {{
    nodes.forEach(b => {{
      if (a===b) return;
      const dx=a.x-b.x, dy=a.y-b.y;
      const d=Math.sqrt(dx*dx+dy*dy)||1;
      a.vx+=(dx/d)*REPEL/(d*d);
      a.vy+=(dy/d)*REPEL/(d*d);
    }});
    a.vx+=(W()/2-a.x)*CENTER;
    a.vy+=(H()/2-a.y)*CENTER;
  }});
  fEdges.forEach(e => {{
    const na=nodes.find(n=>n.id===e.source);
    const nb=nodes.find(n=>n.id===e.target);
    if(!na||!nb) return;
    const dx=nb.x-na.x, dy=nb.y-na.y;
    const d=Math.sqrt(dx*dx+dy*dy)||1;
    const f=(d-IDEAL)*ATTRACT;
    na.vx+=(dx/d)*f; na.vy+=(dy/d)*f;
    nb.vx-=(dx/d)*f; nb.vy-=(dy/d)*f;
  }});
  nodes.forEach(n => {{
    if(n===draggingNode) return;
    n.vx*=DAMPING; n.vy*=DAMPING;
    n.x+=n.vx; n.y+=n.vy;
  }});
  redraw();
  requestAnimationFrame(tick);
}}

applyFilters();
tick();

// ── Mouse interaction ──────────────────────────────────────────────────────────
svg.addEventListener("mousemove", ev => {{
  if (draggingNode) {{
    const p=clientToSVG(ev.clientX,ev.clientY);
    draggingNode.x=p.x; draggingNode.y=p.y;
    draggingNode.vx=0;  draggingNode.vy=0;
  }} else if (panStart) {{
    pan.x+=ev.clientX-panStart.cx;
    pan.y+=ev.clientY-panStart.cy;
    panStart.cx=ev.clientX; panStart.cy=ev.clientY;
    applyTransform();
  }}
}});
svg.addEventListener("mouseup",   ()=>{{ draggingNode=null; panStart=null; }});
svg.addEventListener("mousedown", ev=>{{ if(!draggingNode) panStart={{cx:ev.clientX,cy:ev.clientY}}; }});
svg.addEventListener("wheel", ev=>{{
  ev.preventDefault();
  const dir = ev.deltaY > 0 ? -1 : 1;
  zoom = Math.min(4, Math.max(0.15, zoom*(1+dir*0.05)));
  applyTransform();
}}, {{passive:false}});
function clientToSVG(cx,cy){{ return {{x:(cx-pan.x)/zoom, y:(cy-pan.y)/zoom}}; }}

// ── Window buttons ─────────────────────────────────────────────────────────────
document.querySelectorAll(".window-btn").forEach(btn => {{
  btn.addEventListener("click", () => {{
    document.querySelectorAll(".window-btn").forEach(b=>b.classList.remove("active"));
    btn.classList.add("active");
    activeWindow = parseInt(btn.dataset.days);
    applyFilters();
  }});
}});

// ── Airport search + dropdown ──────────────────────────────────────────────────
const airportInput    = document.getElementById("airport-input");
const airportDropdown = document.getElementById("airport-dropdown");
const airportChips    = document.getElementById("airport-chips");
const clearAirports   = document.getElementById("clear-airports");

function renderChips() {{
  airportChips.innerHTML = [...activeAirports].map(code => {{
    const name = ALL_AIRPORTS.find(a=>a.code===code)?.name || code;
    return `<span class="airport-chip" data-code="${{code}}">
      ${{code}}
      <span class="chip-remove" data-code="${{code}}" title="Remove ${{name}}">×</span>
    </span>`;
  }}).join("");
  clearAirports.style.display = activeAirports.size > 1 ? "block" : "none";
  // Wire up remove buttons
  airportChips.querySelectorAll(".chip-remove").forEach(btn => {{
    btn.addEventListener("click", () => {{
      activeAirports.delete(btn.dataset.code);
      renderChips();
      renderDropdown(airportInput.value);
      applyFilters();
    }});
  }});
}}

function renderDropdown(query) {{
  const q = query.trim().toLowerCase();
  const matches = (q.length === 0 ? ALL_AIRPORTS : ALL_AIRPORTS.filter(a =>
    a.code.toLowerCase().includes(q) || a.name.toLowerCase().includes(q)
  ));

  if (matches.length === 0) {{
    airportDropdown.innerHTML = `<div class="airport-option" style="color:#999">No airports found</div>`;
  }} else {{
    airportDropdown.innerHTML = matches.slice(0, 60).map(a => {{
      const already = activeAirports.has(a.code) ? " selected" : "";
      return `<div class="airport-option${{already}}" data-code="${{a.code}}">
        <span class="code">${{a.code}}</span>${{a.name}}
      </div>`;
    }}).join("");
    airportDropdown.querySelectorAll(".airport-option:not(.selected)").forEach(el => {{
      el.addEventListener("click", () => {{
        activeAirports.add(el.dataset.code);
        airportInput.value = "";
        airportDropdown.style.display = "none";
        renderChips();
        applyFilters();
      }});
    }});
  }}
  airportDropdown.style.display = "block";
}}

airportInput.addEventListener("input",   () => renderDropdown(airportInput.value));
airportInput.addEventListener("focus",   () => renderDropdown(airportInput.value));
airportInput.addEventListener("keydown", ev => {{
  if (ev.key === "Escape") {{ airportDropdown.style.display="none"; airportInput.blur(); }}
}});
document.addEventListener("click", ev => {{
  if (!airportInput.contains(ev.target) && !airportDropdown.contains(ev.target))
    airportDropdown.style.display = "none";
}});

clearAirports.addEventListener("click", () => {{
  activeAirports.clear();
  airportInput.value = "";
  renderChips();
  applyFilters();
}});

// ── Popup ──────────────────────────────────────────────────────────────────────
const popup      = document.getElementById("popup");
const popupTitle = document.getElementById("popup-title");
const popupSub   = document.getElementById("popup-subtitle");
const popupBody  = document.getElementById("popup-body");
document.getElementById("popup-close").addEventListener("click", ()=>popup.style.display="none");

function showPopup(edge, ev) {{
  ev.stopPropagation();
  const allDates = [...new Set(
    edge.overlaps.flatMap(o=>[o.date_a,o.date_b])
  )].sort();
  const dateMin = allDates[0]||"", dateMax = allDates[allDates.length-1]||"";

  popupTitle.textContent = `${{edge.source}} — ${{edge.target}}`;
  popupSub.textContent   = dateMin===dateMax ? dateMin : `${{dateMin}} to ${{dateMax}}`;

  popupBody.innerHTML = `
    <div class="summary-row">
      <span class="summary-pill">${{edge.count}} overlap${{edge.count>1?"s":""}}</span>
      <span class="summary-pill">${{dateMin===dateMax ? dateMin : dateMin+" to "+dateMax}}</span>
    </div>`;

  edge.overlaps.forEach(o => {{
    const dateStr = o.date_a===o.date_b ? o.date_a : `${{o.date_a}} / ${{o.date_b}}`;
    popupBody.innerHTML += `
      <div class="overlap-card">
        <div class="ap-name">${{o.airport_name}}<span class="airport-badge">${{o.airport}}</span></div>
        <div class="detail">Date: ${{dateStr}}</div>
        <div class="detail">Aircraft: ${{o.tail_a}} &amp; ${{o.tail_b}}</div>
        <div class="detail">Coords: ${{o.lat.toFixed(3)}}, ${{o.lon.toFixed(3)}}</div>
      </div>`;
  }});

  popup.style.display="flex";
  let left=ev.clientX+15, top=ev.clientY-20;
  const pw=popup.offsetWidth, ph=popup.offsetHeight;
  if(left+pw>window.innerWidth-10)  left=ev.clientX-pw-15;
  if(top+ph>window.innerHeight-10)  top=window.innerHeight-ph-10;
  if(top<10) top=10;
  popup.style.left=left+"px"; popup.style.top=top+"px";
}}

document.addEventListener("click", ev=>{{
  if(!popup.contains(ev.target)) popup.style.display="none";
}});
</script>
</body>
</html>"""

with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\n✓ Saved -> {OUTPUT_HTML}")
