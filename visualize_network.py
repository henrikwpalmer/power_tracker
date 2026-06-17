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
<title>Co-location Network</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --ink:      #1a1a1a;
    --ink-mid:  #555;
    --ink-faint:#999;
    --paper:    #f5f4f0;
    --line:     #d4d0c8;
    --accent:   #1a1a1a;
    --mono: "IBM Plex Mono", "Courier New", monospace;
  }}

  body {{
    font-family: var(--mono);
    background: var(--paper);
    color: var(--ink);
    overflow: hidden;
  }}

  svg {{ width: 100vw; height: 100vh; cursor: grab; display: block; }}
  svg:active {{ cursor: grabbing; }}

  /* Graph */
  .node circle {{
    fill: var(--paper);
    stroke: var(--ink);
    stroke-width: 1px;
    cursor: pointer;
  }}
  .node circle:hover {{ stroke-width: 2px; }}
  .node circle.dimmed {{ opacity: 0.12; }}
  .node text {{
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    fill: var(--ink);
    pointer-events: none;
    text-anchor: middle;
    dominant-baseline: middle;
  }}
  .node.dimmed text {{ opacity: 0.12; }}

  .edge {{
    stroke: var(--ink);
    stroke-opacity: 0.25;
    cursor: pointer;
    transition: stroke-opacity 0.1s;
  }}
  .edge:hover {{ stroke-opacity: 0.9; }}
  .edge.hidden {{ display: none; }}
  .edge-hit {{ stroke: transparent; fill: none; cursor: pointer; }}
  .edge-hit.hidden {{ display: none; }}

  /* ── Panel (shared base) ── */
  .panel {{
    position: fixed;
    background: var(--paper);
    border: 1px solid var(--line);
    padding: 20px 22px;
    z-index: 50;
  }}

  /* Filter panel */
  #filters {{
    top: 24px;
    left: 24px;
    width: 220px;
  }}
  #filters-title {{
    font-size: 9px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 20px;
  }}
  .filter-group {{ margin-bottom: 18px; }}
  .filter-group:last-child {{ margin-bottom: 0; }}
  .filter-label {{
    font-size: 9px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 8px;
  }}

  /* Window toggle */
  .window-btns {{ display: flex; gap: 0; border: 1px solid var(--line); }}
  .window-btn {{
    flex: 1;
    padding: 6px 0;
    background: var(--paper);
    border: none;
    border-right: 1px solid var(--line);
    font-family: var(--mono);
    font-size: 11px;
    color: var(--ink-mid);
    cursor: pointer;
    text-align: center;
    transition: background 0.1s, color 0.1s;
  }}
  .window-btn:last-child {{ border-right: none; }}
  .window-btn:hover {{ background: var(--line); color: var(--ink); }}
  .window-btn.active {{ background: var(--ink); color: var(--paper); }}

  /* Airport search */
  .airport-search-wrap {{ position: relative; }}
  #airport-input {{
    width: 100%;
    padding: 6px 8px;
    border: 1px solid var(--line);
    background: var(--paper);
    font-family: var(--mono);
    font-size: 11px;
    color: var(--ink);
    outline: none;
    transition: border-color 0.1s;
  }}
  #airport-input:focus {{ border-color: var(--ink); }}
  #airport-input::placeholder {{ color: var(--ink-faint); }}
  #airport-dropdown {{
    display: none;
    position: absolute;
    top: 100%;
    left: 0; right: 0;
    background: var(--paper);
    border: 1px solid var(--ink);
    border-top: none;
    max-height: 180px;
    overflow-y: auto;
    z-index: 300;
  }}
  .airport-option {{
    padding: 7px 8px;
    font-family: var(--mono);
    font-size: 11px;
    cursor: pointer;
    color: var(--ink);
    border-bottom: 1px solid var(--line);
  }}
  .airport-option:last-child {{ border-bottom: none; }}
  .airport-option:hover {{ background: var(--line); }}
  .airport-option.selected {{ opacity: 0.3; pointer-events: none; }}
  .airport-option .code {{
    font-weight: 500;
    margin-right: 6px;
  }}

  /* Chips */
  #airport-chips {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }}
  .airport-chip {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border: 1px solid var(--ink);
    padding: 2px 6px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--ink);
  }}
  .chip-remove {{
    cursor: pointer;
    font-size: 12px;
    color: var(--ink-mid);
    line-height: 1;
  }}
  .chip-remove:hover {{ color: var(--ink); }}
  #clear-airports {{
    display: none;
    margin-top: 6px;
    font-size: 10px;
    color: var(--ink-faint);
    cursor: pointer;
    letter-spacing: 0.04em;
  }}
  #clear-airports:hover {{ color: var(--ink); }}

  /* Summary */
  #filter-summary {{
    margin-top: 18px;
    padding-top: 14px;
    border-top: 1px solid var(--line);
    font-size: 10px;
    color: var(--ink-faint);
    line-height: 1.9;
    letter-spacing: 0.02em;
  }}
  #filter-summary b {{ color: var(--ink); font-weight: 500; }}

  /* ── Popup ── */
  #popup {{
    display: none;
    position: fixed;
    background: var(--paper);
    border: 1px solid var(--ink);
    max-width: 420px;
    min-width: 300px;
    max-height: 72vh;
    z-index: 200;
    flex-direction: column;
    overflow: hidden;
  }}
  #popup-header {{
    padding: 14px 16px 12px;
    border-bottom: 1px solid var(--line);
    flex-shrink: 0;
  }}
  #popup-title {{
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.02em;
    color: var(--ink);
  }}
  #popup-subtitle {{
    font-size: 10px;
    color: var(--ink-faint);
    margin-top: 3px;
    letter-spacing: 0.04em;
  }}
  #popup-close {{
    position: absolute;
    top: 14px;
    right: 16px;
    cursor: pointer;
    font-size: 14px;
    color: var(--ink-faint);
    line-height: 1;
  }}
  #popup-close:hover {{ color: var(--ink); }}
  #popup-header {{ position: relative; }}
  #popup-body {{ overflow-y: auto; padding: 14px 16px 18px; }}

  .summary-row {{
    display: flex;
    gap: 0;
    margin-bottom: 14px;
    border: 1px solid var(--line);
  }}
  .summary-pill {{
    flex: 1;
    padding: 5px 10px;
    font-size: 10px;
    color: var(--ink-mid);
    letter-spacing: 0.04em;
    border-right: 1px solid var(--line);
  }}
  .summary-pill:last-child {{ border-right: none; }}
  .summary-pill b {{ color: var(--ink); font-weight: 500; display: block; }}

  .overlap-card {{
    border-top: 1px solid var(--line);
    padding: 10px 0;
    font-size: 11px;
    line-height: 1.8;
  }}
  .overlap-card:last-child {{ border-bottom: 1px solid var(--line); }}
  .overlap-card .ap-name {{
    font-weight: 500;
    color: var(--ink);
    font-size: 11px;
    letter-spacing: 0.02em;
  }}
  .airport-badge {{
    font-size: 10px;
    color: var(--ink-mid);
    margin-left: 4px;
    font-weight: 400;
  }}
  .overlap-card .detail {{ color: var(--ink-mid); font-size: 10px; }}

  /* Title */
  #title {{
    position: fixed;
    top: 24px;
    left: 50%;
    transform: translateX(-50%);
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 400;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-faint);
    pointer-events: none;
    white-space: nowrap;
  }}

  /* Legend */
  #legend {{
    position: fixed;
    bottom: 24px;
    right: 24px;
    font-size: 10px;
    color: var(--ink-faint);
    line-height: 2;
    pointer-events: none;
    letter-spacing: 0.04em;
    text-align: right;
  }}
