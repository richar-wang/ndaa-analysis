"""
attribute.py - Attribution analysis for flagged NDAA sections

Takes a section number and NDAA year, outputs the section text,
structural context, US Code references, Pangram per-segment heatmap,
and attempts to identify House vs Senate origin from conference reports.

Usage:
    python attribute.py <section-number> <ndaa-year>
    python attribute.py 1533 fy2026
"""

import os
import re
import csv
import json
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTIONS_DIR = os.path.join(BASE_DIR, "data", "sections")
RESULTS_DIR = os.path.join(BASE_DIR, "pangram", "results")
ATTRIBUTION_DIR = os.path.join(BASE_DIR, "attribution")


def load_metadata(year):
    """Load section metadata for a given NDAA year."""
    csv_path = os.path.join(SECTIONS_DIR, year, "metadata.csv")
    if not os.path.exists(csv_path):
        print(f"ERROR: Metadata not found: {csv_path}")
        print("Run parse_ndaa.py first.")
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def find_section(metadata, section_number):
    """Find a section in metadata by number."""
    for row in metadata:
        if row["section_number"] == str(section_number):
            return row
    for row in metadata:
        if str(section_number) in row["section_number"]:
            return row
    return None


def extract_usc_references(text):
    """Find US Code references in section text."""
    patterns = [
        r"(?:title\s+)?(\d+)\s+(?:United\s+States\s+Code|U\.?S\.?C\.?)\s*(?:,?\s*section\s*)?(\d+[a-z]*(?:\([a-z0-9]+\))*)?",
        r"section\s+(\d+[a-z]*)\s+of\s+title\s+(\d+)",
        r"(\d+)\s+U\.?S\.?C\.?\s+(?:\u00a7\s*)?(\d+[a-z]*)",
    ]

    references = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            references.add(match.group(0).strip())

    return sorted(references)


def load_pangram_result(year, section_meta):
    """Load the Pangram JSON result for a section."""
    result_filename = section_meta["file_path"].replace(".txt", ".json")
    result_path = os.path.join(RESULTS_DIR, year, result_filename)

    if not os.path.exists(result_path):
        return None

    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_segment_heatmap(result):
    """Print a per-segment breakdown from Pangram windows[] data.

    Shows each segment with its label, ai_assistance_score, and confidence,
    color-coded by classification. This is the key drill-down that lets you
    pinpoint which paragraphs within a section triggered the detection.
    """
    windows = result.get("windows", [])
    if not windows:
        print("  No per-segment data available in Pangram response.")
        return

    print(f"  {len(windows)} segments analyzed (~50 words each)\n")

    # Header
    print(f"  {'#':>3}  {'Label':<24} {'Score':>5} {'Conf':<6} {'Text preview'}")
    print(f"  {'─' * 3}  {'─' * 24} {'─' * 5} {'─' * 6} {'─' * 50}")

    ai_segments = []

    for i, w in enumerate(windows, 1):
        label = w.get("label", "Unknown")
        score = w.get("ai_assistance_score", 0)
        conf = w.get("confidence", "?")
        text = w.get("text", "").strip()
        word_count = w.get("word_count", len(text.split()))

        # Truncate preview
        preview = text[:80].replace("\n", " ")
        if len(text) > 80:
            preview += "..."

        # Mark non-human segments
        marker = ""
        if label in ("AI-Generated", "Moderately AI-Assisted", "Heavily AI-Assisted"):
            marker = " ***"
            ai_segments.append((i, label, score, conf, text))
        elif label == "Lightly AI-Assisted":
            marker = " *"
            ai_segments.append((i, label, score, conf, text))

        print(f"  {i:>3}  {label:<24} {score:>5.2f} {conf:<6} {preview}{marker}")

    # Print full text of flagged segments
    if ai_segments:
        print(f"\n{'─' * 70}")
        print(f"FLAGGED SEGMENTS - FULL TEXT ({len(ai_segments)} of {len(windows)}):")
        print(f"{'─' * 70}")
        for seg_num, label, score, conf, text in ai_segments:
            print(f"\n  Segment {seg_num} [{label}, score={score:.2f}, confidence={conf}]:")
            # Wrap text for readability
            words = text.split()
            line = "    "
            for word in words:
                if len(line) + len(word) + 1 > 90:
                    print(line)
                    line = "    " + word
                else:
                    line = line + " " + word if line.strip() else "    " + word
            if line.strip():
                print(line)


