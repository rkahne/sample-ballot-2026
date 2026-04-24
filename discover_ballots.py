#!/usr/bin/env python3
"""
Discover extra races in Jefferson County Democratic precinct ballots.
Downloads each precinct's PDF, extracts race titles, and reports anything
not in the standard race list.
"""

import json
import os
import time
import urllib.request
import urllib.error
import collections
import tempfile

import pdfplumber

# ── Config ──────────────────────────────────────────────────────────────────
GEOJSON_PATH = r"C:\Users\Robert\Documents\ldp-apps\sample-ballot-2026\precincts.geojson"
URL_TEMPLATE = "https://jeffersoncountyclerk.org/wheredoivote/images/ballots/{}-JEF-D.pdf"
DELAY = 0.5  # seconds between requests
TEMP_DIR = tempfile.mkdtemp(prefix="jc_ballots_")

# Standard races already handled in the app
STANDARD_RACES = {
    "united states senator",
    "louisville metro mayor",
    "louisville metro mayor and metro council",  # alternate phrasing
    "state senator",
    "state representative",
    "county attorney",
    "county clerk",
    "sheriff",
    "metro council",
    "metro council member",
    "metro council district",
    "u.s. senator",
    "us senator",
    "united states representative",  # include congress just in case
    "u.s. representative",
}

# Keywords that indicate a line is a race heading (not a candidate name / instruction)
RACE_KEYWORDS = [
    "council", "mayor", "commissioner", "judge", "magistrate",
    "trustee", "alderman", "alderperson", "board", "senator",
    "representative", "sheriff", "clerk", "attorney", "treasurer",
    "assessor", "constable", "superintendent", "school",
]


def is_standard(text: str) -> bool:
    t = text.strip().lower()
    for s in STANDARD_RACES:
        if s in t or t in s:
            return True
    return False


