from typing import Dict
from decimal import Decimal
import json

from utils.logging_config import get_logger

logger = get_logger(__name__)


class OrderValidator:
    """Cross-validate order data between Agilent (AI-extracted) and Priority ERP."""

    @staticmethod
    def clean_number(value: str) -> Decimal:
        """Convert a price string (USD, European, or US format) to Decimal."""
        try:
            if isinstance(value, (int, float)):
                return Decimal(str(value))
            if not value:
                return Decimal("0")
            cleaned = value.replace("USD", "").strip()
            if "," in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            if cleaned.endswith("-"):
                cleaned = "-" + cleaned[:-1]
            return Decimal(cleaned)
        except Exception as e:
            logger.warning("Could not parse number '%s': %s", value, e)
            return Decimal("0")

    @staticmethod
    def clean_quantity(value: str) -> int:
        """Extract integer quantity from a string like '1 EA' → 1."""
        try:
            if isinstance(value, (int, float)):
                return int(value)
            number = "".join(c for c in value.split()[0] if c.isdigit())
            return int(number) if number else 0
        except Exception as e:
            logger.warning("Could not parse quantity '%s': %s", value, e)
            return 0

    @classmethod
    def validate_orders(cls, agilent_data: Dict, priority_data: Dict) -> Dict:
        """Compare Agilent order data against Priority data and return a validation report."""
        results = {
            "status": "VALIDATING",
            "mismatches": [],
            "missing_in_priority": [],
            "missing_in_agilent": [],
            "incomplete_items": [],
        }

        for item in agilent_data.get("items", []):
            if item.get("product_code") and not item.get("item_total"):
                results["incomplete_items"].append({
                    "product_code": item["product_code"],
                    "reason": "Missing total price",
                })

        priority_items = {
            item["PARTNAME"]: item
            for item in priority_data["value"][0]["PORDERITEMS_SUBFORM"]
        }
        agilent_items = {
            item["product_code"]: item
            for item in agilent_data.get("items", [])
            if "product_code" in item
        }

        for product_code, agilent_item in agilent_items.items():
            if product_code in priority_items:
                priority_item = priority_items[product_code]
                try:
                    agilent_qty = cls.clean_quantity(agilent_item["quantity"])
                    priority_qty = int(priority_item["TQUANT"])
                    if "item_total" in agilent_item:
                        agilent_total = cls.clean_number(agilent_item["item_total"])
                        priority_total = Decimal(str(priority_item["VATPRICE"]))
                        if (agilent_qty != priority_qty
                                or abs(agilent_total - priority_total) > Decimal("0.01")):
                            results["mismatches"].append({
                                "product_code": product_code,
                                "quantity": {"agilent": agilent_qty, "priority": priority_qty},
                                "total": {"agilent": str(agilent_total), "priority": str(priority_total)},
                            })
                except (ValueError, KeyError) as e:
                    logger.error("Error comparing item %s: %s", product_code, e)
            else:
                results["missing_in_priority"].append(product_code)

        for partname in priority_items:
            if partname not in agilent_items:
                results["missing_in_agilent"].append(partname)

        if results["incomplete_items"]:
            results["status"] = "INCOMPLETE_DATA"
        elif not any([results["mismatches"], results["missing_in_priority"], results["missing_in_agilent"]]):
            results["status"] = "SAME"
        else:
            results["status"] = "MISMATCH"

        return results


def validate_order(agilent_json_path: str, priority_response: Dict) -> Dict:
    """Load Agilent JSON from a file path and validate against a Priority response dict."""
    try:
        with open(agilent_json_path, "r") as f:
            agilent_data = json.load(f)
        return OrderValidator.validate_orders(agilent_data, priority_response)
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}
