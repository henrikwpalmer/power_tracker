"""
=============================================================
  Billionaire Tracker — Flask Backend
  Tracks a specific watchlist of planes by registration.
=============================================================
"""

from flask import Flask, jsonify, render_template_string, request
import math, time, random, urllib.request, json

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
USE_REAL_API  = True
ADSB_API_KEY  = ""                  # ← paste API key here
ADSB_BASE_URL = "https://adsbexchange-com1.p.rapidapi.com/v2"

# ─────────────────────────────────────────────────────────────
#  WATCHLIST — add or remove tail numbers here
# ─────────────────────────────────────────────────────────────
WATCHED_PLANES = ["N8628", "N3200X", "N620JK"]


# ─────────────────────────────────────────────────────────────
#  SHARED HEADERS — reused by every API call
# ─────────────────────────────────────────────────────────────
def adsb_headers():
    return {
        "X-RapidAPI-Key":  ADSB_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    }


# ─────────────────────────────────────────────────────────────
#  FETCH WATCHLIST
#  Loops over WATCHED_PLANES, queries each by registration,
#  and returns a normalised list ready for the frontend.
# ─────────────────────────────────────────────────────────────
def fetch_watched_planes():
    planes = []

    for tail in WATCHED_PLANES:
        url = f"{ADSB_BASE_URL}/registration/{tail}/"
        req = urllib.request.Request(url, headers=adsb_headers())

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = json.loads(resp.read())

            # The API returns a list under "ac" — take the first match
            ac_list = raw.get("ac", [])
            if not ac_list:
                print(f"  [watchlist] No data returned for {tail}")
                continue

            ac = ac_list[0]

            # Normalise fields into the same shape the frontend expects
            planes.append({
                "hex":         ac.get("hex", ""),
                "tail":        ac.get("r", tail),           # registration
                "callsign":    ac.get("flight", "").strip(),
                "operator":    ac.get("ownOp", "Unknown"),
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
            print(f"  [watchlist] {tail} → found at {ac.get('lat')}, {ac.get('lon')}")

        except Exception as e:
            # Don't crash if one plane is unavailable — just skip it
            print(f"  [watchlist] Failed to fetch {tail}: {e}")
            continue

    return planes


# ─────────────────────────────────────────────────────────────
#  FETCH HISTORY
#  Pulls the recent trail for a single plane by hex ID.
#  ADS-B Exchange includes trail data in the /hex/ endpoint.
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
    """Serve the single-page frontend."""
    with open("index.html", "r", encoding="utf-8") as f:
        return render_template_string(f.read())


@app.route("/api/planes")
def api_planes():
    """
    GET /api/planes
    Returns positions for every plane in WATCHED_PLANES.
    """
    planes = fetch_watched_planes()
    return jsonify({"planes": planes, "source": "real"})


@app.route("/api/history/<hex_code>")
def api_history(hex_code):
    """
    GET /api/history/<hex>
    Returns position trail for one aircraft.
    """
    trail = fetch_real_history(hex_code)
    return jsonify({"hex": hex_code, "trail": trail})


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Billionaire Tracker — running at http://127.0.0.1:5000")
    print(f"  Watching : {', '.join(WATCHED_PLANES)}")
    print("=" * 55)
    app.run(debug=True, host="0.0.0.0", port=5000)
