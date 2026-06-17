"""
=============================================================
  Plane Tracker — Flask Backend
  - Live positions via airplanes.live (free, no key needed)
  - Watchlist loaded from CSV: Registration Code, Owner Name, Description
  - Positions saved to SQLite every SAVE_INTERVAL seconds
  - /api/snapshot endpoint serves historical positions for the timeline
  - Polling strategy:
      • One API call every 5 seconds (respects 1 req/s rate limit)
      • Full watchlist cycle waits 5 minutes before repeating
      • Frontend reads from a per-plane cache — results appear
        on the map immediately as each plane is found
=============================================================
"""

from flask import Flask, jsonify, render_template_string, request, send_from_directory 
import urllib.request, json, csv, os, time, threading, sqlite3

app = Flask(__name__)

@app.route('/')
def home():
    return send_from_directory('.', 'index.html')

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
ADSB_BASE_URL = "http://api.airplanes.live/v2"
CSV_PATH      = "PrivateJetDirectory.csv"
DB_PATH       = "planes.db"

POLL_INTERVAL = 5      # seconds between each individual plane lookup
CYCLE_WAIT    = 300    # seconds to wait after a full watchlist cycle (5 min)
SAVE_INTERVAL = 300    # seconds between position snapshots saved to the DB


# ─────────────────────────────────────────────────────────────
#  DATABASE SETUP
#  Creates the positions table if it doesn't already exist.
#  Each row is one position snapshot for one aircraft.
# ─────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            hex       TEXT,
            tail      TEXT,
            owner     TEXT,
            lat       REAL,
            lon       REAL,
            altitude  INTEGER,
            speed     REAL,
            heading   REAL
        )
    """)
    conn.commit()
    conn.close()
    print(f"  [db] Database ready at {DB_PATH}")


# ─────────────────────────────────────────────────────────────
#  SAVE SNAPSHOT
#  Writes one position record for a plane to the DB.
#  Called by the polling loop every SAVE_INTERVAL seconds.
# ─────────────────────────────────────────────────────────────
def save_snapshot(plane):
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO positions
            (timestamp, hex, tail, owner, lat, lon, altitude, speed, heading)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(time.time()),
        plane.get("hex",      ""),
        plane.get("tail",     ""),
        plane.get("owner",    ""),
        plane.get("lat",       0),
        plane.get("lon",       0),
        plane.get("altitude",  0),
        plane.get("speed",     0),
        plane.get("heading",   0),
    ))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────
#  SHARED CACHE
#
#  _plane_cache — keyed by registration; updated one plane at a
#                 time so the frontend sees results immediately.
#  _cycle_meta  — overall polling cycle metadata.
#  _last_save   — unix timestamp of the last DB write; compared
#                 against SAVE_INTERVAL to throttle DB writes.
# ─────────────────────────────────────────────────────────────
_cache_lock  = threading.Lock()
_plane_cache = {}   # { "N628TS": { "status": "found"|"missing", "data": {...} } }
_cycle_meta  = {
    "last_updated": None,
    "next_cycle":   None,
    "status":       "starting",
    "current_reg":  None,
}
_last_save = 0   # protected by _cache_lock


# ─────────────────────────────────────────────────────────────
#  LOAD WATCHLIST FROM CSV
# ─────────────────────────────────────────────────────────────
def load_watchlist():
    watchlist = {}

    if not os.path.exists(CSV_PATH):
        print(f"  [csv] WARNING: {CSV_PATH} not found")
        return watchlist

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reg = row.get("Registration Code", "").strip()
            if reg:
                watchlist[reg] = {
                    "owner":       row.get("Owner Name",  "Unknown").strip(),
                    "description": row.get("Description", "").strip(),
                }

    print(f"  [csv] Loaded {len(watchlist)} planes from {CSV_PATH}")
    return watchlist


# ─────────────────────────────────────────────────────────────
#  FETCH SINGLE PLANE (airplanes.live)
#  Returns a normalised plane dict including a trail array,
#  or None if the plane is not currently transmitting.
# ─────────────────────────────────────────────────────────────
def fetch_plane_by_reg(reg, meta):
    url = f"{ADSB_BASE_URL}/reg/{reg}"
    req = urllib.request.Request(url, headers={"User-Agent": "PlaneTracker/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read())
    except Exception as e:
        print(f"  [poll] {reg} ({meta['owner']}) — request failed: {e}")
        return None

    ac_list = raw.get("ac", [])
    if not ac_list:
        return None

    ac = ac_list[0]

    # alt_baro can be the string "ground" — normalise to 0
    alt = ac.get("alt_baro", 0)
    if alt == "ground":
        alt = 0

    # Normalise trail points from airplanes.live response
    raw_trail = ac.get("trail", [])
    trail = [
        {"lat": pt["lat"], "lon": pt["lon"],
         "alt": pt.get("alt", 0), "ts": pt.get("ts", 0)}
        for pt in raw_trail
        if pt.get("lat") is not None and pt.get("lon") is not None
    ]

    return {
        "hex":         ac.get("hex", ""),
        "tail":        ac.get("r", reg),
        "callsign":    (ac.get("flight") or "").strip(),
        "owner":       meta["owner"],
        "description": meta["description"],
        "type":        ac.get("t", "Unknown"),
        "lat":         ac.get("lat", 0),
        "lon":         ac.get("lon", 0),
        "altitude":    alt,
        "speed":       ac.get("gs", 0),
        "heading":     ac.get("track", 0),
        "vertical":    ac.get("baro_rate", 0),
        "squawk":      ac.get("squawk", ""),
        "seen":        ac.get("seen", 0),
        "trail":       trail,
    }


# ─────────────────────────────────────────────────────────────
#  BACKGROUND POLLING THREAD
#
#  Runs forever:
#    1. Load watchlist from CSV
#    2. Query each plane one at a time, sleeping POLL_INTERVAL
#       between calls to respect the API rate limit
#    3. Update _plane_cache immediately after each result —
#       the frontend sees each plane as soon as it's polled
#    4. Save a DB snapshot every SAVE_INTERVAL seconds
#    5. Sleep CYCLE_WAIT, then repeat
# ─────────────────────────────────────────────────────────────
def polling_loop():
    global _last_save
    print("  [poller] Background polling thread started")

    while True:
        watchlist = load_watchlist()

        if not watchlist:
            print("  [poller] Watchlist is empty — retrying in 30 s")
            time.sleep(30)
            continue

        with _cache_lock:
            _cycle_meta["status"] = "polling"

        # Decide whether this cycle should save snapshots to the DB
        # (checked once per cycle so all planes in a cycle are treated equally)
        should_save = (time.time() - _last_save) >= SAVE_INTERVAL

        for reg, meta in watchlist.items():
            with _cache_lock:
                _cycle_meta["current_reg"] = reg

            print(f"  [poll] Querying {reg} ({meta['owner']})...")
            plane = fetch_plane_by_reg(reg, meta)

            with _cache_lock:
                if plane:
                    print(f"  [poll] {reg} → found at {plane['lat']}, {plane['lon']}")
                    _plane_cache[reg] = {"status": "found", "data": plane}

                    # Save to DB if enough time has passed since last snapshot
                    if should_save:
                        save_snapshot(plane)
                else:
                    print(f"  [poll] {reg} → no signal")
                    _plane_cache[reg] = {
                        "status": "missing",
                        "data": {
                            "tail":        reg,
                            "owner":       meta["owner"],
                            "description": meta["description"],
                        }
                    }

            time.sleep(POLL_INTERVAL)

        # Update save timestamp after a save cycle completes
        if should_save:
            _last_save = time.time()

        now_iso  = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        next_iso = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(time.time() + CYCLE_WAIT)
        )

        with _cache_lock:
            _cycle_meta["last_updated"] = now_iso
            _cycle_meta["next_cycle"]   = next_iso
            _cycle_meta["status"]       = "waiting"
            _cycle_meta["current_reg"]  = None

        found_count   = sum(1 for v in _plane_cache.values() if v["status"] == "found")
        missing_count = sum(1 for v in _plane_cache.values() if v["status"] == "missing")
        print(f"  [poller] Cycle complete — {found_count} found, "
              f"{missing_count} missing. Next at {next_iso}")

        time.sleep(CYCLE_WAIT)


# ─────────────────────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the single-page frontend."""
    with open("index.html", "r", encoding="utf-8") as f:
        return render_template_string(f.read())


