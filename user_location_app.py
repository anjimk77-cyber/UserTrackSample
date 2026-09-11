"""
User Location Tracker - Streamlit App
-------------------------------------------
STANDALONE app. Does NOT modify app.py (the Farm Map app) in any way —
this is a separate script/page that adds a new capability:

  1. A user types their Name and captures their CURRENT browser location
     (via the streamlit-js-eval package, which asks the browser for GPS/
     network location permission) — or enters lat/lon manually as a
     fallback if they decline the permission prompt or are on a device
     without geolocation.
  2. That (Name, Latitude, Longitude, Timestamp) row is saved to a new
     "UserLocations" worksheet in the SAME private Google Sheet that the
     manager app already uses for WaterQualityData (reusing the exact
     same [gcp_service_account] / [gsheet] secrets — no new sheet has to
     be created by hand; this app creates the "UserLocations" tab itself
     the first time it runs, if it doesn't already exist).
  3. The map at the bottom shows BOTH the existing farm locations (read
     the same public CSV that app.py reads — read-only, untouched) AND
     every saved user location (a distinct red pin), so you can see a
     user's saved spot together with all the farms on one map.

Nothing about the original Locations sheet, the Sales sheet, or app.py's
logic is touched or written to — this app only reads the farm CSV for
display and writes to its own new "UserLocations" tab.

Local run:
    pip install streamlit folium streamlit-folium streamlit-js-eval gspread google-auth pandas
    streamlit run user_location_app.py

    Needs the same `.streamlit/secrets.toml` as the manager app / app.py's
    Pond Layout feature:
        [gcp_service_account]
        ... (same service-account JSON fields) ...

        [gsheet]
        sheet_id = "..."   # same WaterQualityData spreadsheet key
    The service account must have Editor access on that spreadsheet (it
    already needs this for WaterQualityData writes in the manager app).

Deploy:
    Same as app.py — push to a GitHub repo and deploy on
    https://share.streamlit.io, with the same secrets configured.
"""

import re
from datetime import datetime

import pandas as pd
import streamlit as st
import folium
import streamlit.components.v1 as components

import gspread
from google.oauth2.service_account import Credentials

try:
    from streamlit_js_eval import get_geolocation
    _GEO_AVAILABLE = True
except ImportError:
    _GEO_AVAILABLE = False

# ============================================================
# CONFIG
# ============================================================
# Read-only: the same public farm-locations CSV that app.py uses, so this
# app's map can show farms + the new user pin together. Nothing here
# writes back to this sheet.
LOCATIONS_SHEET_ID = "1v2qTD5iUtdjFTixt9VZ1vM0dZPnyEVz4AYHtILVJi0A"
LOCATIONS_GID = "0"
LOCATIONS_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{LOCATIONS_SHEET_ID}"
    f"/export?format=csv&gid={LOCATIONS_GID}"
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
USERLOC_WORKSHEET_NAME = "UserLocations"
USERLOC_HEADERS = ["Name", "Latitude", "Longitude", "Timestamp"]

st.set_page_config(page_title="Add My Location", page_icon="📍", layout="wide")
st.title("📍 Add Your Location")


# ============================================================
# GOOGLE SHEET HELPERS (new "UserLocations" tab only)
# ============================================================
def _gsheet_configured():
    return "gcp_service_account" in st.secrets and "gsheet" in st.secrets and "sheet_id" in st.secrets["gsheet"]


@st.cache_resource(show_spinner=False)
def get_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def get_userloc_worksheet():
    """Opens the UserLocations tab, creating it (with headers) the first
    time this app is ever run against this spreadsheet."""
    client = get_client()
    sheet_id = st.secrets["gsheet"]["sheet_id"]
    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(USERLOC_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=USERLOC_WORKSHEET_NAME, rows=200, cols=len(USERLOC_HEADERS))
        ws.append_row(USERLOC_HEADERS)
    return ws


@st.cache_data(ttl=60, show_spinner="Loading saved locations...")
def load_user_locations() -> pd.DataFrame:
    ws = get_userloc_worksheet()
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=USERLOC_HEADERS)
    df.columns = [c.strip() for c in df.columns]
    df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")
    return df.dropna(subset=["Latitude", "Longitude"])


