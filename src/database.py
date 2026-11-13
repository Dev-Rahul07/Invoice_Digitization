"""SQLite Database and Duplicate Detection Module.

Uses standard Python sqlite3 with simple functions only (Zero ORM, Zero Classes).
Implements duplicate invoice detection using a single `duplicate_key` column:
    duplicate_key = vendor (lowercase) + "_" + invoice_number (uppercase)
    Example: 'abc technologies_INV-101'

Key Functions:
1. generate_duplicate_key(vendor, invoice_number) -> Optional[str]
2. create_database(db_path) -> sqlite3.Connection
3. is_duplicate(conn, duplicate_key) -> bool
4. save_invoice(conn, invoice_data) -> Optional[int]
5. save_invoice_source(conn, invoice_id, filename, filepath, raw_ocr_text, status, review_reason) -> int
6. process_invoice(conn, filename, filepath, raw_ocr_text, extracted_data, validation) -> Dict[str, Any]
7. get_summary_counts(conn) -> Dict[str, int]
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

# Processing Status Constants
STATUS_VALID = "valid"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_DUPLICATE_SCAN = "duplicate_scan"
STATUS_ALREADY_PROCESSED = "already_processed"
STATUS_FAILED = "failed"


# ============================================================================
# 1. GENERATE DUPLICATE KEY
# ============================================================================
def generate_duplicate_key(
    vendor: Optional[str], invoice_number: Optional[str]
) -> Optional[str]:
    """Generate a normalized duplicate key: '<vendor_lowercase>_<invoice_uppercase>'.

    Requirements:
    - Remove extra whitespace using strip().
    - Convert vendor name to lowercase.
    - Convert invoice number to uppercase.
    - If vendor or invoice number is missing, return None (send to needs_review).

    Example:
        Vendor: 'ABC Technologies '
        Invoice Number: 'inv-101'
        Result: 'abc technologies_INV-101'
    """
    if not vendor or not invoice_number:
        return None

    cleaned_vendor = vendor.strip().lower()
    cleaned_invoice = invoice_number.strip().upper()

    if not cleaned_vendor or not cleaned_invoice:
        return None

    return f"{cleaned_vendor}_{cleaned_invoice}"


# ============================================================================
# 2. CREATE DATABASE & TABLES
# ============================================================================
def create_database(db_path: Path | str = "invoices.db") -> sqlite3.Connection:
    """Create SQLite database, tables, and constraints if they do not exist.

    Tables:
    - invoices: Master records for valid invoices with duplicate_key UNIQUE.
    - invoice_sources: Every physical scan linked to an invoice (1:N relationship).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Enable SQLite foreign key constraint enforcement
    conn.execute("PRAGMA foreign_keys = ON;")

    with conn:
        # Table 1: Master invoices with duplicate_key as UNIQUE constraint
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                duplicate_key TEXT NOT NULL UNIQUE,
                invoice_date TEXT NOT NULL,
                currency TEXT NOT NULL,
                subtotal REAL NOT NULL,
                tax REAL NOT NULL,
                total REAL NOT NULL,
                subtotal_minor INTEGER,
                tax_minor INTEGER,
                total_minor INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Table 2: Source image scans (Audit trail preserving every physical file)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invoice_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NULL,
                filename TEXT NOT NULL,
                filepath TEXT,
                raw_ocr_text TEXT,
                processing_status TEXT NOT NULL,
                review_reason TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );
            """
        )

        # Fast O(1) Unique Index on duplicate_key
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_invoices_duplicate_key
            ON invoices(duplicate_key);
            """
        )

    return conn


# ============================================================================
# 3. DUPLICATE CHECKING
# ============================================================================
def is_duplicate(conn: sqlite3.Connection, duplicate_key: Optional[str]) -> bool:
    """Check whether the duplicate_key already exists in the invoices table.

    Returns:
        True if the invoice already exists in SQLite, False otherwise.
    """
    if not duplicate_key:
        return False

    query = "SELECT 1 FROM invoices WHERE duplicate_key = ?"
    cursor = conn.execute(query, (duplicate_key,))
    return cursor.fetchone() is not None


