"""
User Location Tracker - Streamlit App (minimal)
-------------------------------------------
STANDALONE app. Does NOT modify app.py (the Farm Map app) in any way.

This app is intentionally just two things for the user:
  1. A "Name" text field
  2. A "Save Location" button

Pressing the button captures the browser's current GPS/network location
(via the streamlit-js-eval package, which triggers the browser's location
permission prompt) and saves (Name, Latitude, Longitude, Timestamp) as a
new row in a "UserLocations" worksheet inside the SAME private Google
Sheet the manager app already uses for WaterQualityData -- reusing the
same [gcp_service_account] / [gsheet] secrets. That worksheet is created
automatically (with headers) the first time this app ever saves a row.

Viewing everyone's saved locations together with the farms on a map is a
separate step, not part of this minimal entry screen -- happy to build
that as its own small "view" app/page if wanted.

Local run:
    pip install -r requirements.txt
    streamlit run user_location_app.py

    Needs the same `.streamlit/secrets.toml` as the manager app:
        [gcp_service_account]
        ... (same service-account JSON fields) ...

        [gsheet]
        sheet_id = "..."   # same WaterQualityData spreadsheet key
    The service account must have Editor access on that spreadsheet.
"""

from datetime import datetime

import streamlit as st
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
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
USERLOC_WORKSHEET_NAME = "UserLocations"
USERLOC_HEADERS = ["Name", "Latitude", "Longitude", "Last Updated"]

st.set_page_config(page_title="Save My Location", page_icon="📍", layout="centered")
st.title("📍 Save My Location")


# ============================================================
# GOOGLE SHEET HELPERS
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


def save_user_location(name: str, lat: float, lon: float):
    """Upsert by name: if this person already has a saved row, overwrite
    it in place with their new lat/lon and today's Last Updated date
    (so each user only ever has ONE row on the sheet). Otherwise append
    a new row."""
    ws = get_userloc_worksheet()
    name = name.strip()
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_names = ws.col_values(1)  # column A, including the header row

    row_number = None
    for i, existing_name in enumerate(existing_names):
        if i == 0:
            continue  # header row
        if existing_name.strip() == name:
            row_number = i + 1  # gspread rows are 1-based
            break

    if row_number:
        ws.update(f"A{row_number}:D{row_number}", [[name, lat, lon, last_updated]])
    else:
        ws.append_row([name, lat, lon, last_updated])


# ============================================================
# MINIMAL UI -- Name + Save Location button, nothing else
# ============================================================
if not _gsheet_configured():
    st.error(
        "⚠️ Saving is unavailable — add the same `[gcp_service_account]` and "
        "`[gsheet]` (with `sheet_id`) sections used by the manager app to "
        "this app's `.streamlit/secrets.toml`."
    )

if not _GEO_AVAILABLE:
    st.error(
        "The `streamlit-js-eval` package isn't installed — run "
        "`pip install streamlit-js-eval` (see requirements.txt) so the "
        "browser's current location can be captured."
    )

# get_geolocation() is ASYNC: it renders a small component that asks the
# browser for permission and only delivers the coordinates on a later
# rerun. Calling it fresh at button-click time (the old bug) meant it was
# always empty on that click. Instead, call it on every run so it keeps
# requesting/refreshing, and stash whatever it returns in session_state
# — the button then just uses whatever has already been captured.
if _GEO_AVAILABLE:
    loc = get_geolocation()
    if loc and "coords" in loc:
        st.session_state["_captured_coords"] = (
            loc["coords"]["latitude"],
            loc["coords"]["longitude"],
        )

captured = st.session_state.get("_captured_coords")
if captured:
    st.caption(f"📍 Location ready: {captured[0]:.6f}, {captured[1]:.6f}")
else:
    st.caption("📍 Waiting for location permission... allow it in the browser prompt above.")

name = st.text_input("Name")

if st.button("💾 Save Location", type="primary", disabled=not (_gsheet_configured() and _GEO_AVAILABLE)):
    if not name.strip():
        st.warning("Please enter your name first.")
    elif not captured:
        st.warning(
            "Still waiting for your location — make sure you allowed the browser's "
            "location permission prompt (it must also be HTTPS, or localhost, for "
            "the browser to share location at all), then try Save again in a moment."
        )
    else:
        lat, lon = captured
        save_user_location(name, lat, lon)
        st.success(f"Saved location for {name.strip()} ({lat:.6f}, {lon:.6f}).")