@app.route("/api/planes")
def api_planes():
    """
    GET /api/planes
    Returns live positions from _plane_cache, plus poller metadata.
    Each plane's entry is updated as soon as it's polled, so the
    frontend sees results one at a time rather than end-of-cycle.
    """
    with _cache_lock:
        planes  = [v["data"] for v in _plane_cache.values() if v["status"] == "found"]
        missing = [v["data"] for v in _plane_cache.values() if v["status"] == "missing"]
        return jsonify({
            "planes":       planes,
            "missing":      missing,
            "last_updated": _cycle_meta["last_updated"],
            "next_cycle":   _cycle_meta["next_cycle"],
            "status":       _cycle_meta["status"],
            "current_reg":  _cycle_meta["current_reg"],
            "source":       "airplanes.live",
        })


@app.route("/api/status")
def api_status():
    """GET /api/status — lightweight poller state, no plane data."""
    with _cache_lock:
        return jsonify({
            "status":       _cycle_meta["status"],
            "current_reg":  _cycle_meta["current_reg"],
            "last_updated": _cycle_meta["last_updated"],
            "next_cycle":   _cycle_meta["next_cycle"],
        })


@app.route("/api/trail/<reg>")
def api_trail(reg):
    """
    GET /api/trail/<registration>
    Returns the short trail from the last airplanes.live response.
    """
    with _cache_lock:
        entry = _plane_cache.get(reg.upper())

    if not entry or entry["status"] != "found":
        return jsonify({"reg": reg, "trail": []})

    return jsonify({"reg": reg, "trail": entry["data"].get("trail", [])})