</style>
</head>
<body>

<div id="title">Co-location Network — Private Aviation</div>

<div id="filters" class="panel">
  <div id="filters-title">Filters</div>

  <div class="filter-group">
    <div class="filter-label">Overlap window</div>
    <div class="window-btns">
      <div class="window-btn" data-days="1">±1d</div>
      <div class="window-btn active" data-days="3">±3d</div>
      <div class="window-btn" data-days="5">±5d</div>
    </div>
  </div>

  <div class="filter-group">
    <div class="filter-label">Airports</div>
    <div class="airport-search-wrap">
      <input id="airport-input" type="text" placeholder="Search name or code…" autocomplete="off">
      <div id="airport-dropdown"></div>
    </div>
    <div id="airport-chips"></div>
    <div id="clear-airports">Clear all</div>
  </div>

  <div id="filter-summary"></div>
</div>

<svg id="svg"></svg>

<div id="popup">
  <div id="popup-header">
    <div id="popup-title"></div>
    <div id="popup-subtitle"></div>
    <span id="popup-close">&#x2715;</span>
  </div>
  <div id="popup-body"></div>
</div>

<div id="legend">
  Node size — total co-locations<br>
  Line weight — shared visits<br>
  Click edge for detail<br>
  Drag / scroll to navigate
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
      <div class="summary-pill"><b>${{edge.count}}</b>overlap${{edge.count>1?"s":""}}</div>
      <div class="summary-pill"><b>range</b>${{dateMin===dateMax ? dateMin : dateMin+" — "+dateMax}}</div>
    </div>`;

  edge.overlaps.forEach(o => {{
    const dateStr = o.date_a===o.date_b ? o.date_a : `${{o.date_a}} / ${{o.date_b}}`;
    popupBody.innerHTML += `
      <div class="overlap-card">
        <div class="ap-name">${{o.airport_name}}<span class="airport-badge">[${{o.airport}}]</span></div>
        <div class="detail">Date: ${{dateStr}}</div>
        <div class="detail">Aircraft: ${{o.tail_a}} / ${{o.tail_b}}</div>
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
