"""
STEP 2: Merge flight data with airports.csv to add lat/lon.
Reads:
    ./data/data_out/flights_raw.db
    airports.csv  (path set below — use an absolute path to avoid confusion)
Outputs:
    ./data/data_out/jet_locations.db   (SQLite, used by visualizer)
    ./data/data_out/jet_locations.csv  (for inspection)

Schema of output:
    id, timestamp, tail, owner, airport, airport_name, lat, lon, leg
"""

import csv
import os
import sqlite3

# ── CONFIGURE THESE PATHS ──────────────────────────────────────────────────────

RAW_DB     = "./data/data_out/flights_raw.db"
OUT_DB     = "./data/data_out/jet_locations.db"
OUT_CSV    = "./data/data_out/jet_locations.csv"

# Set this to the full absolute path of your airports.csv
AIRPORTS_CSV = "./data/data_in/airports.csv"

# ── Load airports.csv ──────────────────────────────────────────────────────────

def load_airports(path):
    """
    Returns dict: IATA_CODE (uppercase) -> (lat, lon, full_name)
    Expects columns: iata, name, city, state, country, latitude, longitude
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"\n\nCannot find airports.csv at: {os.path.abspath(path)}\n"
            f"Set the AIRPORTS_CSV variable at the top of this script to the "
            f"correct absolute path.\n"
        )

    lookup = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        print(f"CSV columns found: {reader.fieldnames}")

        for row in reader:
            code = row["iata"].strip().upper()
            name = row["name"].strip()
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (ValueError, KeyError):
                continue
            if code:
                lookup[code] = (lat, lon, name)

    print(f"Loaded {len(lookup)} airports from {path}")
    return lookup

# ── Merge ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Loading airports from: {os.path.abspath(AIRPORTS_CSV)}")
    airports = load_airports(AIRPORTS_CSV)

    # Read raw flights
    raw_conn = sqlite3.connect(RAW_DB)
    raw_conn.row_factory = sqlite3.Row
    raw_rows = raw_conn.execute("SELECT * FROM flights_raw ORDER BY id").fetchall()
    raw_conn.close()
    print(f"Raw flight records: {len(raw_rows)}")

    # Build merged rows
    merged     = []
    unmatched  = set()

    for r in raw_rows:
        code = r["airport"].strip().upper()
        if code in airports:
            lat, lon, csv_name = airports[code]
        else:
            unmatched.add(code)
            lat, lon, csv_name = 0.0, 0.0, r["airport_name"]

        merged.append({
            "timestamp":    r["timestamp"],
            "tail":         r["tail"],
            "owner":        r["owner"],
            "airport":      code,
            "airport_name": csv_name,   # prefer CSV name (cleaner)
            "lat":          lat,
            "lon":          lon,
            "leg":          r["leg"],
        })

    # Report
    matched_count = sum(1 for m in merged if m["lat"] != 0.0 or m["lon"] != 0.0)
    print(f"\nMatched   : {matched_count} / {len(merged)} records have coordinates")
    print(f"Unmatched : {len(unmatched)} airport codes")
    if unmatched:
        print(f"  Codes with no lat/lon: {', '.join(sorted(unmatched))}")
        print(f"  → Add these to airports.csv or the EXTRA_COORDS dict below.")

    # Write output DB
    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    out_conn = sqlite3.connect(OUT_DB)
    out_conn.execute("DROP TABLE IF EXISTS locations")
    out_conn.execute("""
        CREATE TABLE locations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT,
            tail         TEXT,
            owner        TEXT,
            airport      TEXT,
            airport_name TEXT,
            lat          REAL,
            lon          REAL,
            leg          TEXT
        )
    """)
    out_conn.executemany("""
        INSERT INTO locations (timestamp, tail, owner, airport, airport_name, lat, lon, leg)
        VALUES (:timestamp, :tail, :owner, :airport, :airport_name, :lat, :lon, :leg)
    """, merged)
    out_conn.commit()
    out_conn.close()

    # Write output CSV
    fields = ["timestamp", "tail", "owner", "airport", "airport_name", "lat", "lon", "leg"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(merged)

    print(f"\nDB  → {OUT_DB}")
    print(f"CSV → {OUT_CSV}  (open to inspect)")

    # Per-owner summary
    from collections import Counter
    owner_counts = Counter(m["owner"] for m in merged if m["lat"] != 0.0)
    print(f"\n{'owner':<25} {'records with coords':>20}")
    print("─" * 47)
    for owner, count in sorted(owner_counts.items()):
        print(f"{owner:<25} {count:>20}")

if __name__ == "__main__":
    main()