@app.route("/api/snapshot")
def api_snapshot():
    """
    GET /api/snapshot?ts=<unix_timestamp>
    Returns the closest recorded position for each aircraft that was
    seen within 30 minutes before the requested timestamp.
    Used by the timeline slider in the frontend.
    """
    ts = request.args.get("ts", type=int)
    if not ts:
        return jsonify({"planes": []})

    STALENESS_WINDOW = 30 * 60   # 30 minutes — planes older than this are hidden

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    # For each tail find the most recent record AT OR BEFORE the requested
    # timestamp, but only within the staleness window.
    cur.execute("""
        SELECT p1.id, p1.tail, p1.hex, p1.owner,
               p1.lat, p1.lon, p1.altitude, p1.speed, p1.heading, p1.timestamp
        FROM positions p1
        INNER JOIN (
            SELECT tail, MAX(timestamp) AS max_ts
            FROM positions
            WHERE timestamp <= ?
              AND timestamp >= ? - ?
            GROUP BY tail
        ) p2
          ON  p1.tail      = p2.tail
          AND p1.timestamp = p2.max_ts
        GROUP BY p1.tail
    """, (ts, ts, STALENESS_WINDOW))

    rows = cur.fetchall()
    conn.close()

    return jsonify({"timestamp": ts, "planes": [dict(r) for r in rows]})


@app.route('/<path:filename>')        # <-- the new route, near the bottom
def serve_page(filename):
    return send_from_directory('.', filename)


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    wl = load_watchlist()
    print("=" * 55)
    print("  Plane Tracker — running at http://127.0.0.1:5000")
    print(f"  Watching   : {len(wl)} planes from {CSV_PATH}")
    print(f"  Database   : {DB_PATH}")
    print(f"  Poll gap   : {POLL_INTERVAL}s per plane")
    print(f"  Cycle wait : {CYCLE_WAIT}s between cycles")
    print(f"  Save every : {SAVE_INTERVAL}s")
    print("=" * 55)
    for reg, meta in wl.items():
        print(f"    {reg:12}  {meta['owner']}")
    print("=" * 55)

    poller = threading.Thread(target=polling_loop, daemon=True)
    poller.start()

    # use_reloader=False prevents the reloader from forking a second polling thread
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
