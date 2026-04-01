"""
trim_sections.py - Create trimmed section datasets for Pangram analysis.

Applies identical filtering rules to any NDAA year to ensure methodological
consistency across years. This is the single source of truth for what gets
cut and what gets kept.

Usage:
    python trim_sections.py <ndaa-year>
    python trim_sections.py fy2020
    python trim_sections.py fy2026
"""

import csv
import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTIONS_DIR = os.path.join(BASE_DIR, "data", "sections")

# Minimum word count for AI detection to be reliable
MIN_WORDS = 225

# Maximum word count for a single section — anything above this is almost
# certainly a funding table or codification dump, not prose
MAX_WORDS = 15_000

# Sections whose content is primarily dollar-amount tables (sec4xxx funding tables)
# These exist in every NDAA year with the same section numbers
FUNDING_TABLE_SECTIONS = {"4101", "4201", "4301", "4601"}

# Legal boilerplate title patterns (exact match on stripped lowercase title)
BOILERPLATE_TITLES = {
    "effective date", "effective dates", "applicability",
    "savings clause", "savings provision", "savings provisions",
    "severability", "rule of construction", "rules of construction",
    "sunset", "termination", "implementation", "regulations",
    "transfer of funds",
}


def should_cut(title_lower, title_orig, wc, content, division, subtitle, section_number):
    """Apply filtering rules. Returns (should_cut, reason) tuple.

    Rules are applied in priority order. Every rule here is applied
    identically across all NDAA years.
    """

    # === STRUCTURAL / BOILERPLATE (always cut) ===

    if "table of contents" in title_lower:
        return True, "table_of_contents"

    if "budgetary effect" in title_lower:
        return True, "budgetary_boilerplate"

    if "joint explanatory statement" in title_lower:
        return True, "joint_explanatory"

    if title_lower.strip() == "authorization of appropriations":
        return True, "auth_appropriations"

    if "Funding Tables" in division:
        return True, "funding_tables_div"

    if re.search(r"(clerical|conforming|technical).*(amendment|correction)", title_lower):
        return True, "technical_amendment"

    if title_lower.strip() in ("definitions", "definition"):
        return True, "definitions"

    if title_lower.strip() in ("short title", "short titles"):
        return True, "short_title"

    if "authorization of amounts" in title_lower:
        return True, "funding_amounts"

    # === FUNDING TABLES by section number (sec4101, 4201, 4301, 4601) ===
    if section_number in FUNDING_TABLE_SECTIONS:
        return True, "funding_table_by_number"

    # === SIZE FILTERS ===

    if wc < MIN_WORDS:
        return True, "too_short_for_detection"

    if wc > MAX_WORDS:
        return True, "too_large_non_prose"

    # === LEGAL BOILERPLATE by title ===

    if title_lower.strip() in BOILERPLATE_TITLES:
        return True, "legal_boilerplate"

    # === AMENDMENTS TO EXISTING LAW ===

    amends_existing = bool(re.search(
        r"(?:is amended|are amended|is further amended)", content, re.I
    ))
    creates_new = bool(re.search(
        r"(?:shall establish|is established|there is established|is authorized to"
        r"|shall submit.*(?:report|plan|strategy)|shall develop|shall conduct"
        r"|pilot program|sense of (?:congress|the senate|the house)"
        r"|Not later than.*(?:Secretary|Director|President)|program is designated)",
        content, re.I,
    ))
    amend_hits = len(re.findall(
        r"(?:by striking|by inserting|is amended|by amending|to read as follows"
        r"|by redesignating|by adding at the end)",
        content, re.I,
    ))

    if amends_existing and not creates_new:
        return True, "amendment_to_existing_law"

    if amends_existing and amend_hits >= 3 and wc < 400:
        return True, "mostly_amendment"

    # === SIMPLE EXTENSIONS / REPEALS ===

    if ("extension of" in title_lower or "repeal of" in title_lower) and wc < 300:
        if not re.search(
            r"(?:shall establish|pilot program|shall submit|shall develop|sense of)",
            content, re.I,
        ):
            return True, "simple_extension_repeal"

    return False, None


