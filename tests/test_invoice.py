"""Automated Test Suite for Invoice Digitization.

Tests all required acceptance criteria using fixed OCR text fixtures and pure functions:
1. Test 1: Valid invoice extraction, math validation, and SQLite persistence.
2. Test 2: Duplicate scan linking and idempotency using duplicate_key.
3. Test 3: Incomplete / ambiguous invoice enters needs_review (invoices table untouched).
4. Test 4: Financial math discrepancy (Subtotal + Tax != Total).
5. Test 5: Corrupted or unreadable image handling without halting.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from src.database import (
    STATUS_ALREADY_PROCESSED,
    STATUS_DUPLICATE_SCAN,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_VALID,
    create_database,
    get_summary_counts,
    process_invoice,
)
from src.extractor import extract_invoice_data
from src.validator import validate_invoice

# Fixed OCR Text Fixtures
SAMPLE_VALID_OCR = """SYNTHETIC TEST INVOICE

Invoice: TNM-101

Vendor: Acme Office Supplies

Date: 2026-10-01

Currency: INR

Subtotal: 1200.50

Tax: 216.09

Total: 1416.59
"""

SAMPLE_DUPLICATE_OCR = """SYNTHETIC TEST INVOICE

Invoice: TNM-101

Vendor: ACME OFFICE SUPPLIES

Date: 01/10/2026

Currency: INR

Subtotal: 1,200.50

Tax: 216.09

Total: 1,416.59
"""

SAMPLE_AMBIGUOUS_OCR = """SYNTHETIC TEST INVOICE

Vendor: Charlie Logistics

Date: [not supplied]

Currency: INR

Subtotal: 1000.00

Tax: 180.00

Total: 1170.00
"""

SAMPLE_MATH_MISMATCH_OCR = """SYNTHETIC TEST INVOICE

Invoice: TNM-999

Vendor: Delta Services

Date: 2026-10-15

Currency: USD

Subtotal: 100.00

Tax: 20.00

