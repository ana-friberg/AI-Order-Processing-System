import requests
import re
import json
from typing import Optional, Tuple, Dict, Any
from urllib.parse import quote
from datetime import datetime, timedelta

from config.secrets import PRIORITY_MAIN_SCREEN, PRIORITY_MAIN_SUB_SCREEN
from config.constants import (
    PRIORITY_TIMEZONE_OFFSET,
    PRIORITY_DATE_OFFSET_DAYS,
    PRICE_MATCH_TOLERANCE,
    CUSTOMER_PO_LENGTH,
    CUSTOMER_PO_PREFIX,
    SPECIAL_ADDRESS_KEYWORD,
    SPECIAL_ADDRESS_SUFFIX,
    STATUS_SUPPLIER_APPROVED,
    STATUS_SENT_TO_SUPPLIER,
)
from utils.logging_config import get_logger
from utils.price_utils import extract_numeric_price, extract_numeric_quantity

logger = get_logger(__name__)

_ODATA_ORDERS = PRIORITY_MAIN_SCREEN
_ODATA_ORDER_ITEMS = PRIORITY_MAIN_SUB_SCREEN


class PriorityAPIClient:
    """Interact with the Priority ERP OData API."""

    def __init__(self, base_url: str, auth_token: str):
        self.base_url = base_url
        self.headers = {"Authorization": f"Basic {auth_token}"}

    # ── Public API ────────────────────────────────────────────────────────────

    def check_order_exists(self, order_po: str) -> Tuple[bool, str]:
        """Return (True, 'Order exists') or (False, reason) for a given PO."""
        try:
            encoded_po = quote(order_po)
            url = (
                f"{self.base_url}/{_ODATA_ORDERS}?"
                f"$filter=ORDNAME eq '{encoded_po}'&"
                "$select=SUPNAME,SUPORDNUM,CDES,ORDNAME,CURDATE,STATDES,DISPRICE,ED_REQDATE&"
                f"$expand={_ODATA_ORDER_ITEMS}($select=PARTNAME,TQUANT,PRICE,CODE,VATPRICE,REQDATE,KLINE,ORDI)"
            )
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            if not data.get("value"):
                return False, "Order does not exist in Priority"
            return True, "Order exists"
        except requests.exceptions.RequestException as e:
            return False, f"Error checking Priority: {e}"

    def get_order_data(
        self, order_po: str, ai_total_price: str = None
    ) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
        """Fetch order data from Priority and optionally validate the AI-extracted total price.

        Returns (success, order_data, price_validation).
        order_data contains a special '_original_items' key with unfiltered items for shipping
        validation; the main PORDERITEMS_SUBFORM has SH items stripped out.
        """
        if (
            len(order_po) != CUSTOMER_PO_LENGTH
            or not order_po.startswith(CUSTOMER_PO_PREFIX)
            or not order_po[len(CUSTOMER_PO_PREFIX):].isdigit()
        ):
            logger.warning("Invalid PO format: %s (expected %s + 10 digits)", order_po, CUSTOMER_PO_PREFIX)
            return False, {}, {}

        try:
            encoded_po = quote(order_po)
            logger.info("Fetching Priority data for PO: %s", order_po)
            url = (
                f"{self.base_url}/{_ODATA_ORDERS}?"
                f"$filter=ORDNAME eq '{encoded_po}'&"
                "$select=SUPNAME,SUPORDNUM,CDES,ORDNAME,CURDATE,STATDES,DISPRICE,TOTPRICE,ED_REQDATE&"
                f"$expand={_ODATA_ORDER_ITEMS}($select=PARTNAME,TQUANT,PRICE,CODE,VATPRICE,REQDATE,KLINE,ORDI)"
            )
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            if not data.get("value"):
                return False, {}, {}

            priority_totprice = data["value"][0].get("TOTPRICE", 0)
            price_validation = self._validate_total_price(priority_totprice, ai_total_price)

            # Filter SH items out of the main list; keep originals for shipping validation
            order_items = data["value"][0].get(_ODATA_ORDER_ITEMS, [])
            if order_items:
                original_items = order_items.copy()
                filtered_items = [i for i in order_items if not i.get("PARTNAME", "").startswith("SH")]
                sh_items = [i.get("PARTNAME") for i in order_items if i.get("PARTNAME", "").startswith("SH")]
                if sh_items:
                    logger.debug("Filtered out SH items: %s", sh_items)
                logger.info(
                    "Priority items: %d total, %d non-SH sent for validation",
                    len(order_items), len(filtered_items),
                )
                data["value"][0][_ODATA_ORDER_ITEMS] = filtered_items
                data["value"][0]["_original_items"] = original_items

            return True, data, price_validation

        except requests.exceptions.RequestException as e:
            logger.error("Error fetching Priority data for PO %s: %s", order_po, e)
            return False, {}, {}

    def update_order_items(
        self,
        customer_po: str,
        items_data: list,
        delivery_address: str = "",
        extracted_total_price: str = "",
        ai_shipping_info: Dict = None,
        full_items_data: list = None,
    ) -> Tuple[bool, str, list, Dict]:
        """Update REQDATE on matched Priority line items and set the final order status.

        Business logic for REQDATE = delivery_date − 6 days:
        - "12 Bet … St." address → use the Thursday of that week
        - All other addresses   → if it lands on Saturday, use Friday instead
        """
        success, order_data, price_validation = self.get_order_data(customer_po, extracted_total_price)
        if not success or not order_data.get("value"):
            return False, "Could not retrieve order data from Priority", [], {}

        order_items = order_data["value"][0].get(_ODATA_ORDER_ITEMS, [])
        if not order_items:
            return False, "No order items found in Priority", [], price_validation

        original_order_items = order_data["value"][0].get("_original_items", order_items)
        validation_items = full_items_data if full_items_data is not None else items_data

        item_validation = self.validate_items_detail(order_items, validation_items)
        shipping_validation = self.validate_shipping_charges(original_order_items, validation_items, ai_shipping_info)

        price_validation["item_validation"] = item_validation
        price_validation["shipping_validation"] = shipping_validation

        if price_validation.get("validation_attempted"):
            overall = (
                price_validation.get("price_match", False)
                and item_validation.get("all_items_valid", False)
                and shipping_validation.get("shipping_validation_passed", True)
            )
            price_validation["overall_validation_pass"] = overall
            messages = []
            messages.append("Total price matches" if price_validation.get("price_match") else "Total price mismatch")
            messages.append("item details match" if item_validation.get("all_items_valid") else "item detail discrepancies")
            messages.append("shipping validated" if shipping_validation.get("shipping_validation_passed") else "shipping discrepancies")
            price_validation["overall_validation_message"] = ", ".join(messages)
            logger.info("Overall validation: %s", price_validation["overall_validation_message"])

        partname_to_kline = {
            item.get("PARTNAME"): item.get("KLINE")
            for item in order_items
            if item.get("PARTNAME") and item.get("KLINE") is not None
        }
        logger.debug("PARTNAME→KLINE mapping: %s", partname_to_kline)

        matched_items = [
            {
                "product_code": item["product_code"],
                "delivery_date": item["delivery_date"],
                "kline_id": partname_to_kline[item["product_code"]],
            }
            for item in items_data
            if item.get("product_code") and item.get("delivery_date")
            and item["product_code"] in partname_to_kline
        ]

        update_results, success_count = self._patch_line_items(
            customer_po, matched_items, delivery_address
        )

        # Flag items without product_code/delivery_date as skipped
        for item in items_data:
            if not item.get("product_code") or not item.get("delivery_date"):
                update_results.append({
                    "product_code": item.get("product_code", ""),
                    "status": "skipped",
                    "message": "Missing product_code or delivery_date",
                })

        # Flag items not matched in Priority
        processed_codes = {r["product_code"] for r in update_results}
        for item in items_data:
            if item.get("product_code") and item["product_code"] not in processed_codes:
                update_results.append({
                    "product_code": item["product_code"],
                    "status": "not_found",
                    "message": "Product code not found in Priority order",
                })

        logger.info("Updating final order status for PO: %s", customer_po)
        if not self.update_final_status(customer_po, price_validation):
            logger.warning("Failed to update final order status for PO %s", customer_po)

        valid_count = len([i for i in items_data if i.get("product_code") and i.get("delivery_date")])
        if success_count == 0:
            return False, f"No items updated (0/{valid_count})", update_results, price_validation
        if success_count < len(matched_items):
            return True, f"Partial update: {success_count}/{len(matched_items)} items", update_results, price_validation
        return True, f"All {success_count}/{len(matched_items)} items updated", update_results, price_validation

    def calculate_priority_date(self, delivery_date_str: str, delivery_address: str) -> str:
        """Return the Priority REQDATE (DD/MM/YYYY) for a given delivery date and address.

        Subtracts PRIORITY_DATE_OFFSET_DAYS then applies weekend/address adjustments.
        """
        if "." in delivery_date_str:
            parts = delivery_date_str.split(".")
            if len(parts) != 3:
                raise ValueError(f"Invalid date format: {delivery_date_str}")
            original_date = datetime(int(parts[2]), int(parts[1]), int(parts[0]))
        else:
            original_date = datetime.strptime(delivery_date_str, "%d/%m/%Y")

        calculated = original_date - timedelta(days=PRIORITY_DATE_OFFSET_DAYS)

        if SPECIAL_ADDRESS_KEYWORD in delivery_address and SPECIAL_ADDRESS_SUFFIX in delivery_address:
            # Special case: use the Thursday of the calculated week
            days_to_thursday = 3 - calculated.weekday()  # Thursday = weekday 3
            priority_date = calculated + timedelta(days=days_to_thursday)
            logger.debug(
                "Special address logic: %s → -%d days → %s → Thursday %s",
                delivery_date_str, PRIORITY_DATE_OFFSET_DAYS,
                calculated.strftime("%d.%m.%Y (%A)"),
                priority_date.strftime("%d.%m.%Y (%A)"),
            )
        else:
            # Default: if it falls on Saturday, use Friday instead
            if calculated.weekday() == 5:
                priority_date = calculated - timedelta(days=1)
                logger.debug(
                    "Saturday adjustment: %s → %s",
                    calculated.strftime("%d.%m.%Y"), priority_date.strftime("%d.%m.%Y"),
                )
            else:
                priority_date = calculated

        return priority_date.strftime("%d/%m/%Y")

    def convert_date_format(self, date_str: str) -> str:
        """Convert DD/MM/YYYY to Priority ISO-8601 format (YYYY-MM-DDTHH:MM:SS+03:00)."""
        date_obj = datetime.strptime(date_str, "%d/%m/%Y")
        return date_obj.strftime(f"%Y-%m-%dT00:00:00{PRIORITY_TIMEZONE_OFFSET}")

    def update_final_status(self, customer_po: str, price_validation: Dict) -> bool:
        """Set STATDES on the order based on overall validation results."""
        if not price_validation.get("validation_attempted"):
            logger.info("No validation data — skipping final status update for PO %s", customer_po)
            return True

        item_validation = price_validation.get("item_validation", {})
        shipping_validation = price_validation.get("shipping_validation", {})

        price_match = price_validation.get("price_match", False)
        all_items_valid = item_validation.get("all_items_valid", False)
        item_count_match = item_validation.get("item_count_match", False)
        shipping_valid = shipping_validation.get("shipping_validation_passed", True)
        no_missing = len(item_validation.get("mismatches", {}).get("missing_in_ai", [])) == 0

        logger.info(
            "Final status check for PO %s — price=%s items=%s count=%s missing=%s shipping=%s",
            customer_po,
            "✓" if price_match else "✗",
            "✓" if all_items_valid else "✗",
            "✓" if item_count_match else "✗",
            "✓" if no_missing else "✗",
            "✓" if shipping_valid else "✗",
        )

        if price_match and all_items_valid and item_count_match and shipping_valid and no_missing:
            new_status = STATUS_SUPPLIER_APPROVED
            reason = "All validations passed"
        else:
            new_status = STATUS_SENT_TO_SUPPLIER
            issues = []
            if not price_match:        issues.append("total price mismatch")
            if not all_items_valid:    issues.append("item price/quantity discrepancies")
            if not item_count_match:   issues.append("item count mismatch")
            if not no_missing:
                n = len(item_validation.get("mismatches", {}).get("missing_in_ai", []))
                issues.append(f"{n} items missing in AI extraction")
            if not shipping_valid:     issues.append("shipping validation failed")
            reason = f"Validation failed: {', '.join(issues)}"

        logger.info("Setting order status for PO %s → '%s' (%s)", customer_po, new_status, reason)

        try:
            encoded_po = quote(customer_po)
            url = f"{self.base_url}/{_ODATA_ORDERS}(ORDNAME='{encoded_po}')"
            patch_headers = {**self.headers, "Content-Type": "application/json"}
            resp = requests.patch(url, headers=patch_headers, data=json.dumps({"STATDES": new_status}))

            success = resp.status_code in (200, 204)
            price_validation["final_status_update"] = {
                "attempted": True,
                "success": success,
                "final_status": new_status,
                "reason": reason,
                **({"error": f"HTTP {resp.status_code}: {resp.text}"} if not success else {}),
            }
            if not success:
                logger.error("Status update failed for PO %s: HTTP %d — %s", customer_po, resp.status_code, resp.text)
            return success

        except Exception as e:
            logger.error("Error updating final status for PO %s: %s", customer_po, e)
            price_validation["final_status_update"] = {
                "attempted": True, "success": False,
                "error": str(e), "intended_status": new_status, "reason": reason,
            }
            return False

    def validate_items_detail(self, priority_items: list, ai_items: list) -> Dict:
        """Validate quantity and price for each item between Priority and AI extraction."""
        priority_by_partname = {item.get("PARTNAME", ""): item for item in priority_items}
        ai_by_code = {item.get("product_code", ""): item for item in ai_items}

        results = {
            "all_items_valid": True,
            "item_count_match": len(priority_items) == len(ai_items),
            "priority_item_count": len(priority_items),
            "ai_item_count": len(ai_items),
            "item_details": [],
            "mismatches": {"quantity": [], "price": [], "missing_in_ai": [], "missing_in_priority": []},
        }

        for priority_item in priority_items:
            partname = priority_item.get("PARTNAME", "")
            p_qty = priority_item.get("TQUANT", 0)
            p_price = priority_item.get("VATPRICE", 0)
            detail = {
                "partname": partname, "priority_quantity": p_qty, "priority_price": p_price,
                "quantity_match": False, "price_match": False, "validation_passed": False,
            }

            if partname in ai_by_code:
                ai_item = ai_by_code[partname]
                ai_qty_str = ai_item.get("quantity", "")
                ai_total_str = ai_item.get("item_total", "")
                ai_qty = extract_numeric_quantity(ai_qty_str)
                ai_price = extract_numeric_price(ai_total_str)

                qty_match = ai_qty == p_qty
                price_diff = abs(float(p_price) - ai_price)
                price_match = price_diff < PRICE_MATCH_TOLERANCE

                detail.update({
                    "ai_quantity_raw": ai_qty_str, "ai_quantity_numeric": ai_qty,
                    "ai_price_raw": ai_total_str, "ai_price_numeric": ai_price,
                    "quantity_match": qty_match, "price_match": price_match,
                    "price_difference": price_diff,
                    "validation_passed": qty_match and price_match,
                })
                logger.debug(
                    "Item %s — qty: %s vs %s (%s) | price: %s vs %s (diff=%.4f) | %s",
                    partname, p_qty, ai_qty_str, ai_qty,
                    p_price, ai_total_str, price_diff,
                    "OK" if detail["validation_passed"] else "FAIL",
                )

                if not qty_match:
                    results["mismatches"]["quantity"].append({
                        "partname": partname, "priority_quantity": p_qty,
                        "ai_quantity": ai_qty_str, "ai_quantity_numeric": ai_qty,
                    })
                    results["all_items_valid"] = False
                if not price_match:
                    results["mismatches"]["price"].append({
                        "partname": partname, "priority_price": p_price,
                        "ai_price": ai_total_str, "ai_price_numeric": ai_price,
                        "difference": price_diff,
                    })
                    results["all_items_valid"] = False
            else:
                detail.update({
                    "ai_quantity_raw": None, "ai_quantity_numeric": None,
                    "ai_price_raw": None, "ai_price_numeric": None,
                })
                results["mismatches"]["missing_in_ai"].append(partname)
                results["all_items_valid"] = False
                logger.debug("Item %s — missing in AI extraction", partname)

            results["item_details"].append(detail)

        for ai_item in ai_items:
            code = ai_item.get("product_code", "")
            if code not in priority_by_partname:
                results["mismatches"]["missing_in_priority"].append(code)
                results["all_items_valid"] = False

        logger.info(
            "Item validation: all_valid=%s, count_match=%s (%d vs %d), qty_mismatches=%d, "
            "price_mismatches=%d, missing_ai=%d, missing_priority=%d",
            results["all_items_valid"], results["item_count_match"],
            results["priority_item_count"], results["ai_item_count"],
            len(results["mismatches"]["quantity"]), len(results["mismatches"]["price"]),
            len(results["mismatches"]["missing_in_ai"]), len(results["mismatches"]["missing_in_priority"]),
        )
        return results

    def validate_shipping_charges(
        self, priority_items: list, ai_items: list, ai_shipping_info: Dict = None
    ) -> Dict:
        """Compare Priority SH items against AI-extracted shipping charges."""
        priority_shipping = [
            {"partname": i.get("PARTNAME"), "price": i.get("VATPRICE", 0)}
            for i in priority_items
            if i.get("PARTNAME", "").startswith("SH")
        ]
        total_priority_shipping = sum(float(s["price"]) for s in priority_shipping)

        ai_shipping = ai_shipping_info or self._extract_ai_shipping_info(ai_items)
        ai_price = ai_shipping.get("price_numeric", 0) if ai_shipping else 0

        has_priority = bool(priority_shipping)
        has_ai = ai_shipping is not None

        if has_priority:
            logger.debug("Priority shipping items: %s (total=%.2f)", priority_shipping, total_priority_shipping)
        if has_ai:
            logger.debug("AI shipping: %s (price=%.2f)", ai_shipping.get("raw_text", ""), ai_price)

        if has_priority and has_ai:
            diff = abs(total_priority_shipping - ai_price)
            match = diff < PRICE_MATCH_TOLERANCE
            case = "both_exist_compare_prices"
            passed = match
            msg = (
                f"Priority shipping {total_priority_shipping} vs AI {ai_price}: "
                f"{'MATCH' if match else f'MISMATCH (diff={diff:.4f})'}"
            )
        elif has_priority and not has_ai:
            case, passed = "priority_has_ai_missing", False
            msg = f"Priority has shipping (total={total_priority_shipping}) but AI found none"
        elif not has_priority and has_ai:
            case, passed = "ai_has_priority_missing", False
            msg = f"AI found shipping ({ai_price}) but Priority has no SH items"
        else:
            case, passed = "neither_has_shipping", True
            msg = "No shipping charges in either system"

        diff_value = abs(total_priority_shipping - ai_price) if (has_priority and has_ai) else 0
        logger.info("Shipping validation — case=%s passed=%s: %s", case, passed, msg)

        return {
            "shipping_validation_passed": passed,
            "validation_case": case,
            "priority_shipping_items": priority_shipping,
            "priority_shipping_total": total_priority_shipping,
            "ai_shipping_info": ai_shipping,
            "shipping_match": passed and (has_priority == has_ai),
            "price_difference": diff_value,
            "validation_message": msg,
        }

    def update_order_number(
        self, customer_po: str, order_number: str = None, supplier_name: str = None
    ) -> Tuple[bool, str]:
        """Update SUPORDNUM and SUPNAME on the Priority order."""
        try:
            if order_number is None or supplier_name is None:
                ok, order_data, _ = self.get_order_data(customer_po)
                if not ok or not order_data.get("value"):
                    return False, "Could not retrieve order data from Priority"
                info = order_data["value"][0]
                if order_number is None:
                    order_number = info.get("SUPORDNUM", "")
                if supplier_name is None:
                    supplier_name = info.get("SUPNAME", "")

            if not order_number:
                return False, "SUPORDNUM cannot be empty"
            if not supplier_name:
                return False, "SUPNAME cannot be empty"

            encoded_po = quote(customer_po)
            url = f"{self.base_url}/{_ODATA_ORDERS}?$filter=ORDNAME eq '{encoded_po}'"
            patch_headers = {**self.headers, "Content-Type": "application/json"}
            payload = json.dumps({"SUPORDNUM": order_number, "SUPNAME": supplier_name})

            logger.info("Updating order number for PO %s: SUPORDNUM=%s SUPNAME=%s", customer_po, order_number, supplier_name)
            resp = requests.patch(url, headers=patch_headers, data=payload)

            if resp.status_code >= 400:
                logger.error("Order number update failed for PO %s: HTTP %d — %s", customer_po, resp.status_code, resp.text)
                return False, f"Update failed with status {resp.status_code}: {resp.text}"

            if resp.status_code in (200, 204):
                return True, f"Order number updated: SUPORDNUM={order_number}, SUPNAME={supplier_name}"
            return False, f"Unexpected status: {resp.status_code}"

        except requests.exceptions.RequestException as e:
            logger.error("Network error updating order number for PO %s: %s", customer_po, e)
            return False, f"Network error: {e}"
        except Exception as e:
            logger.error("Unexpected error updating order number for PO %s: %s", customer_po, e)
            return False, str(e)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _validate_total_price(self, priority_totprice: float, ai_total_price: str) -> Dict:
        """Build a price_validation dict comparing Priority TOTPRICE against an AI string."""
        result = {
            "priority_totprice": priority_totprice,
            "ai_total_price_raw": ai_total_price,
            "ai_total_price": None,
            "validation_attempted": ai_total_price is not None,
            "price_match": False,
            "price_difference": 0,
            "validation_message": "",
        }
        if ai_total_price is None:
            result["validation_message"] = "No AI total price provided"
            return result

        try:
            ai_numeric = extract_numeric_price(ai_total_price)
            diff = abs(float(priority_totprice) - ai_numeric)
            match = diff < PRICE_MATCH_TOLERANCE
            result.update({
                "ai_total_price": ai_numeric,
                "ai_total_price_numeric": ai_numeric,
                "price_match": match,
                "price_difference": diff,
                "validation_message": (
                    f"{'Match' if match else 'Mismatch'} — "
                    f"Priority={priority_totprice}, AI={ai_numeric}, diff={diff:.4f}"
                ),
            })
            logger.info(
                "Price validation — Priority=%s AI='%s'→%.4f diff=%.4f match=%s",
                priority_totprice, ai_total_price, ai_numeric, diff, "YES" if match else "NO",
            )
        except (ValueError, TypeError) as e:
            result["validation_message"] = f"Error parsing AI price '{ai_total_price}': {e}"
            logger.warning("Price validation error: %s", result["validation_message"])

        return result

    def _patch_line_items(
        self, customer_po: str, matched_items: list, delivery_address: str
    ) -> Tuple[list, int]:
        """PATCH REQDATE for each matched item. Returns (update_results, success_count)."""
        update_results = []
        success_count = 0
        encoded_po = quote(customer_po)
        patch_headers = {**self.headers, "Content-Type": "application/json"}
        is_special_address = SPECIAL_ADDRESS_KEYWORD in delivery_address and SPECIAL_ADDRESS_SUFFIX in delivery_address

        for item in matched_items:
            product_code = item["product_code"]
            delivery_date = item["delivery_date"]
            kline_id = item["kline_id"]
            try:
                priority_date_str = self.calculate_priority_date(delivery_date, delivery_address)
                converted_date = self.convert_date_format(priority_date_str)
                url = (
                    f"{self.base_url}/{_ODATA_ORDERS}(ORDNAME='{encoded_po}')/"
                    f"{_ODATA_ORDER_ITEMS}({kline_id})"
                )
                logger.debug(
                    "PATCH %s → REQDATE=%s (from %s via %s)",
                    product_code, converted_date, delivery_date, priority_date_str,
                )
                resp = requests.patch(url, headers=patch_headers, data=json.dumps({"REQDATE": converted_date}))
                logger.debug("PATCH response for %s: HTTP %d", product_code, resp.status_code)

                if resp.status_code in (200, 204):
                    update_results.append({
                        "product_code": product_code, "kline_id": kline_id,
                        "status": "success", "message": "Line item updated",
                        "original_delivery_date": delivery_date,
                        "calculated_priority_date": priority_date_str,
                        "updated_date": converted_date,
                        "address_logic": "Special address (12 Bet)" if is_special_address else "Default",
                    })
                    success_count += 1
                else:
                    logger.warning(
                        "Update failed for %s (KLINE=%s): HTTP %d — %s",
                        product_code, kline_id, resp.status_code, resp.text,
                    )
                    update_results.append({
                        "product_code": product_code, "kline_id": kline_id,
                        "status": "failed",
                        "message": f"HTTP {resp.status_code}: {resp.text}",
                        "original_delivery_date": delivery_date,
                        "attempted_date": converted_date,
                    })

            except ValueError as e:
                update_results.append({
                    "product_code": product_code, "kline_id": kline_id,
                    "status": "date_error", "message": str(e),
                })
            except requests.exceptions.RequestException as e:
                update_results.append({
                    "product_code": product_code, "kline_id": kline_id,
                    "status": "network_error", "message": f"Network error: {e}",
                })

        return update_results, success_count

    def _extract_ai_shipping_info(self, ai_items: list) -> Optional[Dict]:
        """Scan the AI items list for a shipping line item."""
        keywords = ["shipping & handling", "expedited handling", "shipping", "handling", "freight", "delivery"]
        for item in ai_items:
            text = " ".join(str(item.get(f, "")) for f in ["product_code", "description", "item_description", "name"]).lower()
            for kw in keywords:
                if kw in text:
                    price_raw = item.get("item_total") or item.get("price") or item.get("total_price", "")
                    return {
                        "found_keyword": kw,
                        "raw_text": text,
                        "price_raw": price_raw,
                        "price_numeric": extract_numeric_price(price_raw),
                        "ai_item": item,
                    }
        return None