def trim_year(year):
    src_dir = os.path.join(SECTIONS_DIR, year)
    dst_dir = os.path.join(SECTIONS_DIR, f"{year}-trimmed")

    if not os.path.isdir(src_dir):
        print(f"ERROR: source directory not found: {src_dir}")
        sys.exit(1)

    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir)

    csv_path = os.path.join(src_dir, "metadata.csv")
    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    cut_list = []
    keep_list = []

    for r in rows:
        fn = r["file_path"]
        wc = int(r["word_count"])
        title_lower = r["title"].lower()
        title_orig = r["title"]
        division = r.get("division", "")
        subtitle = r.get("subtitle", "")
        section_number = r.get("section_number", "")

        filepath = os.path.join(src_dir, fn)
        if not os.path.exists(filepath):
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        do_cut, reason = should_cut(
            title_lower, title_orig, wc, content,
            division, subtitle, section_number,
        )

        if do_cut:
            cut_list.append((fn, wc, reason, title_orig))
        else:
            keep_list.append((fn, wc, title_orig))
            shutil.copy2(filepath, os.path.join(dst_dir, fn))

    # Write trimmed metadata
    with open(os.path.join(dst_dir, "metadata.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["section_number", "title", "division", "subtitle", "word_count", "file_path"])
        keep_fns = {k[0] for k in keep_list}
        for r in rows:
            if r["file_path"] in keep_fns:
                writer.writerow([
                    r["section_number"], r["title"], r["division"],
                    r["subtitle"], r["word_count"], r["file_path"],
                ])

    # Write cut log
    with open(os.path.join(dst_dir, "cut_log.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file", "word_count", "reason", "title"])
        for fn, wc, reason, t in cut_list:
            writer.writerow([fn, wc, reason, t])

    # Report
    cut_words = sum(x[1] for x in cut_list)
    keep_words = sum(x[1] for x in keep_list)
    total_words = cut_words + keep_words
    total_sections = len(cut_list) + len(keep_list)

    print(f"\n{'=' * 60}")
    print(f"Trimming: {year.upper()} NDAA")
    print(f"{'=' * 60}")
    print(f"  Sections: {len(keep_list)} kept / {len(cut_list)} cut / {total_sections} total "
          f"({len(keep_list)/total_sections*100:.1f}% kept)")
    print(f"  Words:    {keep_words:,} kept / {cut_words:,} cut / {total_words:,} total "
          f"({keep_words/total_words*100:.1f}% kept)")

    from collections import Counter
    rc = Counter()
    rw = Counter()
    for fn, wc, reason, t in cut_list:
        rc[reason] += 1
        rw[reason] += wc

    print(f"\n  Cut breakdown:")
    for reason in sorted(rc.keys(), key=lambda x: -rw[x]):
        print(f"    {reason}: {rc[reason]} sections, {rw[reason]:,} words")

    print(f"\n  Output: {dst_dir}")
    return {"year": year, "kept": len(keep_list), "cut": len(cut_list),
            "kept_words": keep_words, "cut_words": cut_words}


def main():
    if len(sys.argv) < 2:
        print("Usage: python trim_sections.py <ndaa-year> [<ndaa-year> ...]")
        print("Example: python trim_sections.py fy2020 fy2026")
        sys.exit(1)

    results = []
    for year in sys.argv[1:]:
        results.append(trim_year(year.lower()))

    if len(results) > 1:
        print(f"\n{'=' * 60}")
        print("Comparison")
        print(f"{'=' * 60}")
        for r in results:
            total = r["kept"] + r["cut"]
            total_w = r["kept_words"] + r["cut_words"]
            print(f"  {r['year'].upper()}: {r['kept']}/{total} sections "
                  f"({r['kept']/total*100:.1f}%), "
                  f"{r['kept_words']:,}/{total_w:,} words "
                  f"({r['kept_words']/total_w*100:.1f}%)")


if __name__ == "__main__":
    main()
