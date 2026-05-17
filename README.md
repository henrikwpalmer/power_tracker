# ✈  Plane Tracker — Setup Guide

A lightweight Python/Flask plane-tracking app with a CartoDB dark map,
click-to-inspect panel, and animated flight-path trails.

---

## Quick Start (Mock Data)

```bash
# 1. Install the only dependency
pip install -r requirements.txt

# 2. Run the server
python app.py

# 3. Open your browser
open http://127.0.0.1:5000
```

You'll see 6 mock European flights. Click any plane to open the detail
panel, then press **SHOW FLIGHT PATH** to draw its trail.

---

## File Structure

```
plane-tracker/
├── app.py            ← Flask backend (API routes + data logic)
├── index.html        ← Single-page frontend (Leaflet map + UI)
└── requirements.txt  ← Python dependencies
```

---

## Switching to Live ADS-B Exchange Data

### 1. Get an API key

ADS-B Exchange is available via **RapidAPI**:

1. Go to <https://rapidapi.com/adsbexchange/api/adsbexchange-com1>
2. Create a free RapidAPI account and subscribe to the **Basic** plan
   (free tier gives ~500 requests/month — enough for testing).
3. Copy your `X-RapidAPI-Key` from the dashboard.

### 2. Update `app.py`

Open `app.py` and change these two lines near the top:

```python
USE_REAL_API = True                  # was False
ADSB_API_KEY = "YOUR_KEY_HERE"       # paste your key
```

That's it — no other code changes needed.

### 3. Optional: adjust the search area

The default search is centred on Central Europe (lat 51.5, lon 10.0)
with a 250 NM radius. You can change the defaults in `fetch_real_planes()`,
or pass query params from the frontend:

```
/api/planes?lat=40.7&lon=-74.0&radius=150   ← New York area
```

---

## Extending the App

| Goal | Where to change |
|---|---|
| Different map style | Replace the tile URL in `index.html` (CartoDB also offers `light_all`, `dark_nolabels`, etc.) |
| Add filters (airline, altitude) | Add query params to `/api/planes` in `app.py` |
| Store history to DB | Replace `generate_mock_history()` with SQLite inserts |
| WebSocket live updates | Replace `setInterval` with a Socket.IO connection |
| Deploy to the web | Wrap with `gunicorn` and deploy to Fly.io or Railway |

---

## ADS-B Exchange API — Field Reference

| Field | Meaning |
|---|---|
| `hex` | ICAO 24-bit address (unique per aircraft) |
| `r` | Registration / tail number |
| `flight` | Callsign |
| `t` | Aircraft type (ICAO code, e.g. `B738`) |
| `ownOp` | Registered owner / operator |
| `orig` / `dest` | Origin / destination IATA codes |
| `lat` / `lon` | Current position |
| `alt_baro` | Barometric altitude in feet |
| `gs` | Ground speed in knots |
| `track` | True heading in degrees |
| `baro_rate` | Vertical speed in ft/min |
| `squawk` | Transponder squawk code |
