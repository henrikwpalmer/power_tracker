"""
Multi-target scraper for celebrityprivatejettracker.com
Scrapes multiple celebrity jet pages and writes all rows into one SQLite DB.

Schema:
    timestamp TEXT    -- date of the location record (YYYY-MM-DD)
    hex       TEXT    -- always 'n/a'
    tail      TEXT    -- aircraft tail number (e.g. N757AF)
    owner     TEXT    -- aircraft owner / celebrity name
    lat       REAL    -- airport latitude  (0.0 if unknown)
    lon       REAL    -- airport longitude (0.0 if unknown)
    altitude  INTEGER -- always 0
    speed     INTEGER -- always 0
    heading   INTEGER -- always 0

Usage:
    python scrape_jet_tracker.py
    # produces jet_locations.db in the current directory

    # Add or remove targets by editing the TARGETS list below.

Dependencies:
    pip install requests beautifulsoup4 lxml
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
# Each entry: (url, tail_number, owner_name)
# Add or remove rows freely.

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

# ── Settings ───────────────────────────────────────────────────────────────────

DB_FILE          = "./data/data_out/jet_locations.db"
AIRPORTS_CSV     = "./data/data_in/airports.csv"   # put the CSV in the same directory as this script
DELAY_BETWEEN_REQUESTS = 2.0        # seconds — be polite to the server

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Airport lookup ─────────────────────────────────────────────────────────────

def load_airport_coords(csv_path):
    """
    Load airports.csv (columns: iata, name, city, state, country, latitude, longitude)
    into a dict keyed by upper-cased IATA code.
    Also builds a K-prefix → entry mapping so that ICAO codes like KATL → ATL work.
    Returns dict: code -> (lat, lon)
    """
    coords = {}
    if not os.path.exists(csv_path):
        print(f"  ⚠  {csv_path} not found — falling back to hard-coded coords only.")
        return coords

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            iata = row.get("iata", "").strip().upper()
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (ValueError, KeyError):
                continue
            if iata:
                coords[iata] = (lat, lon)
                # Also register the K-prefixed ICAO variant for US airports
                if len(iata) == 3:
                    coords["K" + iata] = (lat, lon)

    print(f"  Loaded {len(coords)} airport entries from {csv_path}")
    return coords


# Hard-coded fallbacks for international / military / private airports
# not typically in IATA databases.
EXTRA_COORDS = {
    "TNCA": (12.5014,  -70.0152),
    "TNCB": (12.1310,  -68.2680),
    "MYNN": (25.0379,  -77.4662),
    "MYEH": (25.4799,  -76.6853),
    "MYGF": (26.5587,  -78.6956),
    "MHTG": (14.0608,  -87.2172),
    "MDPC": (18.5674,  -68.3634),
    "MDSD": (18.4297,  -69.6689),
    "TJSJ": (18.4394,  -66.0018),
    "SVMI": (10.6012,  -66.9913),
    "LGAV": (37.9364,   23.9445),
    "LFPG": (49.0097,    2.5479),
    "EGLL": (51.4775,   -0.4614),
    "EHAM": (52.3086,    4.7639),
    "EDDF": (50.0379,    8.5622),
    "LIRF": (41.8003,   12.2389),
    "LEMD": (40.4936,   -3.5668),
    "RJTT": (35.5533,  139.7811),
    "VHHH": (22.3080,  113.9185),
    "ZBAA": (40.0799,  116.6031),
    "OMDB": (25.2532,   55.3657),
    "OEJN": (21.6796,   39.1565),
    "OERK": (24.9576,   46.6988),
    "FAOR": (-26.1392,  28.2460),
    "DNMM": (6.5774,    3.3216),
    "KNHK": (38.2840,  -76.4104),
    "KMGE": (33.9131,  -84.5161),
    "KPBG": (44.6509,  -73.4683),
    "KBDR": (41.1635,  -73.1262),
}


def build_lookup(csv_path):
    """Merge CSV data with hard-coded extras. CSV takes precedence."""
    lookup = dict(EXTRA_COORDS)
    lookup.update(load_airport_coords(csv_path))
    return lookup


def normalize_code(raw):
    return re.sub(r"[^A-Z0-9]", "", raw.upper())


def find_coords(code, lookup):
    code = normalize_code(code)
    return lookup.get(code)


# ── HTTP fetch ─────────────────────────────────────────────────────────────────

def fetch_page(url, retries=3, delay=2.0):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            print(f"    attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(delay * attempt)
    raise RuntimeError(f"Could not fetch {url}")


# ── HTML parsing ───────────────────────────────────────────────────────────────

DATE_KEYWORDS    = ("date", "time", "timestamp", "day")
AIRPORT_KEYWORDS = ("airport", "icao", "iata", "location", "dest",
                    "origin", "from", "to", "arrived", "departed")


def extract_rows(html):
    """
    Try multiple strategies to pull (date_raw, airport_raw) pairs from the page.
    Returns list of dicts with keys: date_raw, airport_raw.
    """
    soup = BeautifulSoup(html, "lxml")
    rows = _try_table(soup)
    if not rows:
        rows = _try_divs(soup)
    return rows


def _try_table(soup):
    rows = []
    for table in soup.find_all("table"):
        # Collect all th text — they might be in thead or scattered in first tr
        all_th = table.find_all("th")
        headers = [th.get_text(strip=True).lower() for th in all_th]

        date_idx = next(
            (i for i, h in enumerate(headers) if any(k in h for k in DATE_KEYWORDS)),
            None,
        )
        airport_idx = next(
            (i for i, h in enumerate(headers) if any(k in h for k in AIRPORT_KEYWORDS)),
            None,
        )
        if date_idx is None or airport_idx is None:
            continue

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) <= max(date_idx, airport_idx):
                continue
            # Skip header rows
            if cells[date_idx].name == "th":
                continue
            date_raw    = cells[date_idx].get_text(strip=True)
            airport_raw = cells[airport_idx].get_text(strip=True)
            if date_raw and airport_raw:
                rows.append({"date_raw": date_raw, "airport_raw": airport_raw})

    return rows


def _try_divs(soup):
    """Regex-based fallback for JS-light or non-table layouts."""
    rows = []
    for div in soup.find_all(class_=re.compile(r"flight|location|row|entry|item", re.I)):
        text = div.get_text(" ", strip=True)
        date_m    = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", text)
        airport_m = re.search(r"\b([A-Z]{3,4})\b", text)
        if date_m and airport_m:
            rows.append({"date_raw": date_m.group(1), "airport_raw": airport_m.group(1)})
    return rows


# ── Date normalisation ─────────────────────────────────────────────────────────

DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y",
    "%B %d, %Y", "%b %d, %Y",
    "%d %B %Y",  "%d %b %Y",
    "%Y/%m/%d",
)


def parse_date(raw):
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw  # return as-is if no format matched


# ── Database ───────────────────────────────────────────────────────────────────

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            hex       TEXT,
            tail      TEXT,
            owner     TEXT,
            lat       REAL,
            lon       REAL,
            altitude  INTEGER,
            speed     INTEGER,
            heading   INTEGER
        )
    """)
    conn.commit()
    return conn


