"""
Louisville Democratic Party - 2026 Sample Ballot App
"""
import os
import re
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory
import geopandas as gpd
from shapely.geometry import Point

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Load precinct shapefile at startup (fast for all subsequent requests)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GEOJSON = os.path.join(BASE_DIR, "precincts.geojson")

print("Loading precinct data…")
precincts_gdf = gpd.read_file(GEOJSON)
if precincts_gdf.crs and precincts_gdf.crs.to_epsg() != 4326:
    precincts_gdf = precincts_gdf.to_crs(epsg=4326)
print(f"Loaded {len(precincts_gdf)} precincts.")


# ---------------------------------------------------------------------------
# Polling place lookup (Jefferson County Clerk scraper)
# ---------------------------------------------------------------------------
_CLERK_URL = "https://jeffersoncountyclerk.org/wheredoivote/"
_CLERK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": _CLERK_URL,
}
_polling_cache = {}  # simple in-memory cache keyed by uppercased street address


def get_polling_place(street_address: str) -> dict:
    """
    Look up polling place for a Jefferson County address via the Clerk's website.
    street_address should be street-only (no city/state/zip), e.g. '527 W Jefferson St'.
    Returns dict with polling_place_name and polling_place_address, or None values on failure.
    """
    street_only = street_address.split(",")[0].strip()
    cache_key = street_only.upper()
    if cache_key in _polling_cache:
        return _polling_cache[cache_key]

    try:
        session = requests.Session()
        session.headers.update(_CLERK_HEADERS)

        # Step 1: GET page to obtain fresh ASP.NET session tokens
        r = session.get(_CLERK_URL, timeout=10)
        r.raise_for_status()

        def _val(html, field_id):
            m = re.search(rf'id="{field_id}"\s+value="([^"]*)"', html)
            return m.group(1) if m else ""

        post_data = {
            "__LASTFOCUS":          "",
            "sm1_HiddenField":      "",
            "__EVENTTARGET":        "",
            "__EVENTARGUMENT":      "",
            "__VIEWSTATE":          _val(r.text, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _val(r.text, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION":    _val(r.text, "__EVENTVALIDATION"),
            "txtStreet":            street_only,
            "cmdDisplay":           "Search",
        }

        # Step 2: POST the address form
        r2 = session.post(_CLERK_URL, data=post_data, timeout=10)
        r2.raise_for_status()
        html = r2.text

        def _tag_text(html, eid):
            m = re.search(rf'id="{eid}"[^>]*>(?:<[^>]+>)*([^<]+)', html)
            return m.group(1).strip() if m else None

        result = {
            "polling_place_name":    _tag_text(html, "lblLocation"),
            "polling_place_address": _tag_text(html, "lblAddress"),
        }

        if result["polling_place_name"]:
            _polling_cache[cache_key] = result

        return result

    except Exception as exc:
        print(f"Polling place lookup error: {exc}")
        return {"polling_place_name": None, "polling_place_address": None}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, "static"), filename)


@app.route("/api/lookup", methods=["POST"])
def lookup():
    data = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"error": "Please enter an address."}), 400
    # Always geocode within Louisville, KY
    if "louisville" not in address.lower() and "jefferson" not in address.lower():
        address = address + ", Louisville, KY"

    # 1. Geocode — Census Bureau first, ArcGIS fallback (both free, no API key)
    lon = lat = matched_address = None

    try:
        geo_resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=15,
        )
        geo_resp.raise_for_status()
        matches = geo_resp.json().get("result", {}).get("addressMatches", [])
        if matches:
            m = matches[0]
            lon = m["coordinates"]["x"]
            lat = m["coordinates"]["y"]
            matched_address = m.get("matchedAddress", address)
    except Exception:
        pass  # fall through to ArcGIS

    if lon is None:
        try:
            arc_resp = requests.get(
                "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates",
                params={
                    "SingleLine": address,
                    "outFields": "Match_addr",
                    "maxLocations": 1,
                    "f": "json",
                },
                timeout=15,
            )
            arc_resp.raise_for_status()
            candidates = arc_resp.json().get("candidates", [])
            if candidates and candidates[0].get("score", 0) >= 80:
                c = candidates[0]
                lon = c["location"]["x"]
                lat = c["location"]["y"]
                matched_address = c.get("address", address)
        except Exception as exc:
            return jsonify({"error": f"Geocoding service error: {exc}"}), 502

    if lon is None:
        return jsonify({
            "error": (
                "Address not found. Try including your city and state, "
                "e.g. '123 Main St, Louisville KY'."
            )
        }), 404

    # 2. Point-in-polygon – which precinct contains this point?
    point = Point(lon, lat)
    hits = precincts_gdf[precincts_gdf.contains(point)]

    if hits.empty:
        return jsonify({
            "error": (
                "That address appears to be outside Jefferson County. "
                "This ballot tool covers Louisville Metro / Jefferson County only."
            )
        }), 404

    row = hits.iloc[0]

    # 3. Polling place lookup (non-fatal — returns None values if it fails)
    polling = get_polling_place(matched_address or address)

    return jsonify({
        "matched_address":       matched_address,
        "precinct":              str(row["PRECINCT"]),
        "council_district":      int(row["COUNDIST"]),
        "congressional_district":int(row["CONGDIST"]),
        "state_house_district":  int(row["LEGISDIST"]),
        "state_senate_district": int(row["SENDIST"]),
        "commissioner_district": str(row["COMMDIST"]),
        "polling_place_name":    polling.get("polling_place_name"),
        "polling_place_address": polling.get("polling_place_address"),
    })


SHEETS_WEBHOOK = (
    "https://script.google.com/a/macros/louisvilledems.com/s/"
    "AKfycbzZ69a66stnlFhsjqfr9K5ZMXBD5jn4ODnRpgdLNMLG9PlEXCE_adeICA1fR-RngK6KXg/exec"
)


@app.route("/api/contact", methods=["POST"])
def contact():
    data = request.get_json(force=True)
    first_name = (data.get("first_name") or "").strip()
    last_name  = (data.get("last_name")  or "").strip()
    email      = (data.get("email")      or "").strip()
    phone      = (data.get("phone")      or "").strip()
    address    = (data.get("address")    or "").strip()

    if not email and not phone:
        return jsonify({"error": "Email or phone required."}), 400

    try:
        resp = requests.post(
            SHEETS_WEBHOOK,
            json={"first_name": first_name, "last_name": last_name, "email": email, "phone": phone, "address": address},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Sheets webhook error: {exc}")
        return jsonify({"error": "Could not save — please try again."}), 502

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
