"""
=============================================================
  Plane Tracker — Flask Backend
  - Live positions via airplanes.live (free, no key needed)
  - Watchlist loaded from CSV with columns:
      Registration Code, Owner Name, Description
  - Polling strategy:
      • One API call every 5 seconds (respects 1 req/s limit)
      • Full watchlist cycle waits 5 minutes before repeating
      • Frontend reads from a per-plane cache — results appear
        on the map immediately as each plane is found, rather
        than waiting for the full cycle to complete
=============================================================
"""

from flask import Flask, jsonify, render_template_string
import urllib.request, json, csv, os, time, threading

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
ADSB_BASE_URL = "http://api.airplanes.live/v2"
CSV_PATH      = "PrivateJetDirectory.csv"

POLL_INTERVAL = 5      # seconds between each individual plane lookup
CYCLE_WAIT    = 300    # seconds to wait after a full watchlist cycle (5 min)


# ─────────────────────────────────────────────────────────────
#  SHARED CACHE
#
#  _plane_cache  — dict keyed by registration; each entry holds
#                  the latest result (found or missing) for that
#                  plane. Updated immediately when each plane is
#                  polled, so the frontend can show results one
#                  by one rather than waiting for the full cycle.
#
#  _cycle_meta   — metadata about the current/last cycle overall.
# ─────────────────────────────────────────────────────────────
_cache_lock  = threading.Lock()

_plane_cache = {}   # { "N628TS": { "status": "found"|"missing", "data": {...} } }

_cycle_meta  = {
    "last_updated": None,   # ISO string of last completed cycle
    "next_cycle":   None,   # ISO string of when next cycle starts
    "status":       "starting",   # "polling" | "waiting" | "starting"
    "current_reg":  None,   # registration currently being queried
}


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
#
#  airplanes.live returns a "trail" array in the response — a
#  list of recent position points, each with lat, lon, alt,
#  and a unix timestamp. This gives us a short flight path
#  (typically the last few minutes) for free with every lookup.
#
#  Trail point structure:
#    { "lat": float, "lon": float, "alt": int,
#      "spd": int, "hd": int, "ts": int }
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

    # Extract trail points — normalise field names for the frontend
    raw_trail = ac.get("trail", [])
    trail = []
    for pt in raw_trail:
        if pt.get("lat") is not None and pt.get("lon") is not None:
            trail.append({
                "lat": pt.get("lat"),
                "lon": pt.get("lon"),
                "alt": pt.get("alt", 0),
                "ts":  pt.get("ts",  0),
            })

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
        "trail":       trail,   # list of recent position points
    }


# ─────────────────────────────────────────────────────────────
#  BACKGROUND POLLING THREAD
#
#  Each time a plane is queried, _plane_cache is updated
#  immediately — found or missing. The frontend polls
#  /api/planes which assembles results from _plane_cache,
#  so each plane appears on the map as soon as it's checked
#  rather than at the end of the full cycle.
# ─────────────────────────────────────────────────────────────
def polling_loop():
    print("  [poller] Background polling thread started")

    while True:
        watchlist = load_watchlist()

        if not watchlist:
            print("  [poller] Watchlist is empty — retrying in 30 s")
            time.sleep(30)
            continue

        with _cache_lock:
            _cycle_meta["status"] = "polling"

        for reg, meta in watchlist.items():

            # Signal which plane is currently being queried
            with _cache_lock:
                _cycle_meta["current_reg"] = reg

            print(f"  [poll] Querying {reg} ({meta['owner']})...")
            plane = fetch_plane_by_reg(reg, meta)

            # ── Update the per-plane cache immediately ──────────
            # The frontend will see this result on its next poll,
            # without waiting for the rest of the watchlist.
            with _cache_lock:
                if plane:
                    print(f"  [poll] {reg} → found at {plane['lat']}, {plane['lon']}")
                    _plane_cache[reg] = {"status": "found",   "data": plane}
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

        # Full cycle complete — update timestamps
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
              f"{missing_count} missing. Next cycle at {next_iso}")

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
    Assembles the current state of every plane from _plane_cache.
    Because _plane_cache is updated one plane at a time as the
    poller runs, the frontend gets the latest known result for
    each plane immediately — found planes appear on the map as
    soon as they're polled, not at end of cycle.
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
    """
    GET /api/status
    Lightweight endpoint — just the poller state, no plane data.
    Used by the frontend to update the status dot/countdown
    without re-fetching the full plane list.
    """
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
    Returns the trail points stored in the cache for one plane.
    The trail comes from the airplanes.live response and covers
    the last few minutes of flight.
    """
    with _cache_lock:
        entry = _plane_cache.get(reg.upper())

    if not entry or entry["status"] != "found":
        return jsonify({"reg": reg, "trail": []})

    trail = entry["data"].get("trail", [])
    return jsonify({"reg": reg, "trail": trail})


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    wl = load_watchlist()
    print("=" * 55)
    print("  Plane Tracker — running at http://127.0.0.1:5000")
    print(f"  Watching {len(wl)} planes from {CSV_PATH}")
    for reg, meta in wl.items():
        print(f"    {reg:12}  {meta['owner']}")
    print(f"  Poll interval : {POLL_INTERVAL}s per plane")
    print(f"  Cycle wait    : {CYCLE_WAIT}s between cycles")
    print("=" * 55)

    poller = threading.Thread(target=polling_loop, daemon=True)
    poller.start()

    # use_reloader=False is required — the default reloader forks
    # the process which would start a second polling thread
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
