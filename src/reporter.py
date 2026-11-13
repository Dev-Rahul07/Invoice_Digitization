"""Simple JSON Reporting Module for Invoice Digitization.

Generates:
1. output/results.json - All processed scans and extracted fields.
2. output/needs_review.json - Only records needing manual review.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def build_report_record(
    filename: str,
    extracted_data: Dict[str, Any],
    validation_status: str,
    duplicate_status: str,
    invoice_id: Optional[int],
    review_reason: Optional[str] = None,
    review_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Format one processed invoice scan into a clean report dictionary."""
    sub_minor = extracted_data.get("subtotal_minor")
    tax_minor = extracted_data.get("tax_minor")
    tot_minor = extracted_data.get("total_minor")

    reasons = review_reasons or ([review_reason] if review_reason else [])

    return {
        "source_filename": filename,
        "validation_status": validation_status,
        "duplicate_status": duplicate_status,
        "invoice_id": invoice_id,
        "extracted_fields": {
            "invoice_number": extracted_data.get("invoice_number"),
            "vendor": extracted_data.get("vendor"),
            "invoice_date": extracted_data.get("invoice_date"),
            "currency": extracted_data.get("currency"),
            "subtotal": round(sub_minor / 100.0, 2) if sub_minor is not None else None,
            "tax": round(tax_minor / 100.0, 2) if tax_minor is not None else None,
            "total": round(tot_minor / 100.0, 2) if tot_minor is not None else None,
            "subtotal_minor": sub_minor,
            "tax_minor": tax_minor,
            "total_minor": tot_minor,
        },
        "review_reason": review_reason,
        "review_reasons": reasons,
        "processed_at": datetime.now().isoformat(),
    }


def save_json(data: Any, file_path: Path | str) -> None:
    """Helper to write data to disk as formatted JSON."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def generate_results_report(
    records: List[Dict[str, Any]], output_path: Path | str
) -> None:
    """Save all processed records into output/results.json."""
    save_json(records, output_path)


def generate_needs_review_report(
    records: List[Dict[str, Any]], output_path: Path | str
) -> None:
    """Save only records that failed validation into output/needs_review.json."""
    required = ["invoice_number", "vendor", "invoice_date", "currency", "subtotal", "tax", "total"]
    review_records = []

    for rec in records:
        if rec.get("validation_status") in ("needs_review", "failed"):
            fields = rec.get("extracted_fields", {})
            missing = [f for f in required if fields.get(f) is None]

            review_records.append(
                {
                    "source_filename": rec.get("source_filename"),
                    "validation_status": rec.get("validation_status"),
                    "review_reasons": rec.get("review_reasons") or [rec.get("review_reason")],
                    "missing_or_ambiguous_fields": missing,
                    "extracted_values": fields,
                    "processed_at": rec.get("processed_at"),
                }
            )

    save_json(review_records, output_path)