def save_user_location(name: str, lat: float, lon: float):
    ws = get_userloc_worksheet()
    ws.append_row([name.strip(), lat, lon, datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    load_user_locations.clear()


# ============================================================
# FARM LOCATIONS (read-only — same parsing rules as app.py)
# ============================================================
@st.cache_data(ttl=300, show_spinner="Loading farm locations...")
def load_farm_locations(url: str) -> pd.DataFrame:
    df = pd.read_csv(url)
    df.columns = [c.strip() for c in df.columns]
    return df


def parse_location(location: str):
    """Same point/polygon parser as app.py — polygons are reduced to
    their centroid here since this app only needs a point per farm."""
    if not isinstance(location, str):
        return None, None
    location = location.strip()
    if location.lower().startswith("polygon"):
        coords_match = re.search(r"\(\(([^)]+)\)\)", location)
        if not coords_match:
            return None, None
        points = []
        for pair in coords_match.group(1).split(","):
            parts = pair.strip().split()
            if len(parts) != 2:
                continue
            try:
                lon, lat = float(parts[0]), float(parts[1])
                points.append((lat, lon))
            except ValueError:
                continue
        if not points:
            return None, None
        return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
    match = re.match(r"\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*", location)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


# ============================================================
# FORM — capture Name + current location
# ============================================================
if not _gsheet_configured():
    st.error(
        "⚠️ Saving is unavailable — add the same `[gcp_service_account]` and "
        "`[gsheet]` (with `sheet_id`) sections used by the manager app to "
        "this app's `.streamlit/secrets.toml` before locations can be saved."
    )

st.subheader("1. Enter your name and capture your location")

name = st.text_input("Your name")

browser_lat, browser_lon = None, None
if _GEO_AVAILABLE:
    st.caption("Click below and allow the browser's location permission prompt.")
    loc = get_geolocation()
    if loc and "coords" in loc:
        browser_lat = loc["coords"]["latitude"]
        browser_lon = loc["coords"]["longitude"]
        st.success(f"Current location captured: {browser_lat:.6f}, {browser_lon:.6f}")
    else:
        st.info("Waiting for location permission... (or use manual entry below)")
else:
    st.warning(
        "The `streamlit-js-eval` package isn't installed, so automatic browser "
        "location capture is unavailable — run `pip install streamlit-js-eval` "
        "to enable it. Use manual entry below in the meantime."
    )

with st.expander("Enter coordinates manually instead", expanded=not _GEO_AVAILABLE):
    manual_lat = st.number_input("Latitude", value=browser_lat or 0.0, format="%.6f")
    manual_lon = st.number_input("Longitude", value=browser_lon or 0.0, format="%.6f")

final_lat = browser_lat if browser_lat is not None else manual_lat
final_lon = browser_lon if browser_lon is not None else manual_lon

if st.button("💾 Save My Location", type="primary", disabled=not _gsheet_configured()):
    if not name.strip():
        st.warning("Please enter your name first.")
    elif not final_lat or not final_lon:
        st.warning("No location set yet — allow browser location access or enter coordinates manually.")
    else:
        save_user_location(name, final_lat, final_lon)
        st.success(f"Saved location for {name.strip()}.")
        st.rerun()

# ============================================================
# MAP — farms (read-only) + saved user locations
# ============================================================
st.subheader("2. Map — farms and saved user locations")

try:
    raw_farms = load_farm_locations(LOCATIONS_CSV_URL)
    farm_df = raw_farms.copy()
    parsed = farm_df["Location"].apply(parse_location)
    farm_df["lat"] = parsed.apply(lambda x: x[0])
    farm_df["lon"] = parsed.apply(lambda x: x[1])
    farm_df = farm_df.dropna(subset=["lat", "lon"])
except Exception as e:
    farm_df = pd.DataFrame(columns=["Customer Name", "Farm Name", "lat", "lon"])
    st.warning(f"Could not load farm locations for the map background: {e}")

user_df = pd.DataFrame(columns=USERLOC_HEADERS)
if _gsheet_configured():
    try:
        user_df = load_user_locations()
    except Exception as e:
        st.warning(f"Could not load saved user locations: {e}")

if st.button("🔄 Refresh map"):
    load_user_locations.clear()
    load_farm_locations.clear()
    st.rerun()

st.caption(f"Showing {len(farm_df)} farm(s) and {len(user_df)} saved user location(s)")

if not user_df.empty:
    center_lat, center_lon = user_df["Latitude"].iloc[-1], user_df["Longitude"].iloc[-1]
    zoom = 14
elif not farm_df.empty:
    center_lat, center_lon = farm_df["lat"].mean(), farm_df["lon"].mean()
    zoom = 10
else:
    center_lat, center_lon = 7.8731, 80.7718  # fallback: center of Sri Lanka
    zoom = 8

m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, tiles=None)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Esri, Maxar, Earthstar Geographics",
    name="Satellite",
    overlay=False,
    control=False,
).add_to(m)

# Farms — small blue markers, for context only.
for _, row in farm_df.iterrows():
    display_name = (
        f"{row['Customer Name']} — {row['Farm Name']}"
        if str(row.get("Farm Name", "")).strip() not in ("", "-", "nan")
        else row.get("Customer Name", "")
    )
    folium.CircleMarker(
        location=[row["lat"], row["lon"]],
        radius=6,
        color="#3388ff",
        fill=True,
        fill_color="#3388ff",
        fill_opacity=0.8,
        tooltip=str(display_name),
        popup=folium.Popup(f"<b>Farm:</b> {display_name}", max_width=250),
    ).add_to(m)

# Saved user locations — distinct red pins on top.
for _, row in user_df.iterrows():
    folium.Marker(
        location=[row["Latitude"], row["Longitude"]],
        tooltip=str(row.get("Name", "")),
        popup=folium.Popup(
            f"<b>{row.get('Name', '')}</b><br>Saved: {row.get('Timestamp', '')}", max_width=250
        ),
        icon=folium.Icon(color="red", icon="user", prefix="fa"),
    ).add_to(m)

map_html = folium.Figure().add_child(m).render()
components.html(map_html, height=650, width=None)

if not user_df.empty:
    st.subheader("Saved locations")
    st.dataframe(user_df, hide_index=True, use_container_width=True)
