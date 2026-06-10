"""
STEP 1: Scrape celebrityprivatejettracker.com
Stores raw flight data with airport codes but NO lat/lon.
Outputs:
    ./data/data_out/flights_raw.db   (SQLite)
    ./data/data_out/flights_raw.csv  (for inspection)

Schema:
    id           INTEGER
    timestamp    TEXT     -- YYYY-MM-DD
    tail         TEXT     -- e.g. N757AF
    owner        TEXT     -- e.g. Donald Trump
    airport      TEXT     -- IATA code extracted from site, e.g. PBI
    airport_name TEXT     -- full name from site, e.g. Palm Beach International Airport
    leg          TEXT     -- 'departure' or 'arrival'

Dependencies: pip install requests beautifulsoup4 lxml
"""

import csv
import os
import re
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ── Targets ────────────────────────────────────────────────────────────────────
TARGETS = [
    ("https://celebrityprivatejettracker.com/donald-trump-n757af/",  "N757AF", "Donald Trump"),
    ("https://celebrityprivatejettracker.com/ron-desantis-n943fl/",  "N943FL", "Ron DeSantis"),
    ("https://celebrityprivatejettracker.com/larry-ellison-n817gs/", "N817GS", "Larry Ellison"),
    ("https://celebrityprivatejettracker.com/eric-schmidt-n652we/",  "N652WE", "Eric Schmidt"),
    ("https://celebrityprivatejettracker.com/peter-thiel-n878db/",   "N878DB", "Peter Thiel"),
    ("https://celebrityprivatejettracker.com/google-n10xg/",         "N10XG",  "Google"),
    ("https://celebrityprivatejettracker.com/mark-zuckerberg-n68885/", "N68885", "Mark Zuckerberg"),
    ("https://celebrityprivatejettracker.com/michael-bloomberg-n5mv/", "N5MV", "Michael Bloomberg"),
    ("https://celebrityprivatejettracker.com/rupert-murdoch-n898nc/", "N898NC", "Rupert Murdoch"),
    ("https://celebrityprivatejettracker.com/elon-musk-n628ts/", "N628TS", "Elon Musk"),
    ("https://celebrityprivatejettracker.com/michael-bloomberg-n47eg/", "N47EG", "Michael Bloomberg"),
    ("https://celebrityprivatejettracker.com/michael-bloomberg-n8ag/", "N8AG", "Michael Bloomberg"),
    ("https://celebrityprivatejettracker.com/bill-gates-n887wm/", "N887WM", "Bill Gates"),
    ("https://celebrityprivatejettracker.com/bill-gates-n194wm/", "N194WM", "Bill Gates"),
    ("https://celebrityprivatejettracker.com/jeff-bezos-n758pb/", "N758PB", "Jeff Bezos")
]

OUT_DIR  = "./data/data_out"
DB_FILE  = os.path.join(OUT_DIR, "flights_raw.db")
CSV_FILE = os.path.join(OUT_DIR, "flights_raw.csv")
DELAY    = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Parsing helpers ────────────────────────────────────────────────────────────

# Matches "Palm Beach International Airport (PBI)West Palm Beach, Florida"
AIRPORT_RE = re.compile(r'^(.+?)\s*\(([A-Z]{2,4})\)', re.IGNORECASE)

def parse_airport_cell(text):
    """Return (iata_code, airport_name) or (None, raw_text)."""
    text = text.strip()
    m = AIRPORT_RE.match(text)
    if m:
        return m.group(2).upper(), m.group(1).strip()
    return None, text

DATE_FORMATS = (
    "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
)

def parse_date(raw):
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw

def fetch_page(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            print(f"    attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Could not fetch {url}")

def scrape_target(url, tail, owner):
    print(f"\n→ {owner} ({tail})\n  {url}")
    html  = fetch_page(url)
    soup  = BeautifulSoup(html, "lxml")
    rows  = []

    for table in soup.find_all("table"):
        headers = [re.sub(r'\s+', ' ', th.get_text(strip=True)).upper()
                   for th in table.find_all("th")]

        if not any("FLIGHT" in h and "DATE" in h for h in headers):
            continue
        if not any("DEPARTING" in h for h in headers):
            continue

        date_idx = next((i for i, h in enumerate(headers) if "FLIGHT" in h and "DATE" in h), None)
        dep_idx  = next((i for i, h in enumerate(headers) if "DEPARTING" in h), None)
        arr_idx  = next((i for i, h in enumerate(headers) if "ARRIVING" in h), None)

        if None in (date_idx, dep_idx, arr_idx):
            continue

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) <= max(date_idx, dep_idx, arr_idx):
                continue
            if cells[date_idx].name == "th":
                continue

            date_raw = cells[date_idx].get_text(strip=True)
            dep_raw  = cells[dep_idx].get_text(strip=True)
            arr_raw  = cells[arr_idx].get_text(strip=True)

            # Skip footnote / summary rows
            if len(date_raw) > 20 or not date_raw:
                continue

            timestamp = parse_date(date_raw)
            dep_code, dep_name = parse_airport_cell(dep_raw)
            arr_code, arr_name = parse_airport_cell(arr_raw)

            for code, name, leg in (
                (dep_code, dep_name, "departure"),
                (arr_code, arr_name, "arrival"),
            ):
                if code is None:
                    continue
                rows.append({
                    "timestamp":    timestamp,
                    "tail":         tail,
                    "owner":        owner,
                    "airport":      code,
                    "airport_name": name,
                    "leg":          leg,
                })
        break  # found our table

    print(f"  ✓  {len(rows)} records")
    return rows

# ── DB / CSV output ────────────────────────────────────────────────────────────

def init_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE IF EXISTS flights_raw")
    conn.execute("""
        CREATE TABLE flights_raw (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT,
            tail         TEXT,
            owner        TEXT,
            airport      TEXT,
            airport_name TEXT,
            leg          TEXT
        )
    """)
    conn.commit()
    return conn

def write_csv(all_rows, path):
    fields = ["timestamp", "tail", "owner", "airport", "airport_name", "leg"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    conn      = init_db(DB_FILE)
    all_rows  = []

    for url, tail, owner in TARGETS:
        try:
            rows = scrape_target(url, tail, owner)
            if rows:
                conn.executemany("""
                    INSERT INTO flights_raw (timestamp, tail, owner, airport, airport_name, leg)
                    VALUES (:timestamp, :tail, :owner, :airport, :airport_name, :leg)
                """, rows)
                conn.commit()
                all_rows.extend(rows)
        except RuntimeError as exc:
            print(f"  ✗  Skipping {owner}: {exc}")
        time.sleep(DELAY)

    conn.close()
    write_csv(all_rows, CSV_FILE)

    print(f"\n{'─'*50}")
    print(f"Total records : {len(all_rows)}")
    print(f"DB  → {DB_FILE}")
    print(f"CSV → {CSV_FILE}  (open this to inspect)")

    # Unique airport codes found
    codes = sorted(set(r["airport"] for r in all_rows))
    print(f"\n{len(codes)} unique airport codes found:")
    print(", ".join(codes))

if __name__ == "__main__":
    main()
