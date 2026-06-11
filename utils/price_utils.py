"""Shared helpers for parsing price and quantity strings.

Both Priority API responses and AI-extracted text use European number formatting
(period as thousands separator, comma as decimal), but US format also appears.
These utilities handle both unambiguously.
"""
import re


def extract_numeric_price(price_str: str) -> float:
    """Parse a price string in European or US format and return a float.

    Examples
    --------
    "USD 383.04"   -> 383.04
    "USD 7.157,16" -> 7157.16  (European: period=thousands, comma=decimal)
    "USD 1,234.56" -> 1234.56  (US: comma=thousands, period=decimal)
    "USD 129,00"   -> 129.0    (European decimal comma, no thousands)
    """
    if not price_str:
        return 0.0

    cleaned = re.sub(r"[^\d\.,]", "", str(price_str))
    if not cleaned:
        return 0.0

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # European: "7.157,16" — period=thousands, comma=decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US: "1,234.56" — comma=thousands, period=decimal
            cleaned = cleaned.replace(",", "")

    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # European decimal comma: "129,00"
            cleaned = cleaned.replace(",", ".")
        elif len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            # Thousands comma: "1,234"
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", "")

    elif "." in cleaned:
        parts = cleaned.split(".")
        if not (len(parts) == 2 and len(parts[1]) <= 2):
            # Thousands period: "1.234"
            cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def extract_numeric_quantity(quantity_str: str) -> int:
    """Return the first integer found in a quantity string, e.g. '1 EA' → 1."""
    numbers = re.findall(r"\d+", str(quantity_str))
    return int(numbers[0]) if numbers else 0
