import re

def assert_claim_number_format(summary: str) -> bool:
    """Validate claim number format: CLM-YYYY-NNNNN"""
    return bool(re.search(r"CLM-\d{4}-\d{5}", summary))

def assert_date_of_loss_present(summary: str) -> bool:
    """Validate date of loss is present and parseable (basic format YYYY-MM-DD or Month DD, YYYY)."""
    # Just a simple check for our generated summaries
    has_iso = bool(re.search(r"\d{4}-\d{2}-\d{2}", summary))
    has_text = bool(re.search(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4}", summary))
    return has_iso or has_text

def assert_excess_deductible_numeric(summary: str) -> bool:
    """Validate deductible/excess amount is numeric."""
    return bool(re.search(r"\$\d+(?:,\d{3})*(?:\.\d{2})?", summary))

def assert_exclusion_clause_cited(summary: str) -> bool:
    """If a denial or exclusion is stated, an exclusion clause ID must be cited."""
    is_denied = bool(re.search(r"(?i)\b(denied|exclusion|excluded|not covered)\b", summary))
    if not is_denied:
        return True # Not a denial, assertion trivially holds
    # Look for HO-0304, E-17, Exclusion 3, etc.
    return bool(re.search(r"(?i)(HO-\d{4}|E-\d{2}|Exclusion \d+[a-z]?)", summary))
