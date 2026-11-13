"""Main Pipeline Orchestrator.

Connects all implemented functions into one simple master function:
1. create_database()      -> Setup SQLite tables
2. process_all_images()    -> Run OCR on all images in 'input/'
3. extract_invoice_data()  -> Extract fields using regex & normalization
4. validate_invoice()      -> Validate presence & math reconciliation
5. process_invoice()       -> Store in SQLite with duplicate detection
6. build_report_record()   -> Format report entry
7. generate_reports()      -> Save results.json & needs_review.json
8. get_summary_counts()    -> Fetch final counts from SQLite
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path so 'python src/main.py' works directly
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from typing import Any, Dict

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
from src.ocr import process_all_images
from src.reporter import (
    build_report_record,
    generate_needs_review_report,
    generate_results_report,
)
from src.validator import validate_invoice


def run_pipeline(
    input_folder: str = "input",
    output_folder: str = "output",
    raw_ocr_folder: str = "raw_ocr",
    db_file: str = "invoices.db",
) -> Dict[str, int]:
    """Single master function that connects and executes all pipeline steps."""
    input_dir = Path(input_folder)
    output_dir = Path(output_folder)
    raw_ocr_dir = Path(raw_ocr_folder)
    db_path = Path(db_file)

    # 1. Setup SQLite Database
    conn = create_database(db_path)

    # 2. OCR Stage: Read all images from input/
    ocr_results = process_all_images(input_dir, raw_ocr_dir=raw_ocr_dir)

    all_records = []

    try:
        # 3. Process every image: Extract -> Validate -> Store
        for item in ocr_results:
            filename = item["filename"]
            raw_text = item["raw_text"]
            filepath = str(input_dir / filename)

            if not item["success"]:
                # OCR Failure (corrupt image or unreadable)
                err = item["error"] or "OCR processing failed"
                extracted = {
                    "invoice_number": None, "vendor": None, "invoice_date": None,
                    "currency": None, "subtotal_minor": None, "tax_minor": None, "total_minor": None,
                }
                validation = {
                    "is_valid": False, "status": STATUS_FAILED,
                    "reasons": [err], "review_reason": err,
                }
            else:
                # Call Extractor function
                extracted = extract_invoice_data(raw_text)
                # Call Validator function
                validation = validate_invoice(extracted)

            # Call Database Storage & Deduplication function
            db_res = process_invoice(
                conn=conn,
                filename=filename,
                filepath=filepath,
                raw_ocr_text=raw_text,
                extracted_data=extracted,
                validation=validation,
            )

            # Format record for reporting
            dup_status = (
                "already_processed" if db_res["status"] == STATUS_ALREADY_PROCESSED
                else "duplicate_scan" if db_res["status"] == STATUS_DUPLICATE_SCAN
                else "original"
            )
            val_status = (
                STATUS_FAILED if not item["success"]
                else STATUS_VALID if validation["is_valid"]
                else STATUS_NEEDS_REVIEW
            )

            record = build_report_record(
                filename=filename,
                extracted_data=extracted,
                validation_status=val_status,
                duplicate_status=dup_status,
                invoice_id=db_res["invoice_id"],
                review_reason=db_res["review_reason"],
                review_reasons=validation["reasons"],
            )
            all_records.append(record)

        # 4. Reporter Stage: Generate JSON files
        generate_results_report(all_records, output_dir / "results.json")
        generate_needs_review_report(all_records, output_dir / "needs_review.json")

        # 5. Query final statistics from SQLite
        summary = get_summary_counts(conn)
        return summary

    finally:
        conn.close()


def main() -> None:
    """Run the entire pipeline when executing the file."""
    print("Starting Invoice Digitization Pipeline...")
    summary = run_pipeline()

    print("\n=============================================")
    print("      INVOICE DIGITIZATION SUMMARY")
    print("=============================================")
    print(f"Total images processed : {summary['total_images_processed']}")
    print(f"Valid invoices stored  : {summary['valid_invoices_stored']}")
    print(f"Duplicate scans        : {summary['duplicate_scans']}")
    print(f"Needs review           : {summary['needs_review']}")
    print(f"Failed images          : {summary['failed_images']}")
    print("=============================================\n")
    print("Reports successfully generated in 'output/'.")
    print("Raw OCR text saved in 'raw_ocr/'.")
    print("SQLite database updated at 'invoices.db'.")


if __name__ == "__main__":
    main()
