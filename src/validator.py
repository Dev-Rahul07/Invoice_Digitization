"""Simple Invoice Validation Module.

Validates extracted invoice fields using simple functions and dictionaries.
No classes, no dataclasses, no properties - just pure functions.
"""

from typing import Any, Dict, List, Optional

# Status constants
STATUS_VALID = "valid"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_FAILED = "failed"


def validate_invoice(
    data: Dict[str, Any],
    tolerance_minor: int = 0,
) -> Dict[str, Any]:
    """Validate extracted invoice data and return a simple dictionary."""
    reasons: List[str] = []

    # 1. Check required text fields
    if not data.get("invoice_number"):
        reasons.append("Missing or empty invoice number")

    if not data.get("vendor"):
        reasons.append("Missing or empty vendor name")

    if not data.get("invoice_date"):
        reasons.append("Missing or invalid invoice date (expected YYYY-MM-DD)")

    currency = data.get("currency")
    if not currency or len(str(currency).strip()) != 3:
        reasons.append(f"Missing or unrecognized currency code: '{currency}'")

    # 2. Check monetary fields (must exist and be >= 0)
    sub = data.get("subtotal_minor")
    tax = data.get("tax_minor")
    tot = data.get("total_minor")

    if sub is None or sub < 0:
        reasons.append("Missing or invalid subtotal amount")
    if tax is None or tax < 0:
        reasons.append("Missing or invalid tax amount")
    if tot is None or tot < 0:
        reasons.append("Missing or invalid total amount")

    # 3. Check math reconciliation: Subtotal + Tax == Total
    if sub is not None and tax is not None and tot is not None:
        expected_total = sub + tax
        diff = abs(expected_total - tot)
        if diff > tolerance_minor:
            reasons.append(
                f"Math mismatch: Subtotal ({sub/100:.2f}) + Tax ({tax/100:.2f}) = {expected_total/100:.2f}, "
                f"but Total is {tot/100:.2f} (discrepancy of {diff} minor units)"
            )

    is_valid = len(reasons) == 0
    status = STATUS_VALID if is_valid else STATUS_NEEDS_REVIEW
    review_reason = "; ".join(reasons) if reasons else None

    return {
        "is_valid": is_valid,
        "status": status,
        "reasons": reasons,
        "review_reason": review_reason,
    }