def get_invoice_id_by_key(
    conn: sqlite3.Connection, duplicate_key: str
) -> Optional[int]:
    """Helper to fetch the primary key (id) of an existing invoice by duplicate_key."""
    query = "SELECT id FROM invoices WHERE duplicate_key = ?"
    cursor = conn.execute(query, (duplicate_key,))
    row = cursor.fetchone()
    return row["id"] if row else None


# ============================================================================
# 4. SAVE MASTER INVOICE
# ============================================================================
def save_invoice(
    conn: sqlite3.Connection, invoice_data: Dict[str, Any]
) -> Optional[int]:
    """Insert a new valid invoice into the `invoices` table.

    Requirements:
    - Generate duplicate key.
    - Check if invoice already exists.
    - If duplicate, do not insert another invoice.
    - Use parameterized SQL queries.
    - Handle SQLite UNIQUE constraint errors safely.

    Returns:
        The newly generated invoice primary key (id), or None if duplicate/failed.
    """
    vendor = invoice_data.get("vendor")
    invoice_number = invoice_data.get("invoice_number")

    duplicate_key = generate_duplicate_key(vendor, invoice_number)
    if not duplicate_key:
        return None

    # Check whether the invoice already exists
    if is_duplicate(conn, duplicate_key):
        return None

    # Calculate major units (e.g. 1200.50) from integer minor units (paise/cents)
    sub_minor = invoice_data.get("subtotal_minor")
    tax_minor = invoice_data.get("tax_minor")
    tot_minor = invoice_data.get("total_minor")

    sub_val = invoice_data.get("subtotal") or (sub_minor / 100.0 if sub_minor is not None else 0.0)
    tax_val = invoice_data.get("tax") or (tax_minor / 100.0 if tax_minor is not None else 0.0)
    tot_val = invoice_data.get("total") or (tot_minor / 100.0 if tot_minor is not None else 0.0)

    query = """
        INSERT INTO invoices (
            vendor, invoice_number, duplicate_key, invoice_date, currency,
            subtotal, tax, total, subtotal_minor, tax_minor, total_minor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        vendor,
        invoice_number,
        duplicate_key,
        invoice_data["invoice_date"],
        invoice_data["currency"],
        round(sub_val, 2),
        round(tax_val, 2),
        round(tot_val, 2),
        sub_minor,
        tax_minor,
        tot_minor,
    )

    try:
        cursor = conn.execute(query, params)
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # SQLite UNIQUE constraint protects against duplicate race conditions
        return None


# ============================================================================
# 5. SAVE SOURCE SCAN RECORD
# ============================================================================
def save_invoice_source(
    conn: sqlite3.Connection,
    invoice_id: Optional[int],
    filename: str,
    filepath: str,
    raw_ocr_text: str,
    status: str,
    review_reason: Optional[str] = None,
) -> int:
    """Save an image scan record into `invoice_sources` for complete traceability.

    Returns:
        The newly generated source record id.
    """
    query = """
        INSERT INTO invoice_sources (
            invoice_id, filename, filepath,
            raw_ocr_text, processing_status, review_reason
        ) VALUES (?, ?, ?, ?, ?, ?)
    """
    cursor = conn.execute(
        query,
        (
            invoice_id,
            filename,
            filepath,
            raw_ocr_text,
            status,
            review_reason,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ============================================================================
# 6. PROCESS INVOICE DECISION FUNCTION
# ============================================================================
def process_invoice(
    conn: sqlite3.Connection,
    filename: str,
    filepath: str,
    raw_ocr_text: str,
    extracted_data: Dict[str, Any],
    validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Handle extracted invoice data, decide if it's new, duplicate, or needs review.

    Rules:
    1. Idempotency: If this exact filename was already imported, skip re-insertion.
    2. Failed validation: Save to `invoice_sources` with invoice_id = NULL.
    3. Missing vendor or invoice number: Flag as `needs_review`, do not guess.
    4. Duplicate invoice: Link to existing invoice_id, do not create duplicate invoice row.
    5. New valid invoice: Insert into `invoices`, link in `invoice_sources`.
    """
    # Rule 1: Check if this file was already processed (safe re-run)
    existing_file = conn.execute(
        "SELECT id, invoice_id, review_reason FROM invoice_sources WHERE filename = ?",
        (filename,),
    ).fetchone()
    if existing_file:
        return {
            "status": STATUS_ALREADY_PROCESSED,
            "invoice_id": existing_file["invoice_id"],
            "source_id": existing_file["id"],
            "is_duplicate": False,
            "is_duplicate_invoice": False,
            "is_duplicate_file": True,
            "review_reason": existing_file["review_reason"],
        }

    # Rule 2: Handle failed validation (e.g. corrupt image format, unreadable OCR)
    if validation and not validation.get("is_valid", True):
        status = validation.get("status", STATUS_NEEDS_REVIEW)
        reason = validation.get("review_reason") or "Validation failed"
        source_id = save_invoice_source(
            conn,
            invoice_id=None,
            filename=filename,
            filepath=filepath,
            raw_ocr_text=raw_ocr_text,
            status=status,
            review_reason=reason,
        )
        return {
            "status": status,
            "invoice_id": None,
            "source_id": source_id,
            "is_duplicate": False,
            "is_duplicate_invoice": False,
            "is_duplicate_file": False,
            "review_reason": reason,
        }

    vendor = extracted_data.get("vendor")
    invoice_num = extracted_data.get("invoice_number")

    # Rule 3: Generate duplicate_key; if missing vendor or number -> needs_review
    duplicate_key = generate_duplicate_key(vendor, invoice_num)
    if not duplicate_key:
        reason = "Missing vendor or invoice number"
        source_id = save_invoice_source(
            conn,
            invoice_id=None,
            filename=filename,
            filepath=filepath,
            raw_ocr_text=raw_ocr_text,
            status=STATUS_NEEDS_REVIEW,
            review_reason=reason,
        )
        return {
            "status": STATUS_NEEDS_REVIEW,
            "invoice_id": None,
            "source_id": source_id,
            "is_duplicate": False,
            "is_duplicate_invoice": False,
            "is_duplicate_file": False,
            "review_reason": reason,
        }

    # Rule 4: Check if duplicate_key already exists in SQLite
    if is_duplicate(conn, duplicate_key):
        existing_invoice_id = get_invoice_id_by_key(conn, duplicate_key)
        reason = f"Duplicate scan of existing invoice ID {existing_invoice_id} (key: {duplicate_key})"
        source_id = save_invoice_source(
            conn,
            invoice_id=existing_invoice_id,
            filename=filename,
            filepath=filepath,
            raw_ocr_text=raw_ocr_text,
            status=STATUS_DUPLICATE_SCAN,
            review_reason=reason,
        )
        return {
            "status": STATUS_DUPLICATE_SCAN,
            "invoice_id": existing_invoice_id,
            "source_id": source_id,
            "is_duplicate": True,
            "is_duplicate_invoice": True,
            "is_duplicate_file": False,
            "review_reason": reason,
        }

    # Rule 5: NEW VALID INVOICE: Insert into `invoices` and link in `invoice_sources`
    new_invoice_id = save_invoice(conn, extracted_data)
    source_id = save_invoice_source(
        conn,
        invoice_id=new_invoice_id,
        filename=filename,
        filepath=filepath,
        raw_ocr_text=raw_ocr_text,
        status=STATUS_VALID,
        review_reason=None,
    )
    return {
        "status": STATUS_VALID,
        "invoice_id": new_invoice_id,
        "source_id": source_id,
        "is_duplicate": False,
        "is_duplicate_invoice": False,
        "is_duplicate_file": False,
        "review_reason": None,
    }

def get_summary_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Query dynamic database counts directly from SQLite."""
    def count(query: str, *params: Any) -> int:
        return conn.execute(query, params).fetchone()[0]

    return {
        "total_images_processed": count("SELECT COUNT(*) FROM invoice_sources"),
        "valid_invoices_stored": count("SELECT COUNT(*) FROM invoices"),
        "duplicate_scans": count(
            "SELECT COUNT(*) FROM invoice_sources WHERE processing_status = ?",
            STATUS_DUPLICATE_SCAN,
        ),
        "needs_review": count(
            "SELECT COUNT(*) FROM invoice_sources WHERE processing_status = ?",
            STATUS_NEEDS_REVIEW,
        ),
        "failed_images": count(
            "SELECT COUNT(*) FROM invoice_sources WHERE processing_status = ?",
            STATUS_FAILED,
        ),
    }
