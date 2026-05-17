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

Currently just 3 planes being tracked.

---

## File Structure

```
plane-tracker/
├── app.py            ← Flask backend (API routes + data logic)
├── index.html        ← Single-page frontend (Leaflet map + UI)
└── requirements.txt  ← Python dependencies
```

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
