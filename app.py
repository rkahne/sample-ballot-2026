"""
Louisville Democratic Party - 2026 Sample Ballot App
"""
import os
import json
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

    # 1. Geocode with U.S. Census Bureau (no API key required)
    try:
        geo_resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={
                "address": address,
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
            timeout=15,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
    except Exception as exc:
        return jsonify({"error": f"Geocoding service error: {exc}"}), 502

    matches = geo_data.get("result", {}).get("addressMatches", [])
    if not matches:
        return jsonify({
            "error": (
                "Address not found. Try including your city and state, "
                "e.g. '123 Main St, Louisville KY'."
            )
        }), 404

    match = matches[0]
    lon = match["coordinates"]["x"]
    lat = match["coordinates"]["y"]
    matched_address = match.get("matchedAddress", address)

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

    return jsonify({
        "matched_address": matched_address,
        "precinct": str(row["PRECINCT"]),
        "council_district": int(row["COUNDIST"]),
        "congressional_district": int(row["CONGDIST"]),
        "state_house_district": int(row["LEGISDIST"]),
        "state_senate_district": int(row["SENDIST"]),
        "commissioner_district": str(row["COMMDIST"]),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
