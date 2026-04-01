"""
fetch_ndaa.py - Download NDAA full text XML from govinfo.gov

Downloads FY2026 (P.L. 119-60), FY2024 (P.L. 118-31), FY2023 (P.L. 117-263),
and FY2020 (P.L. 116-92) NDAAs in XML format.

FY2020 and FY2023 predate ChatGPT (Nov 2022) and serve as false positive controls.

Uses two URL strategies:
  1. PLAW USLM XML (structured public law schema) — best for parsing
  2. BILLS enrolled XML (legislative bill schema) — fallback, always available
"""

import os
import sys
import time
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_XML_DIR = os.path.join(BASE_DIR, "data", "raw-xml")

# Enrolled bill identifiers for the BILLS collection on govinfo.gov
# FY2026 enrolled as S.1071 (not HR.5009 — the Senate bill was the vehicle)
NDAA_BILLS = {
    "fy2026": {
        "public_law": "119-60",
        "bills_pkg": "BILLS-119s1071enr",
        "plaw_pkg": "PLAW-119publ60",
        "description": "FY2026 NDAA (P.L. 119-60, signed Dec 18, 2025)",
    },
    "fy2024": {
        "public_law": "118-31",
        "bills_pkg": "BILLS-118hr2670enr",
        "plaw_pkg": "PLAW-118publ31",
        "description": "FY2024 NDAA (P.L. 118-31)",
    },
    "fy2023": {
        "public_law": "117-263",
        "bills_pkg": "BILLS-117hr7776enr",
        "plaw_pkg": "PLAW-117publ263",
        "description": "FY2023 NDAA (P.L. 117-263) [pre-ChatGPT control]",
    },
    "fy2020": {
        "public_law": "116-92",
        "bills_pkg": "BILLS-116s1790enr",
        "plaw_pkg": "PLAW-116publ92",
        "description": "FY2020 NDAA (P.L. 116-92) [pre-ChatGPT control]",
    },
}


def is_valid_xml(content):
    """Check if content looks like actual XML, not an error page."""
    # govinfo returns HTML error pages with 200 status
    head = content[:500].lower() if isinstance(content, str) else content[:500].decode("utf-8", errors="ignore").lower()
    if "<!doctype html" in head or "<html" in head:
        return False
    if "<?xml" in head or "<bill" in head or "<lawDoc" in head or "<pLaw" in head or "<uslm" in head:
        return True
    return False


def fetch_bill_xml(year, bill):
    """Download the XML for a single NDAA bill."""
    output_path = os.path.join(RAW_XML_DIR, f"ndaa_{year}.xml")

    if os.path.exists(output_path):
        size = os.path.getsize(output_path)
        # Re-download if file is suspiciously small or is an error page
        with open(output_path, "rb") as f:
            head = f.read(500)
        if size > 100_000 and is_valid_xml(head):
            print(f"  Already exists: {output_path} ({size:,} bytes), skipping")
            return True
        else:
            print(f"  Existing file invalid ({size:,} bytes), re-downloading...")

    plaw_pkg = bill["plaw_pkg"]
    bills_pkg = bill["bills_pkg"]

    # Strategy 1: PLAW USLM XML (better structure, uses /uslm/ not /xml/)
    plaw_url = f"https://www.govinfo.gov/content/pkg/{plaw_pkg}/uslm/{plaw_pkg}.xml"
    print(f"  Trying PLAW USLM: {plaw_url}")
    try:
        resp = requests.get(plaw_url, timeout=120)
        if resp.status_code == 200 and is_valid_xml(resp.content):
            with open(output_path, "wb") as f:
                f.write(resp.content)
            print(f"  Saved: {output_path} ({len(resp.content):,} bytes) [PLAW USLM]")
            return True
        else:
            print(f"  PLAW USLM: status {resp.status_code} or not valid XML")
    except requests.RequestException as e:
        print(f"  PLAW USLM failed: {e}")

    # Strategy 2: BILLS enrolled XML (always available)
    bills_url = f"https://www.govinfo.gov/content/pkg/{bills_pkg}/xml/{bills_pkg}.xml"
    print(f"  Trying BILLS enrolled: {bills_url}")
    try:
        resp = requests.get(bills_url, timeout=120)
        if resp.status_code == 200 and is_valid_xml(resp.content):
            with open(output_path, "wb") as f:
                f.write(resp.content)
            print(f"  Saved: {output_path} ({len(resp.content):,} bytes) [BILLS enrolled]")
            return True
        else:
            print(f"  BILLS enrolled: status {resp.status_code} or not valid XML")
    except requests.RequestException as e:
        print(f"  BILLS enrolled failed: {e}")

    pl = bill["public_law"]
    print(f"  WARNING: Could not download XML for {year}.")
    print(f"  Try manually: https://www.govinfo.gov/app/details/{plaw_pkg}")
    return False


def main():
    os.makedirs(RAW_XML_DIR, exist_ok=True)

    print("Fetching NDAA XML files from govinfo.gov\n")

    results = {}
    for year, bill in NDAA_BILLS.items():
        print(f"[{year.upper()}] {bill['description']}")
        success = fetch_bill_xml(year, bill)
        results[year] = success
        time.sleep(2)
        print()

    print("\n=== Summary ===")
    for year, success in results.items():
        status = "OK" if success else "FAILED"
        path = os.path.join(RAW_XML_DIR, f"ndaa_{year}.xml")
        if success and os.path.exists(path):
            size = os.path.getsize(path)
            print(f"  {year.upper()}: {status} ({size:,} bytes)")
        else:
            print(f"  {year.upper()}: {status}")


if __name__ == "__main__":
    main()