def insert_rows(conn, records):
    conn.executemany(
        """
        INSERT INTO locations (timestamp, hex, tail, owner, lat, lon, altitude, speed, heading)
        VALUES (:timestamp, :hex, :tail, :owner, :lat, :lon, :altitude, :speed, :heading)
        """,
        records,
    )
    conn.commit()


# ── Per-target scrape ──────────────────────────────────────────────────────────

def scrape_target(url, tail, owner, lookup):
    """Fetch one page and return a list of DB-ready dicts."""
    print(f"\n→ {owner} ({tail})")
    print(f"  {url}")

    html = fetch_page(url)
    raw_rows = extract_rows(html)

    if not raw_rows:
        print("  ⚠  No rows found (page may require JavaScript rendering).")
        return []

    records = []
    unknown = set()

    for row in raw_rows:
        timestamp   = parse_date(row["date_raw"])
        code        = normalize_code(row["airport_raw"])
        coords      = find_coords(code, lookup)

        if coords is None:
            unknown.add(code)
            lat, lon = 0.0, 0.0
        else:
            lat, lon = coords

        records.append({
            "timestamp": timestamp,
            "hex":       "n/a",
            "tail":      tail,
            "owner":     owner,
            "lat":       lat,
            "lon":       lon,
            "altitude":  0,
            "speed":     0,
            "heading":   0,
        })

    if unknown:
        print(f"  ⚠  Unknown airport codes (lat/lon = 0.0): {', '.join(sorted(unknown))}")

    print(f"  ✓  {len(records)} rows parsed")
    return records


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Resolve airports.csv — look next to this script first, then cwd
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    csv_path    = os.path.join(script_dir, AIRPORTS_CSV)
    if not os.path.exists(csv_path):
        csv_path = AIRPORTS_CSV  # fall back to cwd

    print("Loading airport coordinates …")
    lookup = build_lookup(csv_path)

    print(f"\nInitialising database: {DB_FILE}")
    conn = init_db(DB_FILE)

    total = 0
    for url, tail, owner in TARGETS:
        try:
            records = scrape_target(url, tail, owner, lookup)
            if records:
                insert_rows(conn, records)
                total += len(records)
        except RuntimeError as exc:
            print(f"  ✗  Skipping {owner}: {exc}")

        time.sleep(DELAY_BETWEEN_REQUESTS)

    conn.close()
    print(f"\n{'─'*50}")
    print(f"Done. Total rows written: {total}  →  {DB_FILE}")

    # Quick summary per owner
    conn2 = sqlite3.connect(DB_FILE)
    print(f"\n{'owner':<25} {'tail':<10} {'rows':>6}")
    print("─" * 44)
    for row in conn2.execute(
        "SELECT owner, tail, COUNT(*) FROM locations GROUP BY owner, tail ORDER BY owner"
    ):
        print(f"{row[0]:<25} {row[1]:<10} {row[2]:>6}")
    conn2.close()


# ── JavaScript fallback (uncomment if the site needs JS rendering) ─────────────
#
# Replace fetch_page() with this if you see "No rows found" for every target:
#
#   from selenium import webdriver
#   from selenium.webdriver.chrome.options import Options
#
#   def fetch_page(url, **_):
#       opts = Options()
#       opts.add_argument("--headless")
#       opts.add_argument("--no-sandbox")
#       opts.add_argument("--disable-dev-shm-usage")
#       driver = webdriver.Chrome(options=opts)
#       driver.get(url)
#       time.sleep(4)
#       html = driver.page_source
#       driver.quit()
#       return html
#
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