Total: 125.00
"""


def test_valid_invoice_extraction_and_validation(tmp_path: Path) -> None:
    """Test 1: Clean invoice extracts all 7 fields and passes mathematical validation."""
    db_file = tmp_path / "test_valid.db"
    conn = create_database(db_file)

    # 1. Extraction
    extracted = extract_invoice_data(SAMPLE_VALID_OCR)
    assert extracted["invoice_number"] == "TNM-101"
    assert extracted["vendor"] == "Acme Office Supplies"
    assert extracted["invoice_date"] == "2026-10-01"
    assert extracted["currency"] == "INR"
    assert extracted["subtotal_minor"] == 120050
    assert extracted["tax_minor"] == 21609
    assert extracted["total_minor"] == 141659

    # 2. Validation
    result = validate_invoice(extracted)
    assert result["is_valid"] is True
    assert result["status"] == STATUS_VALID
    assert len(result["reasons"]) == 0

    # 3. Database Storage (Pure Function)
    db_res = process_invoice(
        conn=conn,
        filename="invoice-101.png",
        filepath="input/invoice-101.png",
        raw_ocr_text=SAMPLE_VALID_OCR,
        extracted_data=extracted,
        validation=result,
    )
    assert db_res["status"] == STATUS_VALID
    assert db_res["invoice_id"] == 1

    counts = get_summary_counts(conn)
    assert counts["valid_invoices_stored"] == 1
    assert counts["total_images_processed"] == 1

    conn.close()


def test_duplicate_and_repeated_import(tmp_path: Path) -> None:
    """Test 2: Duplicate scan linking and idempotency using duplicate_key.

    Verifies:
    1. First import creates 1 invoice and 1 source record.
    2. Different image scan of same invoice links to existing invoice without creating a second invoice row.
    3. Re-processing the exact same source file does not create duplicate invoice or source records.
    """
    db_file = tmp_path / "test_dedup.db"
    conn = create_database(db_file)

    # Ingest Scan 1 (Original physical file)
    ext_1 = extract_invoice_data(SAMPLE_VALID_OCR)
    val_1 = validate_invoice(ext_1)
    res_1 = process_invoice(
        conn=conn,
        filename="invoice-101.png",
        filepath="input/invoice-101.png",
        raw_ocr_text=SAMPLE_VALID_OCR,
        extracted_data=ext_1,
        validation=val_1,
    )
    assert res_1["status"] == STATUS_VALID
    assert res_1["invoice_id"] == 1

    counts = get_summary_counts(conn)
    assert counts["valid_invoices_stored"] == 1
    assert counts["total_images_processed"] == 1

    # Ingest Scan 2 (Different scan of same invoice: all-caps vendor, slash date)
    ext_2 = extract_invoice_data(SAMPLE_DUPLICATE_OCR)
    val_2 = validate_invoice(ext_2)
    res_2 = process_invoice(
        conn=conn,
        filename="invoice-101-copy.png",
        filepath="input/invoice-101-copy.png",
        raw_ocr_text=SAMPLE_DUPLICATE_OCR,
        extracted_data=ext_2,
        validation=val_2,
    )
    assert res_2["status"] == STATUS_DUPLICATE_SCAN
    assert res_2["is_duplicate_invoice"] is True
    assert res_2["invoice_id"] == 1  # Linked to original invoice!

    # Only 1 unique invoice record must exist, but 2 source scans must be retained
    counts_after_copy = get_summary_counts(conn)
    assert counts_after_copy["valid_invoices_stored"] == 1
    assert counts_after_copy["duplicate_scans"] == 1
    assert counts_after_copy["total_images_processed"] == 2

    # Ingest Scan 1 AGAIN (Simulating re-running the tool on the same folder)
    res_repeat = process_invoice(
        conn=conn,
        filename="invoice-101.png",
        filepath="input/invoice-101.png",
        raw_ocr_text=SAMPLE_VALID_OCR,
        extracted_data=ext_1,
        validation=val_1,
    )
    assert res_repeat["status"] == STATUS_ALREADY_PROCESSED
    assert res_repeat["is_duplicate_file"] is True

    # Database counts must remain strictly identical
    counts_after_repeat = get_summary_counts(conn)
    assert counts_after_repeat["valid_invoices_stored"] == 1
    assert counts_after_repeat["duplicate_scans"] == 1
    assert counts_after_repeat["total_images_processed"] == 2

    conn.close()


def test_incomplete_or_ambiguous_invoice(tmp_path: Path) -> None:
    """Test 3: Incomplete/ambiguous invoice enters needs_review and is NOT stored in invoices table."""
    db_file = tmp_path / "test_review.db"
    conn = create_database(db_file)

    extracted = extract_invoice_data(SAMPLE_AMBIGUOUS_OCR)
    result = validate_invoice(extracted)

    # 1. Validation must fail and enter needs_review
    assert result["is_valid"] is False
    assert result["status"] == STATUS_NEEDS_REVIEW

    # 2. Clear review reasons must be generated
    reason_str = result["review_reason"]
    assert "invoice date" in reason_str.lower()
    assert ("total amount" in reason_str.lower() or "math mismatch" in reason_str.lower())

    # 3. Insert into database
    db_res = process_invoice(
        conn=conn,
        filename="invoice-103-review.png",
        filepath="input/invoice-103-review.png",
        raw_ocr_text=SAMPLE_AMBIGUOUS_OCR,
        extracted_data=extracted,
        validation=result,
    )

    # 4. Must NOT create an invoice record
    assert db_res["invoice_id"] is None
    assert db_res["status"] == STATUS_NEEDS_REVIEW

    counts = get_summary_counts(conn)
    assert counts["valid_invoices_stored"] == 0
    assert counts["needs_review"] == 1
    assert counts["total_images_processed"] == 1

    # Verify directly via SQL that invoices table is empty
    inv_count = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    assert inv_count == 0

    # But invoice_sources has the full record
    source_row = conn.execute(
        "SELECT * FROM invoice_sources WHERE filename = ?",
        ("invoice-103-review.png",),
    ).fetchone()
    assert source_row is not None
    assert source_row["invoice_id"] is None
    assert source_row["processing_status"] == STATUS_NEEDS_REVIEW
    assert source_row["review_reason"] is not None

    conn.close()


def test_math_mismatch_fails_validation() -> None:
    """Test 4 (Bonus): Subtotal + Tax != Total triggers clear review reason."""
    extracted = extract_invoice_data(SAMPLE_MATH_MISMATCH_OCR)
    result = validate_invoice(extracted)

    assert result["is_valid"] is False
    assert result["status"] == STATUS_NEEDS_REVIEW
    assert "math mismatch" in result["review_reason"].lower()


def test_corrupted_image_handling(tmp_path: Path) -> None:
    """Test 5 (Bonus): Failed or corrupted image preserves source scan without halting."""
    db_file = tmp_path / "test_failed.db"
    conn = create_database(db_file)

    validation = {
        "is_valid": False,
        "status": STATUS_FAILED,
        "reasons": ["Corrupt image format: cannot identify image file"],
        "review_reason": "Corrupt image format: cannot identify image file",
    }
    empty_extracted = {
        "invoice_number": None,
        "vendor": None,
        "invoice_date": None,
        "currency": None,
        "subtotal_minor": None,
        "tax_minor": None,
        "total_minor": None,
    }

    db_res = process_invoice(
        conn=conn,
        filename="corrupted_invoice.png",
        filepath="input/corrupted_invoice.png",
        raw_ocr_text="",
        extracted_data=empty_extracted,
        validation=validation,
    )

    assert db_res["status"] == STATUS_FAILED
    assert db_res["invoice_id"] is None

    counts = get_summary_counts(conn)
    assert counts["failed_images"] == 1
    assert counts["valid_invoices_stored"] == 0

    conn.close()
