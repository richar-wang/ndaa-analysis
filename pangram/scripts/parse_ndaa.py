"""
parse_ndaa.py - Parse NDAA XML and split into individual sections

Reads raw XML from data/raw-xml/, extracts individual sections,
and saves each as a .txt file in data/sections/[ndaa-year]/.

Also generates metadata.csv for each NDAA with section info.
Skips sections under 50 words (Pangram minimum).
"""

import os
import re
import csv
import sys
from lxml import etree
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_XML_DIR = os.path.join(BASE_DIR, "data", "raw-xml")
SECTIONS_DIR = os.path.join(BASE_DIR, "data", "sections")

MIN_WORDS = 50

NDAA_YEARS = ["fy2020", "fy2023", "fy2024", "fy2026"]


def slugify(text):
    """Convert a section title to a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    text = text.strip("_")
    return text[:80]


def extract_text(element):
    """Extract all text content from an XML element, stripping tags."""
    return " ".join(element.itertext()).strip()


def word_count(text):
    """Count words in text."""
    return len(text.split())


def _get_local_tag(element):
    """Get the local tag name, stripping any namespace."""
    tag = element.tag
    if isinstance(tag, str) and "}" in tag:
        return etree.QName(tag).localname
    return tag if isinstance(tag, str) else ""


def _get_element_label(parent_element):
    """Get the best label for a structural element (division/title/subtitle).

    Prefers <heading>/<header> (e.g. "PROCUREMENT") over <num> (e.g. "TITLE I—").
    Falls back to <num> with trailing em-dashes stripped.
    """
    heading_text = None
    num_text = None

    for child in parent_element:
        child_tag = _get_local_tag(child).lower()
        if child_tag in ("heading", "header") and heading_text is None:
            heading_text = extract_text(child)
        elif child_tag in ("enum", "num") and num_text is None:
            num_text = extract_text(child)

    if heading_text:
        return heading_text.strip().rstrip("\u2014").strip()  # strip trailing em-dash
    if num_text:
        return num_text.strip().rstrip("\u2014").strip()
    if "identifier" in parent_element.attrib:
        return parent_element.attrib["identifier"]
    return None


def find_current_division(section_element):
    """Walk up the tree to find the parent division/title."""
    parent = section_element.getparent()
    while parent is not None:
        tag = _get_local_tag(parent).lower()
        if tag in ("division", "title"):
            label = _get_element_label(parent)
            if label:
                return label
        parent = parent.getparent()
    return "Unknown"


def find_current_subtitle(section_element):
    """Walk up the tree to find the parent subtitle."""
    parent = section_element.getparent()
    while parent is not None:
        tag = _get_local_tag(parent).lower()
        if tag == "subtitle":
            label = _get_element_label(parent)
            if label:
                return label
        parent = parent.getparent()
    return ""


def parse_govinfo_xml(xml_path):
    """Parse govinfo.gov public law XML format."""
    tree = etree.parse(xml_path)
    root = tree.getroot()

    # Remove namespace prefixes for easier querying
    nsmap = root.nsmap
    ns = nsmap.get(None, "")

    sections = []

    # Try multiple XPath patterns since govinfo XML structure varies
    section_elements = []

    # Pattern 1: USLM format (newer bills)
    if ns:
        section_elements = root.findall(f".//{{{ns}}}section")
    if not section_elements:
        section_elements = root.findall(".//section")

    # Pattern 2: Traditional bill XML format
    if not section_elements:
        # Try with legis-body/*/section pattern
        section_elements = root.findall(".//legis-body//section")

    if not section_elements:
        # Fallback: try BeautifulSoup for messy XML
        print("  Falling back to BeautifulSoup parser...")
        return parse_with_beautifulsoup(xml_path)

    print(f"  Found {len(section_elements)} section elements")

    for elem in section_elements:
        # Extract section number
        sec_num = ""
        sec_title = ""

        # Try enum/header pattern (traditional)
        for child in elem:
            child_tag = _get_local_tag(child).lower()
            if child_tag in ("enum", "num"):
                sec_num = extract_text(child).strip().rstrip(".")
            elif child_tag in ("header", "heading"):
                sec_title = extract_text(child).strip()

        # Try identifier attribute (USLM)
        if not sec_num and "identifier" in elem.attrib:
            sec_num = elem.attrib["identifier"]

        # Extract just the section number digits
        num_match = re.search(r"(\d+[a-zA-Z]?)", sec_num)
        if num_match:
            sec_num = num_match.group(1)
        else:
            continue  # skip if no section number found

        # Get full text of the section
        text = extract_text(elem)
        wc = word_count(text)

        # Get structural context
        division = find_current_division(elem)
        subtitle = find_current_subtitle(elem)

        sections.append({
            "section_number": sec_num,
            "title": sec_title,
            "division": division,
            "subtitle": subtitle,
            "text": text,
            "word_count": wc,
        })

    return sections


def parse_with_beautifulsoup(xml_path):
    """Fallback parser using BeautifulSoup for non-standard XML."""
    with open(xml_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    soup = BeautifulSoup(content, "lxml-xml")
    sections = []

    for section_tag in soup.find_all("section"):
        sec_num = ""
        sec_title = ""

        # Look for enum and header children
        enum_tag = section_tag.find(["enum", "num"])
        header_tag = section_tag.find(["header", "heading"])

        if enum_tag:
            sec_num = enum_tag.get_text(strip=True).rstrip(".")
        if header_tag:
            sec_title = header_tag.get_text(strip=True)

        num_match = re.search(r"(\d+[a-zA-Z]?)", sec_num)
        if num_match:
            sec_num = num_match.group(1)
        else:
            continue

        text = section_tag.get_text(" ", strip=True)
        wc = word_count(text)

        # Try to find division context
        division = "Unknown"
        subtitle = ""
        parent = section_tag.parent
        while parent:
            if parent.name and parent.name.lower() in ("division", "title"):
                header = parent.find(["heading", "header", "num"])
                if header:
                    division = header.get_text(strip=True)
                break
            if parent.name and parent.name.lower() == "subtitle":
                header = parent.find(["heading", "header", "num"])
                if header:
                    subtitle = header.get_text(strip=True)
            parent = parent.parent

        sections.append({
            "section_number": sec_num,
            "title": sec_title,
            "division": division,
            "subtitle": subtitle,
            "text": text,
            "word_count": wc,
        })

    return sections


def process_ndaa(year):
    """Process a single NDAA XML file."""
    xml_path = os.path.join(RAW_XML_DIR, f"ndaa_{year}.xml")
    if not os.path.exists(xml_path):
        print(f"\n[{year.upper()}] XML not found: {xml_path}, skipping")
        return

    print(f"\n[{year.upper()}] Parsing {xml_path}")
    print(f"  File size: {os.path.getsize(xml_path):,} bytes")

    sections = parse_govinfo_xml(xml_path)

    if not sections:
        print(f"  WARNING: No sections extracted from {year}")
        return

    # Create output directory
    year_dir = os.path.join(SECTIONS_DIR, year)
    os.makedirs(year_dir, exist_ok=True)

    # Filter and save sections
    saved = 0
    skipped_short = 0
    division_counts = {}
    total_words = 0

    metadata_rows = []

    for sec in sections:
        if sec["word_count"] < MIN_WORDS:
            skipped_short += 1
            continue

        # Build filename
        title_slug = slugify(sec["title"]) if sec["title"] else "untitled"
        filename = f"sec{sec['section_number']}_{title_slug}.txt"
        filepath = os.path.join(year_dir, filename)

        # Save section text
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(sec["text"])

        # Track stats
        saved += 1
        total_words += sec["word_count"]
        div = sec["division"] or "Unknown"
        division_counts[div] = division_counts.get(div, 0) + 1

        metadata_rows.append({
            "section_number": sec["section_number"],
            "title": sec["title"],
            "division": sec["division"],
            "subtitle": sec["subtitle"],
            "word_count": sec["word_count"],
            "file_path": filename,
        })

    # Save metadata CSV
    csv_path = os.path.join(year_dir, "metadata.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "section_number", "title", "division", "subtitle", "word_count", "file_path"
        ])
        writer.writeheader()
        writer.writerows(metadata_rows)

    # Print summary
    print(f"\n  Summary for {year.upper()}:")
    print(f"    Total sections found: {len(sections)}")
    print(f"    Sections saved (>= {MIN_WORDS} words): {saved}")
    print(f"    Sections skipped (< {MIN_WORDS} words): {skipped_short}")
    print(f"    Total word count: {total_words:,}")
    print(f"    Metadata CSV: {csv_path}")
    print(f"\n    Sections by division:")
    for div, count in sorted(division_counts.items()):
        print(f"      {div}: {count}")


def main():
    years = sys.argv[1:] if len(sys.argv) > 1 else NDAA_YEARS

    print("NDAA XML Parser")
    print("=" * 60)

    for year in years:
        process_ndaa(year)

    print("\n" + "=" * 60)
    print("Done.")


if __name__ == "__main__":
    main()