def looks_like_race_heading(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    for kw in RACE_KEYWORDS:
        if kw in t:
            return True
    return False


def extract_races(pdf_path: str):
    """
    Return a list of (race_title, [candidates]) tuples found in the PDF.
    We look for bold / large text that matches race keywords, then collect
    the lines below until the next heading or a known separator.
    """
    races = []
    current_race = None
    current_candidates = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract words with font size info so we can detect headings
            words = page.extract_words(extra_attrs=["fontname", "size"])
            # Group words into lines by their top-y coordinate (within 3pt)
            lines_by_y = collections.defaultdict(list)
            for w in words:
                y_bucket = round(w["top"] / 3) * 3
                lines_by_y[y_bucket].append(w)

            for y in sorted(lines_by_y.keys()):
                line_words = sorted(lines_by_y[y], key=lambda w: w["x0"])
                line_text = " ".join(w["text"] for w in line_words).strip()
                if not line_text:
                    continue

                # Estimate if heading: larger font OR all-caps AND contains keyword
                avg_size = sum(w.get("size", 10) for w in line_words) / len(line_words)
                is_caps = line_text == line_text.upper() and len(line_text) > 4
                is_large = avg_size >= 10  # most body text is ~8-9pt in these PDFs

                if looks_like_race_heading(line_text) and (is_caps or is_large):
                    # Save previous race
                    if current_race:
                        races.append((current_race, current_candidates))
                    current_race = line_text
                    current_candidates = []
                elif current_race:
                    # Skip instruction lines
                    low = line_text.lower()
                    if any(skip in low for skip in [
                        "vote for", "select", "instruction", "official ballot",
                        "democratic", "republican", "primary", "2026", "jefferson",
                        "write-in", "write in", "page ", "continued",
                    ]):
                        continue
                    # Candidate names: typically Title Case, not all-caps, reasonable length
                    if 2 < len(line_text) < 60 and not line_text.startswith("("):
                        current_candidates.append(line_text)

        if current_race:
            races.append((current_race, current_candidates))

    return races


def main():
    # Load precincts
    print("Loading precincts from GeoJSON...")
    with open(GEOJSON_PATH, "r") as f:
        data = json.load(f)
    precincts = sorted(set(feat["properties"]["PRECINCT"] for feat in data["features"]))
    print(f"Found {len(precincts)} unique precincts.\n")
    print(f"Temp PDF dir: {TEMP_DIR}\n")

    # Group by file size to avoid re-parsing identical PDFs
    size_to_precincts = collections.defaultdict(list)
    failed = []
    not_found = []

    print("Step 1/3: Downloading ballots...")
    for i, prec in enumerate(precincts):
        url = URL_TEMPLATE.format(prec)
        pdf_path = os.path.join(TEMP_DIR, f"{prec}.pdf")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read()
            with open(pdf_path, "wb") as f:
                f.write(content)
            size_to_precincts[len(content)].append(prec)
            if (i + 1) % 50 == 0:
                print(f"  {i+1}/{len(precincts)} done...")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                not_found.append(prec)
            else:
                failed.append((prec, str(e)))
        except Exception as e:
            failed.append((prec, str(e)))
        time.sleep(DELAY)

    print(f"\nDownload summary:")
    print(f"  Successful: {sum(len(v) for v in size_to_precincts.values())}")
    print(f"  404 Not Found: {len(not_found)}")
    print(f"  Other errors: {len(failed)}")
    if not_found:
        print(f"  404 precincts: {not_found}")
    if failed:
        print(f"  Error precincts: {failed}")

    # Unique PDF sizes → unique ballot variants
    print(f"\nUnique ballot variants (by file size): {len(size_to_precincts)}")
    for size, precs in sorted(size_to_precincts.items()):
        print(f"  {size:,} bytes -> {len(precs)} precincts (e.g. {precs[0]})")

    # Step 2: Parse one PDF per unique size
    print("\nStep 2/3: Extracting races from each unique ballot variant...")
    # race_title → { candidates: set, precincts: list }
    extra_race_info = collections.defaultdict(lambda: {"candidates": set(), "precincts": []})

    for size, precs in sorted(size_to_precincts.items()):
        representative = precs[0]
        pdf_path = os.path.join(TEMP_DIR, f"{representative}.pdf")
        print(f"\n  Parsing {representative}.pdf ({size:,} bytes, covers {len(precs)} precincts)...")
        try:
            races = extract_races(pdf_path)
            print(f"    Races found: {len(races)}")
            for race_title, candidates in races:
                print(f"      Race: {race_title!r}")
                if candidates:
                    print(f"        Candidates: {candidates}")
                if not is_standard(race_title):
                    extra_race_info[race_title]["precincts"].extend(precs)
                    extra_race_info[race_title]["candidates"].update(candidates)
        except Exception as e:
            print(f"    ERROR parsing: {e}")

    # Step 3: Report
    print("\n" + "=" * 70)
    print("STEP 3: EXTRA RACES NOT IN STANDARD LIST")
    print("=" * 70)

    if not extra_race_info:
        print("No extra races found.")
    else:
        for race_title, info in sorted(extra_race_info.items()):
            print(f"\nRACE: {race_title}")
            print(f"  Precincts ({len(info['precincts'])}): {sorted(info['precincts'])[:10]}{'...' if len(info['precincts']) > 10 else ''}")
            if info["candidates"]:
                print(f"  Candidates: {sorted(info['candidates'])}")
            else:
                print(f"  Candidates: (none extracted)")

    print("\n" + "=" * 70)
    print("FULL RACE EXTRACTION PER BALLOT VARIANT")
    print("=" * 70)
    # Re-run to show ALL races per variant for completeness
    for size, precs in sorted(size_to_precincts.items()):
        representative = precs[0]
        pdf_path = os.path.join(TEMP_DIR, f"{representative}.pdf")
        print(f"\nVariant: {size:,} bytes | Example precinct: {representative} | Total precincts: {len(precs)}")
        try:
            races = extract_races(pdf_path)
            for race_title, candidates in races:
                marker = "" if is_standard(race_title) else "  *** EXTRA ***"
                print(f"  [{race_title}]{marker}")
                for c in candidates:
                    print(f"    - {c}")
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\nDone. PDFs saved in: {TEMP_DIR}")


if __name__ == "__main__":
    main()
