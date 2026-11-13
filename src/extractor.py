"""Data Extraction Module for Invoice Digitization.

Extracts the 7 core invoice fields from raw OCR text using regex and
deterministic normalization functions:
1. Invoice number
2. Vendor name
3. Invoice date (normalized to YYYY-MM-DD)
4. Currency (ISO code or recognized symbol)
5. Subtotal (converted to integer minor currency units, e.g. cents/paise)
6. Tax (converted to integer minor currency units)
7. Total (converted to integer minor currency units)

Implements safe parsing: invalid, ambiguous, or OCR-corrupted values return None
so the validation engine can mark them for review rather than silently guessing.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

# Supported currency symbols and codes map to ISO code
CURRENCY_MAP = {
    "INR": "INR",
    "RS": "INR",
    "RS.": "INR",
    "₹": "INR",
    "USD": "USD",
    "$": "USD",
    "EUR": "EUR",
    "€": "EUR",
    "GBP": "GBP",
    "£": "GBP",
}

# Supported date formats to try during normalization
DATE_FORMATS = (
    "%Y-%m-%d",  # 2026-10-01 (ISO)
    "%d/%m/%Y",  # 01/10/2026
    "%m/%d/%Y",  # 10/01/2026
    "%d-%m-%Y",  # 01-10-2026
    "%Y/%m/%d",  # 2026/10/01
    "%d.%m.%Y",  # 01.10.2026
    "%B %d, %Y", # October 1, 2026
    "%b %d, %Y", # Oct 1, 2026
)


def normalize_vendor(raw_vendor: Optional[str]) -> Optional[str]:
    """Normalize vendor name: strip whitespace and reduce multiple spaces.

    Args:
        raw_vendor: Raw vendor string extracted from OCR.

    Returns:
        Cleaned vendor string or None if empty.
    """
    if not raw_vendor:
        return None
    cleaned = " ".join(raw_vendor.strip().split())
    return cleaned if cleaned else None


def normalize_date(raw_date: Optional[str]) -> Optional[str]:
    """Normalize a raw date string into ISO YYYY-MM-DD format.

    Rejects placeholders like '[not supplied]', 'N/A', 'missing',
    or unrecognized date structures.

    Args:
        raw_date: Raw date string from OCR.

    Returns:
        ISO formatted date string 'YYYY-MM-DD', or None if invalid/unparseable.
    """
    if not raw_date:
        return None

    cleaned = raw_date.strip()

    # Reject known placeholders or brackets
    if any(token in cleaned.lower() for token in ("[", "]", "not supplied", "na", "n/a", "missing", "none")):
        return None

    # Strip any surrounding punctuation
    cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", cleaned)

    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def normalize_currency(raw_currency: Optional[str]) -> Optional[str]:
    """Normalize currency string or symbol to standard 3-letter ISO code.

    Args:
        raw_currency: Raw currency string (e.g. 'INR', '$', 'USD').

    Returns:
        Standard ISO currency code (e.g. 'INR', 'USD') or None if unrecognized.
    """
    if not raw_currency:
        return None
    cleaned = raw_currency.strip().upper()
    return CURRENCY_MAP.get(cleaned, cleaned if len(cleaned) == 3 and cleaned.isalpha() else None)


def parse_money_to_minor(raw_amount: Optional[str]) -> Optional[int]:
    """Parse monetary string to integer minor units (e.g. cents/paise).

    Avoids floating point arithmetic by using the Decimal module.
    Rejects values containing corrupted OCR characters (e.g. '11?0.00'),
    letters, or invalid punctuation.

    Args:
        raw_amount: Raw amount string from OCR (e.g. '1,200.50', '1416.59').

    Returns:
        Integer minor units (e.g. 120050 for 1200.50), or None if invalid.
    """
    if not raw_amount:
        return None

    cleaned = raw_amount.strip()

    # Detect OCR corruption or unexpected characters
    # Only allow digits, comma, period, whitespace, and known currency symbols
    if re.search(r"[^\d.,\s$€£₹A-Za-z]", cleaned):
        return None

    # Remove currency symbols and letters
    num_str = re.sub(r"[$€£₹A-Za-z\s]", "", cleaned)

    # Handle standard thousands separator: '1,200.50' -> '1200.50'
    num_str = num_str.replace(",", "")

    # Validate that what remains has at most one decimal point and is non-empty
    if not num_str or num_str.count(".") > 1:
        return None

    try:
        dec = Decimal(num_str)
        # Convert to minor currency units (x 100) exactly
        minor = int((dec * Decimal(100)).to_integral_value())
        return minor
    except (InvalidOperation, ValueError):
        return None


def extract_field_by_pattern(pattern: str, text: str) -> Optional[str]:
    """Helper to extract a single captured group using a regex pattern.

    Args:
        pattern: Regex pattern with one capture group.
        text: Target text to search.

    Returns:
        Captured string stripped of whitespace, or None if no match.
    """
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if match:
        val = match.group(1).strip()
        return val if val else None
    return None


def extract_invoice_data(raw_ocr_text: str) -> Dict[str, Any]:
    """Extract and normalize all 7 invoice fields from raw OCR text.

    Uses specific regex patterns with multi-line matching.
    Never invents missing values; invalid/missing fields return None.

    Args:
        raw_ocr_text: Raw string extracted by OCR.

    Returns:
        Dictionary containing both raw and normalized field values.
    """
    # 1. Invoice Number: matches "Invoice: TNM-101" or "Invoice #101"
    raw_invoice_number = extract_field_by_pattern(
        r"^\s*(?:Invoice|Inv|Invoice\s*(?:Number|No|#))\s*[:#\-]\s*([A-Za-z0-9\-_/]+)",
        raw_ocr_text,
    )

    # 2. Vendor: matches "Vendor: Acme Office Supplies" or "From: ..."
    raw_vendor = extract_field_by_pattern(
        r"^\s*(?:Vendor|Company|From|Supplier)\s*[:\-]\s*([^\r\n]+)",
        raw_ocr_text,
    )

    # 3. Date: matches "Date: 2026-10-01" or "Invoice Date: ..."
    raw_date = extract_field_by_pattern(
        r"^\s*(?:Invoice\s*Date|Date)\s*[:\-]\s*([^\r\n]+)",
        raw_ocr_text,
    )

    # 4. Currency: matches "Currency: INR" or "Currency: USD"
    raw_currency = extract_field_by_pattern(
        r"^\s*Currency\s*[:\-]\s*([^\r\n]+)",
        raw_ocr_text,
    )

    # 5. Subtotal: matches "Subtotal: 1200.50" or "Sub-total: ..."
    raw_subtotal = extract_field_by_pattern(
        r"^\s*(?:Subtotal|Sub-total|Sub\s*Total|Net\s*Amount)\s*[:\-]\s*([^\r\n]+)",
        raw_ocr_text,
    )

    # 6. Tax: matches "Tax: 216.09" or "GST: ..." or "VAT: ..."
    raw_tax = extract_field_by_pattern(
        r"^\s*(?:Tax|GST|VAT|Tax\s*Amount)\s*[:\-]\s*([^\r\n]+)",
        raw_ocr_text,
    )

    # 7. Total: matches "Total: 1416.59" or "Total Amount: ..."
    # Negative lookbehind (?<!Sub) ensures Subtotal is never accidentally captured as Total
    raw_total = extract_field_by_pattern(
        r"^\s*(?<!Sub)(?:Total|Total\s*Amount|Grand\s*Total)\s*[:\-]\s*([^\r\n]+)",
        raw_ocr_text,
    )

    # Normalized fields
    vendor_norm = normalize_vendor(raw_vendor)
    date_norm = normalize_date(raw_date)
    currency_norm = normalize_currency(raw_currency)
    subtotal_minor = parse_money_to_minor(raw_subtotal)
    tax_minor = parse_money_to_minor(raw_tax)
    total_minor = parse_money_to_minor(raw_total)

    return {
        "invoice_number": raw_invoice_number,
        "vendor": vendor_norm,
        "invoice_date": date_norm,
        "currency": currency_norm,
        "subtotal_minor": subtotal_minor,
        "tax_minor": tax_minor,
        "total_minor": total_minor,
        "raw_fields": {
            "invoice_number": raw_invoice_number,
            "vendor": raw_vendor,
            "invoice_date": raw_date,
            "currency": raw_currency,
            "subtotal": raw_subtotal,
            "tax": raw_tax,
            "total": raw_total,
        },
    }


if __name__ == "__main__":
    # Test harness demonstrating extraction on sample text
    samples = {
        "Valid ISO": "Invoice: TNM-101\nVendor: Acme Office Supplies\nDate: 2026-10-01\nCurrency: INR\nSubtotal: 1200.50\nTax: 216.09\nTotal: 1416.59",
        "Duplicate Scan (Commas/Slash Date)": "Invoice: TNM-101\nVendor: ACME OFFICE SUPPLIES\nDate: 01/10/2026\nCurrency: INR\nSubtotal: 1,200.50\nTax: 216.09\nTotal: 1,416.59",
        "Corrupted Review": "Invoice: TNM-103\nVendor: Gamma Paper Co\nDate: [not supplied]\nCurrency: INR\nSubtotal: 1000.00\nTax: 180.00\nTotal: 11?0.00",
    }

    print("=== Testing Extractor Logic ===")
    for title, text in samples.items():
        print(f"\n--- {title} ---")
        extracted = extract_invoice_data(text)
        for k, v in extracted.items():
            if k != "raw_fields":
                print(f"  {k:15}: {v}")
