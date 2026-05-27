"""
=============================================================
  Plane Tracker — Flask Backend
  - Live positions via ADS-B Exchange (RapidAPI)
  - Last known position via OpenSky Network (free, no key needed)
  - Watchlist loaded from CSV with columns:
      Registration Code, Owner Name, Description, ICAO
=============================================================
"""

from flask import Flask, jsonify, render_template_string, request
import urllib.request, urllib.parse, json, csv, os, time
import sqlite3


# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
USE_REAL_API  = True
ADSB_API_KEY  = "1c1f5c9d60msh15ac82f4928dc9ap163224jsn5b0358a4d923"                  # ← paste your RapidAPI key here
ADSB_BASE_URL = "https://adsbexchange-com1.p.rapidapi.com/v2"

# OpenSky is free and requires no API key for anonymous access.
# Rate limit: ~10 requests per 10 seconds for anonymous users.
OPENSKY_BASE_URL = "https://opensky-network.org/api"

CSV_PATH = "PrivateJetDirectory.csv"

LAST_SAVE = 0
SAVE_INTERVAL = 300   # 300 sec = 5 min

# ─────────────────────────────────────────────────────────────
#  DATABASE SETUP
# ─────────────────────────────────────────────────────────────

DB_PATH = "planes.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            hex TEXT,
            tail TEXT,
            owner TEXT,
            lat REAL,
            lon REAL,
            altitude INTEGER,
            speed REAL,
            heading REAL
        )
    """)

    conn.commit()
    conn.close()

# ─────────────────────────────────────────────────────────────
#  SAVE SNAPSHOT
# ─────────────────────────────────────────────────────────────

def save_snapshot(plane):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO positions (
            timestamp,
            hex,
            tail,
            owner,
            lat,
            lon,
            altitude,
            speed,
            heading
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(time.time()),
        plane["hex"],
        plane["tail"],
        plane["owner"],
        plane["lat"],
        plane["lon"],
        plane["altitude"],
        plane["speed"],
        plane["heading"],
    ))

    conn.commit()
    conn.close()

app = Flask(__name__)



# ─────────────────────────────────────────────────────────────
#  LOAD WATCHLIST FROM CSV
#  Expects columns: Registration Code, Owner Name, Description, ICAO
#  ICAO is the 6-character hex transponder address, e.g. "a835af"
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
                    # ICAO hex — lowercase, strip whitespace
                    "icao":        row.get("ICAO", "").strip().lower(),
                }

    print(f"  [csv] Loaded {len(watchlist)} planes from {CSV_PATH}")
    return watchlist


# ─────────────────────────────────────────────────────────────
#  SHARED ADSB HEADERS
# ─────────────────────────────────────────────────────────────
def adsb_headers():
    return {
        "X-RapidAPI-Key":  ADSB_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    }


# ─────────────────────────────────────────────────────────────
#  FETCH LIVE PLANES (ADS-B Exchange)
#  Returns (found, missing) where missing entries include the
#  ICAO hex so the frontend can request last-known positions.
# ─────────────────────────────────────────────────────────────
def fetch_watched_planes(watchlist):
    global LAST_SAVE
    should_save = (time.time() - LAST_SAVE) > SAVE_INTERVAL


    found   = []
    missing = []

    for reg, meta in watchlist.items():
        url = f"{ADSB_BASE_URL}/registration/{reg}/"
        req = urllib.request.Request(url, headers=adsb_headers())

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = json.loads(resp.read())

            ac_list = raw.get("ac", [])

            if not ac_list:
                print(f"  [watchlist] {reg} ({meta['owner']}) — no signal")
                missing.append({
                    "tail":        reg,
                    "owner":       meta["owner"],
                    "description": meta["description"],
                    "icao":        meta["icao"],
                })
                continue

            ac = ac_list[0]
            found.append({
                "hex":         ac.get("hex", ""),
                "tail":        ac.get("r", reg),
                "callsign":    ac.get("flight", "").strip(),
                "owner":       meta["owner"],
                "description": meta["description"],
                "icao":        meta["icao"],
                "operator":    ac.get("ownOp", meta["owner"]),
                "type":        ac.get("t", "Unknown"),
                "origin":      ac.get("orig", "?"),
                "destination": ac.get("dest", "?"),
                "lat":         ac.get("lat", 0),
                "lon":         ac.get("lon", 0),
                "altitude":    ac.get("alt_baro", 0),
                "speed":       ac.get("gs", 0),
                "heading":     ac.get("track", 0),
                "vertical":    ac.get("baro_rate", 0),
                "squawk":      ac.get("squawk", ""),
            })
            if should_save:
                save_snapshot(found[-1])
            print(f"  [watchlist] {reg} ({meta['owner']}) → {ac.get('lat')}, {ac.get('lon')}")


        except Exception as e:
            print(f"  [watchlist] {reg} ({meta['owner']}) — error: {e}")
            missing.append({
                "tail":        reg,
                "owner":       meta["owner"],
                "description": meta["description"],
                "icao":        meta["icao"],
            })

        if should_save: ## might be in wrong place   
            LAST_SAVE = time.time()

    return found, missing


# ─────────────────────────────────────────────────────────────
#  FETCH LAST KNOWN POSITION (OpenSky Network)
#
#  OpenSky's /states/all endpoint accepts an ICAO hex and
#  returns the most recent state vector it has on record —
#  even if the plane landed hours or days ago. This is the
#  "last seen" snapshot, not a live position.
#
#  Endpoint: GET /states/all?icao24={hex}
#  Returns:  list of state vectors, each with:
#    [icao24, callsign, origin_country, time_position,
#     last_contact, longitude, latitude, baro_altitude,
#     on_ground, velocity, true_track, vertical_rate,
#     sensors, geo_altitude, squawk, spi, position_source]
#
#  Anonymous rate limit: ~10 req / 10 s. We cache results for
#  60 seconds to avoid hammering the API on map refreshes.
# ─────────────────────────────────────────────────────────────

# Simple in-memory cache: { icao: { "ts": unix_time, "data": {...} } }
_opensky_cache = {}
CACHE_TTL = 60  # seconds


def fetch_last_known_position(icao_hex):
    """
    Query OpenSky for the last recorded state vector for one aircraft.
    Returns a dict with lat, lon, altitude, heading, speed, timestamp,
    or None if nothing is found.
    """
    if not icao_hex:
        return None

    # Return cached result if fresh enough
    cached = _opensky_cache.get(icao_hex)
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        return cached["data"]

    url = f"{OPENSKY_BASE_URL}/states/all?icao24={icao_hex.lower()}"
    req = urllib.request.Request(url, headers={"User-Agent": "PlaneTracker/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())
    except Exception as e:
        print(f"  [opensky] Failed for {icao_hex}: {e}")
        return None

    states = raw.get("states")
    if not states:
        print(f"  [opensky] No state vector found for {icao_hex}")
        return None

    # State vector field indices (per OpenSky docs)
    sv = states[0]
    lat         = sv[6]   # latitude
    lon         = sv[5]   # longitude
    baro_alt    = sv[7]   # barometric altitude in metres (None if unknown)
    on_ground   = sv[8]   # boolean
    speed       = sv[9]   # m/s
    heading     = sv[10]  # degrees
    last_contact= sv[4]   # unix timestamp of last ADS-B message

    if lat is None or lon is None:
        return None

    # Convert metres → feet for consistency with the rest of the app
    alt_ft = round(baro_alt * 3.28084) if baro_alt else 0
    # Convert m/s → knots
    speed_kts = round(speed * 1.94384) if speed else 0

    result = {
        "icao":         icao_hex,
        "lat":          lat,
        "lon":          lon,
        "altitude":     alt_ft,
        "speed":        speed_kts,
        "heading":      round(heading) if heading else 0,
        "on_ground":    on_ground,
        "last_contact": last_contact,  # unix timestamp
    }

    # Cache it
    _opensky_cache[icao_hex] = {"ts": time.time(), "data": result}
    return result


# ─────────────────────────────────────────────────────────────
#  FETCH FLIGHT HISTORY (ADS-B Exchange)
# ─────────────────────────────────────────────────────────────
def fetch_real_history(hex_code):
    url = f"{ADSB_BASE_URL}/hex/{hex_code}/"
    req = urllib.request.Request(url, headers=adsb_headers())

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read())
    except Exception as e:
        print(f"  [history] Failed for hex {hex_code}: {e}")
        return []

    trail = []
    for pos in raw.get("ac", [{}])[0].get("trail", []):
        trail.append({
            "lat": pos.get("lat"),
            "lon": pos.get("lon"),
            "alt": pos.get("alt"),
            "ts":  pos.get("ts"),
        })
    return trail


# ─────────────────────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return render_template_string(f.read())


@app.route("/api/planes")
def api_planes():
    """
    GET /api/planes
    Returns:
      planes   — aircraft with live ADS-B positions
      missing  — aircraft not currently transmitting (includes icao for last-known lookup)
    """
    watchlist      = load_watchlist()
    found, missing = fetch_watched_planes(watchlist)
    return jsonify({"planes": found, "missing": missing, "source": "real"})


@app.route("/api/history/<hex_code>")
def api_history(hex_code):
    """GET /api/history/<hex> — recent ADS-B trail from ADS-B Exchange."""
    trail = fetch_real_history(hex_code)
    return jsonify({"hex": hex_code, "trail": trail})


@app.route("/api/last_known/<icao_hex>")
def api_last_known(icao_hex):
    """
    GET /api/last_known/<icao>
    Returns the most recent state vector OpenSky has for this aircraft.
    Used to show a grey "last seen" pin for grounded/offline planes.
    """
    position = fetch_last_known_position(icao_hex)
    if position:
        return jsonify({"found": True,  "position": position})
    else:
        return jsonify({"found": False, "position": None})


@app.route("/api/history_db/<tail>")
def api_history_db(tail):

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()

    # Last 24 hours
    since = int(time.time()) - 24 * 60 * 60

    cur.execute("""
        SELECT *
        FROM positions
        WHERE tail = ?
        AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (tail, since))

    rows = cur.fetchall()
    conn.close()

    history = [dict(row) for row in rows]

    return jsonify({
        "tail": tail,
        "history": history
    })



@app.route("/api/snapshot")
def api_snapshot():

    ts = request.args.get("ts", type=int)

    if not ts:
        return jsonify({"planes": []})

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # For each aircraft:
    # find the closest datapoint BEFORE the selected timestamp
    cur.execute("""
        SELECT p1.*
        FROM positions p1
        INNER JOIN (
            SELECT tail, MAX(timestamp) AS max_ts
            FROM positions
            WHERE timestamp <= ?
            GROUP BY tail
        ) p2
        ON p1.tail = p2.tail
        AND p1.timestamp = p2.max_ts
    """, (ts,))

    rows = cur.fetchall()
    conn.close()

    return jsonify({
        "timestamp": ts,
        "planes": [dict(r) for r in rows]
    })

# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    wl = load_watchlist()
    print("=" * 55)
    print("  Plane Tracker — running at http://127.0.0.1:5050")
    print(f"  Watching {len(wl)} planes from {CSV_PATH}")
    for reg, meta in wl.items():
        icao_str = f"  ICAO: {meta['icao']}" if meta['icao'] else "  ICAO: not set"
        print(f"    {reg:12}  {meta['owner']:20} {icao_str}")
    print("=" * 55)
    app.run(debug=True, host="0.0.0.0", port=5050)
