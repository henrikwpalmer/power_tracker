"""
=============================================================
  Plane Tracker — Flask Backend
  Uses mock data by default. Swap in real ADS-B Exchange
  calls once you have an API key (see REAL API section).
=============================================================
"""

from flask import Flask, jsonify, render_template_string, request
import math, time, random

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
#  Set USE_REAL_API = True and add your key when you're ready.
# ─────────────────────────────────────────────────────────────
USE_REAL_API  = True           # Toggle to True after you get a key
ADSB_API_KEY  = ""
ADSB_BASE_URL = "https://adsbexchange-com1.p.rapidapi.com/v2"

WATCHED_PLANES = ["N8628", "N3200X", "N620JK"]

# ─────────────────────────────────────────────────────────────
#  REAL API HELPERS  (only called when USE_REAL_API = True)
# ─────────────────────────────────────────────────────────────
def fetch_real_planes(lat=51.5, lon=10.0, radius_nm=250):
    """
    Fetch live aircraft within `radius_nm` nautical miles of a
    centre point from ADS-B Exchange via RapidAPI.

    Sign up at: https://rapidapi.com/adsbexchange/api/adsbexchange-com1
    Then set ADSB_API_KEY above.
    """
    import urllib.request, json
    url = f"{ADSB_BASE_URL}/lat/{lat}/lon/{lon}/dist/{radius_nm}/"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key":  ADSB_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = json.loads(resp.read())

    planes = []
    for ac in raw.get("ac", []):
        planes.append({
            "hex":        ac.get("hex", ""),
            "tail":       ac.get("r", "N/A"),       # registration
            "callsign":   ac.get("flight", "").strip(),
            "operator":   ac.get("ownOp", "Unknown"),
            "type":       ac.get("t", "Unknown"),
            "origin":     ac.get("orig", "?"),
            "destination":ac.get("dest", "?"),
            "lat":        ac.get("lat", 0),
            "lon":        ac.get("lon", 0),
            "altitude":   ac.get("alt_baro", 0),
            "speed":      ac.get("gs", 0),
            "heading":    ac.get("track", 0),
            "vertical":   ac.get("baro_rate", 0),
            "squawk":     ac.get("squawk", ""),
        })
    return planes

def fetch_by_registration(tail):
    """
    Fetch a single aircraft by its registration/tail number.
    e.g. tail = "N8737L"
    """
    import urllib.request, json
    url = f"{ADSB_BASE_URL}/registration/{tail}/"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key":  ADSB_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def fetch_by_hex(hex_code):
    """
    Fetch a single aircraft by its ICAO hex ID.
    e.g. hex_code = "a6a8f5"
    """
    import urllib.request, json
    url = f"{ADSB_BASE_URL}/hex/{hex_code}/"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key":  ADSB_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())
    
def fetch_real_history(hex_code):
    """
    Fetch recent position history for a single aircraft.
    ADS-B Exchange provides up to 60 s of recent track data.
    """
    import urllib.request, json
    url = f"{ADSB_BASE_URL}/hex/{hex_code}/"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key":  ADSB_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = json.loads(resp.read())

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
    """Serve the single-page frontend (loaded from index.html)."""
    with open("index.html", "r", encoding="utf-8") as f:
        return render_template_string(f.read())


@app.route("/api/planes")
def api_planes():
    """
    GET /api/planes
    Returns a JSON list of currently tracked aircraft.
    Query params (only used with real API):
      lat      — centre latitude  (default 51.5)
      lon      — centre longitude (default 10.0)
      radius   — search radius in NM (default 250)
    """
    if USE_REAL_API:
        lat    = float(request.args.get("lat",    51.5))
        lon    = float(request.args.get("lon",    10.0))
        radius = float(request.args.get("radius", 250))
        planes = fetch_real_planes(lat, lon, radius)
    else:
        # Animate mock planes a little so the map feels "live"
        planes = []
        t = time.time() % (3600)          # cycle every hour
        for p in MOCK_PLANES:
            clone = dict(p)
            # Nudge position forward along heading
            rad   = math.radians(p["heading"])
            clone["lat"] = round(p["lat"] + math.cos(rad) * t * 0.00003, 5)
            clone["lon"] = round(p["lon"] + math.sin(rad) * t * 0.00003, 5)
            planes.append(clone)

    return jsonify({"planes": planes, "source": "real" if USE_REAL_API else "mock"})


@app.route("/api/history/<hex_code>")
def api_history(hex_code):
    """
    GET /api/history/<hex>
    Returns a list of past position points for one aircraft.
    """
    if USE_REAL_API:
        trail = fetch_real_history(hex_code)
    else:
        plane = next((p for p in MOCK_PLANES if p["hex"] == hex_code), None)
        trail = generate_mock_history(plane) if plane else []

    return jsonify({"hex": hex_code, "trail": trail})


for tail in WATCHED_PLANES:
    data = fetch_by_registration(tail)
    # parse and add to your planes list
    

# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Plane Tracker — running at http://127.0.0.1:5000")
    print(f"  Data source : {'REAL ADS-B Exchange API' if USE_REAL_API else 'MOCK (test) data'}")
    print("=" * 55)
    # debug=True gives live reloading while you develop
    app.run(debug=True, host="0.0.0.0", port=5000)
