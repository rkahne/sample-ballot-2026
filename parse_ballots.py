#!/usr/bin/env python3
"""
Parse already-downloaded ballot PDFs and extract races.
Run after discover_ballots.py has downloaded the PDFs.
"""

import json
import os
import sys
import collections

import pdfplumber

GEOJSON_PATH = r"C:\Users\Robert\Documents\ldp-apps\sample-ballot-2026\precincts.geojson"
TEMP_DIR = sys.argv[1] if len(sys.argv) > 1 else input("Enter temp dir path: ").strip()

STANDARD_RACES = {
    "united states senator",
    "louisville metro mayor",
    "louisville metro mayor and metro council",
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
    "united states representative",
    "u.s. representative",
    "united states house",
    "u.s. house",
}

RACE_KEYWORDS = [
    "council", "mayor", "commissioner", "judge", "magistrate",
    "trustee", "alderman", "alderperson", "board", "senator",
    "representative", "sheriff", "clerk", "attorney", "treasurer",
    "assessor", "constable", "superintendent", "school",
]

SKIP_PHRASES = [
    "vote for", "select", "instruction", "official ballot",
    "democratic", "republican", "primary", "2026", "jefferson",
    "write-in", "write in", "page ", "continued", "nonpartisan",
    "polling", "precinct",
]


def is_standard(text):
    t = text.strip().lower()
    for s in STANDARD_RACES:
        if s in t or t in s:
            return True
    return False


def looks_like_race_heading(text):
    t = text.strip().lower()
    if not t:
        return False
    for kw in RACE_KEYWORDS:
        if kw in t:
            return True
    return False


def extract_all_text_blocks(pdf_path):
    """Extract text from PDF grouped by font size to identify headings vs body."""
    blocks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            words = page.extract_words(extra_attrs=["fontname", "size"])
            lines_by_y = collections.defaultdict(list)
            for w in words:
                y_bucket = round(w["top"] / 3) * 3
                lines_by_y[y_bucket].append(w)

            for y in sorted(lines_by_y.keys()):
                line_words = sorted(lines_by_y[y], key=lambda w: w["x0"])
                line_text = " ".join(w["text"] for w in line_words).strip()
                if not line_text:
                    continue
                avg_size = sum(w.get("size", 10) for w in line_words) / len(line_words)
                blocks.append({
                    "text": line_text,
                    "size": avg_size,
                    "page": page_num,
                    "y": y,
                })
    return blocks


def extract_races(pdf_path):
    """Return list of (race_title, [candidates])."""
    blocks = extract_all_text_blocks(pdf_path)

    # Find the maximum font size to help identify main headings
    max_size = max((b["size"] for b in blocks), default=10)

    races = []
    current_race = None
    current_candidates = []

    for b in blocks:
        text = b["text"].strip()
        size = b["size"]
        is_caps = text == text.upper() and len(text) > 3
        is_heading_size = size >= (max_size * 0.75)

        if looks_like_race_heading(text) and (is_caps or is_heading_size):
            if current_race:
                races.append((current_race, current_candidates[:]))
            current_race = text
            current_candidates = []
        elif current_race:
            low = text.lower()
            if any(skip in low for skip in SKIP_PHRASES):
                continue
            if len(text) < 3 or len(text) > 70:
                continue
            if text.startswith("(") or text[0].isdigit():
                continue
            # Skip if all caps and not a candidate name pattern
            if text == text.upper() and len(text.split()) > 4:
                continue
            current_candidates.append(text)

    if current_race:
        races.append((current_race, current_candidates[:]))

    return races


def main():
    print("Loading precincts...")
    with open(GEOJSON_PATH, "r") as f:
        data = json.load(f)
    precincts = sorted(set(feat["properties"]["PRECINCT"] for feat in data["features"]))

    # Group PDFs by file size
    size_to_precincts = collections.defaultdict(list)
    for prec in precincts:
        pdf_path = os.path.join(TEMP_DIR, f"{prec}.pdf")
        if os.path.exists(pdf_path):
            size = os.path.getsize(pdf_path)
            size_to_precincts[size].append(prec)

    print(f"Found {len(size_to_precincts)} unique ballot variants across {sum(len(v) for v in size_to_precincts.values())} precincts.\n")

    # race -> {candidates, precincts, is_standard}
    all_races = collections.defaultdict(lambda: {"candidates": set(), "precincts": [], "standard": False})

    print("Parsing ballot variants...")
    for size, precs in sorted(size_to_precincts.items()):
        representative = precs[0]
        pdf_path = os.path.join(TEMP_DIR, f"{representative}.pdf")
        print(f"\nVariant: {size:,} bytes | Representative: {representative} | Precincts: {len(precs)}")

        try:
            races = extract_races(pdf_path)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        for race_title, candidates in races:
            std = is_standard(race_title)
            marker = "  [STANDARD]" if std else "  *** EXTRA ***"
            print(f"  {race_title}{marker}")
            for c in candidates:
                print(f"    - {c}")
            all_races[race_title]["precincts"].extend(precs)
            all_races[race_title]["candidates"].update(candidates)
            all_races[race_title]["standard"] = std

    # Final report
    print("\n" + "=" * 70)
    print("SUMMARY: EXTRA RACES NOT IN STANDARD LIST")
    print("=" * 70)

    extra = {k: v for k, v in all_races.items() if not v["standard"]}
    if not extra:
        print("No extra races found.")
    else:
        for race_title in sorted(extra.keys()):
            info = extra[race_title]
            prec_list = sorted(set(info["precincts"]))
            print(f"\nRACE: {race_title}")
            print(f"  Precinct count: {len(prec_list)}")
            print(f"  Precincts: {prec_list}")
            cands = sorted(info["candidates"])
            if cands:
                print(f"  Candidates ({len(cands)}):")
                for c in cands:
                    print(f"    - {c}")
            else:
                print("  Candidates: (none extracted cleanly)")

    print("\n" + "=" * 70)
    print("STANDARD RACES FOUND")
    print("=" * 70)
    for race_title in sorted(k for k, v in all_races.items() if v["standard"]):
        info = all_races[race_title]
        print(f"  {race_title} ({len(set(info['precincts']))} precincts)")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
