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


## Airplane.live API — Field Reference
Endpoints

| Field | Meaning |
|---|---|
| `hex` | ICAO 24-bit address (unique per aircraft) |
| `reg` | Registration / tail number |
| `callsign` | Callsign |
| `type` | Aircraft type (ICAO code, e.g. `B738`) |
| `mil` | returns all aircraft tagged as military |
| `ladd` | returns aircraft tagged as LADD |
| `lat` / `lon` | Current position |
| `alt_baro` | Barometric altitude in feet |
| `gs` | Ground speed in knots |
| `track` | True heading in degrees |
| `baro_rate` | Vertical speed in ft/min |
| `squawk` | Transponder squawk code |