def check_conference_report(year, section_number):
    """Check if conference report information is available for attribution."""
    cr_patterns = [
        os.path.join(ATTRIBUTION_DIR, f"{year}_conference_report.txt"),
        os.path.join(ATTRIBUTION_DIR, f"{year}_joint_explanatory_statement.txt"),
    ]

    for path in cr_patterns:
        if not os.path.exists(path):
            continue

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        sec_pattern = rf"(?:Section|Sec\.?)\s*{section_number}\b"
        matches = list(re.finditer(sec_pattern, content, re.IGNORECASE))

        if not matches:
            continue

        results = []
        for match in matches:
            start = max(0, match.start() - 500)
            end = min(len(content), match.end() + 500)
            context = content[start:end]

            house_indicators = [
                "House bill", "House provision", "House amendment",
                "as passed by the House", "House-passed",
            ]
            senate_indicators = [
                "Senate bill", "Senate provision", "Senate amendment",
                "as passed by the Senate", "Senate-passed",
            ]

            origin = "Unknown"
            for indicator in house_indicators:
                if indicator.lower() in context.lower():
                    origin = "House"
                    break
            for indicator in senate_indicators:
                if indicator.lower() in context.lower():
                    if origin == "House":
                        origin = "Both chambers"
                    else:
                        origin = "Senate"
                    break

            results.append({"context": context, "origin": origin})
        return results

    return None


def main():
    if len(sys.argv) < 3:
        print("Usage: python attribute.py <section-number> <ndaa-year>")
        print("Example: python attribute.py 1533 fy2026")
        sys.exit(1)

    section_number = sys.argv[1]
    year = sys.argv[2].lower()

    print(f"Attribution Analysis: Section {section_number}, {year.upper()} NDAA")
    print("=" * 70)

    # Load metadata
    metadata = load_metadata(year)
    section = find_section(metadata, section_number)

    if not section:
        print(f"ERROR: Section {section_number} not found in {year} metadata")
        print(f"Available sections: {', '.join(r['section_number'] for r in metadata[:20])}...")
        sys.exit(1)

    # Section info
    print(f"\n  Section Number: {section['section_number']}")
    print(f"  Title: {section['title']}")
    print(f"  Division: {section['division']}")
    print(f"  Subtitle: {section['subtitle']}")
    print(f"  Word Count: {section['word_count']}")

    # Pangram detection summary
    pangram_result = load_pangram_result(year, section)
    if pangram_result:
        print(f"\n{'─' * 70}")
        print("PANGRAM DETECTION RESULT:")
        print(f"{'─' * 70}")
        headline = pangram_result.get("headline", "N/A")
        pred_short = pangram_result.get("prediction_short", "N/A")
        frac_ai = pangram_result.get("fraction_ai", 0)
        frac_asst = pangram_result.get("fraction_ai_assisted", 0)
        frac_human = pangram_result.get("fraction_human", 0)
        num_seg = (pangram_result.get("num_ai_segments", 0)
                   + pangram_result.get("num_ai_assisted_segments", 0)
                   + pangram_result.get("num_human_segments", 0))

        print(f"  Headline:       {headline}")
        print(f"  Classification: {pred_short}")
        print(f"  Fraction AI:          {frac_ai:.1%}")
        print(f"  Fraction AI-Assisted: {frac_asst:.1%}")
        print(f"  Fraction Human:       {frac_human:.1%}")
        print(f"  Total segments: {num_seg}")

        # Segment-level heatmap
        print(f"\n{'─' * 70}")
        print("PER-SEGMENT HEATMAP:")
        print(f"{'─' * 70}")
        print_segment_heatmap(pangram_result)
    else:
        print(f"\n  No Pangram result found. Run detect.py first.")

    # Full text
    section_path = os.path.join(SECTIONS_DIR, year, section["file_path"])
    if os.path.exists(section_path):
        with open(section_path, "r", encoding="utf-8") as f:
            text = f.read()

        print(f"\n{'─' * 70}")
        print("FULL SECTION TEXT:")
        print(f"{'─' * 70}")
        print(text)
        print(f"{'─' * 70}")

        # USC references
        usc_refs = extract_usc_references(text)
        if usc_refs:
            print(f"\n  US Code References Found ({len(usc_refs)}):")
            for ref in usc_refs:
                print(f"    - {ref}")
        else:
            print("\n  No US Code references found.")
    else:
        print(f"\n  WARNING: Section file not found: {section_path}")

    # Conference report origin
    print(f"\n{'─' * 70}")
    print("ORIGIN ANALYSIS:")
    print(f"{'─' * 70}")

    cr_results = check_conference_report(year, section_number)
    if cr_results:
        for i, result in enumerate(cr_results, 1):
            print(f"\n  Match {i}:")
            print(f"  Likely Origin: {result['origin']}")
            print(f"  Conference Report Context:")
            for line in result["context"].split("\n"):
                print(f"    {line}")
    else:
        print("  No conference report found in attribution/ directory.")
        print(f"  To enable origin analysis, download the conference report to:")
        print(f"    {os.path.join(ATTRIBUTION_DIR, f'{year}_conference_report.txt')}")


if __name__ == "__main__":
    main()
