import requests
import re
import json
from typing import Optional, Tuple, Dict, Any
from urllib.parse import quote
from datetime import datetime

class PriorityAPIClient:
    """Handle Priority API requests"""
    
    def __init__(self, base_url: str, auth_token: str):
        self.base_url = base_url
        self.headers = {
            'Authorization': f'Basic {auth_token}'
        }

    def check_order_exists(self, order_po: str) -> Tuple[bool, str]:
        """
        Check if order exists in Priority
        Returns: (exists: bool, message: str)
        """
        try:
            # URL encode the PO number
            encoded_po = quote(order_po)
            
            # Construct the URL
            url = (f"{self.base_url}/odata/Priority/tabula.ini/eld0999/PORDERS?"
                  f"$filter=ORDNAME eq '{encoded_po}'&"
                  "$select=SUPNAME,SUPORDNUM,CDES,ORDNAME,CURDATE,STATDES,DISPRICE,ED_REQDATE&"
                  "$expand=PORDERITEMS_SUBFORM($select=PARTNAME,TQUANT,PRICE,CODE,VATPRICE,REQDATE,KLINE,ORDI)")

            # Make the request
            response = requests.get(url, headers=self.headers)
            
            # Check response
            response.raise_for_status()
            data = response.json()

            # Check if data exists
            if not data.get('value'):
                return False, "Order does not exist in Priority"
            return True, "Order exists"

        except requests.exceptions.RequestException as e:
            return False, f"Error checking Priority: {str(e)}"

    def get_order_data(self, order_po: str, ai_total_price: str = None) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
        """
        Get order data from Priority and validate against AI-extracted total price
        Args:
            order_po: The PO number
            ai_total_price: AI extracted total price for validation
        Returns:
            Tuple of (success, order_data_with_filtered_items, price_validation)
            Note: order_data includes a special '_original_items' key with unfiltered items for shipping validation
        """
        try:
            # Validate PO format before making API call
            if len(order_po) != 12 or not order_po.startswith('PO') or not order_po[2:].isdigit():
                print(f"Invalid PO format: {order_po}. Expected format: PO + 10 digits")
                return False, {}, {}
            
            encoded_po = quote(order_po)
            print(f"Fetching order data for PO: {encoded_po} (length: {len(order_po)})")
            
            url = (f"{self.base_url}/odata/Priority/tabula.ini/eld0999/PORDERS?"
                  f"$filter=ORDNAME eq '{encoded_po}'&"
                  "$select=SUPNAME,SUPORDNUM,CDES,ORDNAME,CURDATE,STATDES,DISPRICE,TOTPRICE,ED_REQDATE&"
                  "$expand=PORDERITEMS_SUBFORM($select=PARTNAME,TQUANT,PRICE,CODE,VATPRICE,REQDATE,KLINE,ORDI)")

            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            if not data.get('value'):
                return False, {}, {}
            
            # Get Priority TOTPRICE for validation
            order_info = data['value'][0]
            priority_totprice = order_info.get('TOTPRICE', 0)
            
            # Initialize price validation result
            price_validation = {
                "priority_totprice": priority_totprice,
                "ai_total_price_raw": ai_total_price,  # Store original string
                "ai_total_price": None,  # Will store numeric value for consistency
                "validation_attempted": ai_total_price is not None,
                "price_match": False,
                "price_difference": 0,
                "validation_message": ""
            }
            
            # Perform price validation if AI total price is provided
            if ai_total_price is not None:
                try:
                    # Extract numeric value from AI total price string with European formatting
                    # Handle formats like "USD 7.157,16" -> 7157.16
                    ai_price_numeric = self._extract_numeric_price(ai_total_price)
                    
                    # Calculate price difference
                    price_difference = abs(float(priority_totprice) - ai_price_numeric)
                    
                    # Consider prices matching if difference is less than 1 cent
                    price_match = price_difference < 0.01
                    
                    price_validation.update({
                        "ai_total_price": ai_price_numeric,  # Store numeric value in main field
                        "ai_total_price_numeric": ai_price_numeric,  # Keep for backward compatibility
                        "price_match": price_match,
                        "price_difference": price_difference,
                        "validation_message": f"Prices {'match' if price_match else 'do not match'} - Priority: {priority_totprice}, AI: {ai_price_numeric}, Difference: {price_difference}"
                    })
                    
                    print("=" * 60)
                    print("PRICE VALIDATION RESULT:")
                    print(f"  Priority TOTPRICE: {priority_totprice}")
                    print(f"  AI Total Price (raw): '{ai_total_price}'")
                    print(f"  AI Total Price (numeric): {ai_price_numeric}")
                    print(f"  Price Difference: {price_difference}")
                    print(f"  PRICES MATCH: {'✓ YES' if price_match else '✗ NO'}")
                    print("=" * 60)
                    
                    print(f"Price validation result: {price_validation['validation_message']}")
                    
                except (ValueError, TypeError) as e:
                    price_validation.update({
                        "validation_message": f"Error parsing AI total price '{ai_total_price}': {str(e)}",
                        "ai_total_price": None,  # Set numeric field to None on error
                        "ai_total_price_numeric": None
                    })
                    print(f"Price validation error: {price_validation['validation_message']}")
            else:
                price_validation["validation_message"] = "No AI total price provided for validation"
                print("Price validation skipped - no AI total price provided")
            
            # Filter out items with PARTNAMEs that start with "SH" before returning
            if data.get('value') and len(data['value']) > 0:
                order_items = data['value'][0].get('PORDERITEMS_SUBFORM', [])
                if order_items:
                    # Store original items for shipping validation
                    original_items = order_items.copy()
                    
                    # Filter out shipping items (PARTNAME starts with "SH")
                    filtered_items = [item for item in order_items if not item.get('PARTNAME', '').startswith('SH')]
                    
                    # Log the filtering operation
                    all_partnames = [item.get('PARTNAME', '') for item in order_items]
                    filtered_partnames = [item.get('PARTNAME', '') for item in filtered_items]
                    sh_partnames = [item.get('PARTNAME', '') for item in order_items if item.get('PARTNAME', '').startswith('SH')]
                    
                    print(f"Found {len(order_items)} PARTNAMEs in Priority: {all_partnames}")
                    if sh_partnames:
                        print(f"Filtered out SH items: {sh_partnames}")
                    print(f"Sending {len(filtered_items)} items to validation: {filtered_partnames}")
                    
                    # Update the data with filtered items
                    data['value'][0]['PORDERITEMS_SUBFORM'] = filtered_items
                    
                    # Store original items for shipping validation
                    data['value'][0]['_original_items'] = original_items
            
            return True, data, price_validation

        except requests.exceptions.RequestException as e:
            print(f"Error fetching Priority data: {str(e)}")
            return False, {}, {}

    def convert_date_format(self, date_str: str) -> str:
        """
        Convert date from DD/MM/YYYY format to Priority format (YYYY-MM-DDTHH:MM:SS+03:00)
        """
        try:
            # Parse the date from DD/MM/YYYY format
            date_obj = datetime.strptime(date_str, "%d/%m/%Y")
            # Convert to Priority format with timezone
            priority_format = date_obj.strftime("%Y-%m-%dT00:00:00+03:00")
            print(f"Converted date from {date_str} to {priority_format}")
            return priority_format
        except ValueError as e:
            print(f"Error converting date {date_str}: {str(e)}")
            raise ValueError(f"Invalid date format. Expected DD/MM/YYYY, got: {date_str}")

    def update_order_items(self, customer_po: str, items_data: list, delivery_address: str = "", extracted_total_price: str = "", ai_shipping_info: Dict = None, full_items_data: list = None) -> Tuple[bool, str, list, Dict]:
        """
        Update Priority order line items with new delivery dates based on address type
        Args:
            customer_po: The PO number (e.g., 'PO2410000285')
            items_data: List of items with product_code and delivery_date (simplified for updates)
            delivery_address: The delivery address to determine date calculation logic
            extracted_total_price: The total price extracted by AI for validation
            ai_shipping_info: Optional shipping information from AI extraction (e.g., from order_info level)
            full_items_data: Full items data with all fields for validation (quantity, price, etc.)
        Returns: (success: bool, message: str, update_results: list, price_validation: dict)
        
        Business Logic:
        - If address contains "12 Bet" AND "St.": subtract 6 days, then use Thursday of that week
        - Everything else: subtract 6 days, but if it falls on Saturday, use Friday instead
        """
        try:
            # First, get the current order data to map product codes to line indices and validate price
            success, order_data, price_validation = self.get_order_data(customer_po, extracted_total_price)
            if not success or not order_data.get('value'):
                return False, "Could not retrieve order data from Priority", [], {}
            
            # Extract the PORDERITEMS_SUBFORM
            order_items = order_data['value'][0].get('PORDERITEMS_SUBFORM', [])
            if not order_items:
                return False, "No order items found in Priority", [], price_validation
            
            # Get original items for shipping validation (includes SH items)
            original_order_items = order_data['value'][0].get('_original_items', order_items)
            
            # Perform detailed item validation (price and quantity)
            # Use full_items_data if provided, otherwise fall back to items_data
            validation_items = full_items_data if full_items_data is not None else items_data
            item_validation_results = self.validate_items_detail(order_items, validation_items)
            
            # Perform shipping validation - IMPORTANT: Use original unfiltered items that include SH items
            shipping_validation_results = self.validate_shipping_charges(original_order_items, validation_items, ai_shipping_info)
            
            # Add item validation results to price validation
            price_validation["item_validation"] = item_validation_results
            price_validation["shipping_validation"] = shipping_validation_results
            
            # Update overall validation status based on item validation
            all_items_valid = item_validation_results.get("all_items_valid", False)
            shipping_valid = shipping_validation_results.get("shipping_validation_passed", True)  # Default true if no shipping involved
            if price_validation.get("validation_attempted", False):
                # All validations must pass: total price, item validation, and shipping validation
                overall_validation_pass = price_validation.get("price_match", False) and all_items_valid and shipping_valid
                price_validation["overall_validation_pass"] = overall_validation_pass
                
                validation_messages = []
                if price_validation.get("price_match", False):
                    validation_messages.append("Total price matches")
                else:
                    validation_messages.append("Total price has discrepancy")
                    
                if all_items_valid:
                    validation_messages.append("all item details (price & quantity) match")
                else:
                    validation_messages.append("item details have discrepancies")
                    
                if shipping_valid:
                    validation_messages.append("shipping charges validated successfully")
                else:
                    validation_messages.append("shipping charges have discrepancies")
                    
                validation_message = f"{', '.join(validation_messages)}"
                price_validation["overall_validation_message"] = validation_message
                print(f"Overall validation result: {validation_message}")
            
            print(f"Retrieved Priority order items ({len(order_items)} items):")
            for i, item in enumerate(order_items):
                kline = item.get('KLINE', 'N/A')
                ordi = item.get('ORDI', 'N/A')
                print(f"  Array Index {i}: {item.get('PARTNAME', '')}, KLINE: {kline}, ORDI: {ordi}")
            
            # Create a mapping of PARTNAME to KLINE value from Priority order
            partname_to_kline = {}
            for item in order_items:
                partname = item.get('PARTNAME', '')
                kline = item.get('KLINE')
                if partname and kline is not None:
                    partname_to_kline[partname] = kline
        
        except Exception as e:
            print(f"Unexpected error updating Priority order items: {str(e)}")
            return False, f"Unexpected error: {str(e)}", [], {}
    
        print(f"Priority PARTNAME to KLINE mapping: {partname_to_kline}")
    
        # Create list of items that exist in both Priority and JSON data
        matched_items = []
        for item in items_data:
            product_code = item.get('product_code', '')
            delivery_date = item.get('delivery_date', '')
            
            # Skip items without required data
            if not product_code or not delivery_date:
                continue
            
            # Check if product exists in Priority order
            if product_code in partname_to_kline:
                matched_items.append({
                    'product_code': product_code,
                    'delivery_date': delivery_date,
                    'kline_id': partname_to_kline[product_code]
                })
    
        print(f"Matched items for update ({len(matched_items)} items):")
        for item in matched_items:
            print(f"  Product: {item['product_code']}, KLINE ID: {item['kline_id']}, Date: {item['delivery_date']}")
    
        update_results = []
        success_count = 0
        
        # Process each matched item
        for item in matched_items:
            product_code = item['product_code']
            delivery_date = item['delivery_date']
            kline_id = item['kline_id']  # Use KLINE value as identifier
            
            try:
                # Calculate Priority date based on address type and delivery date
                priority_date_str = self.calculate_priority_date(delivery_date, delivery_address)
                
                # Convert the calculated date to Priority format
                converted_date = self.convert_date_format(priority_date_str)
                
                # Prepare update data
                update_data = {
                    "REQDATE": converted_date
                }
                
                # Debug: Print the exact JSON being sent
                print(f"Update payload: {json.dumps(update_data, indent=2)}")
                
                # URL encode the PO number
                encoded_po = quote(customer_po)
                
                # Construct the URL for the specific line item using KLINE value
                url = f"{self.base_url}/odata/Priority/tabula.ini/eld0999/PORDERS(ORDNAME='{encoded_po}')/PORDERITEMS_SUBFORM({kline_id})"
                
                # Prepare headers for PATCH request
                patch_headers = {
                    'Content-Type': 'application/json',
                    'Authorization': self.headers['Authorization']
                }
                
                print(f"Updating Priority KLINE {kline_id} for product {product_code}")
                print(f"Delivery address: {delivery_address}")
                print(f"Original delivery date: {delivery_date}")
                print(f"Calculated Priority date: {priority_date_str}")
                print(f"Final Priority format: {converted_date}")
                print(f"URL: {url}")
                print(f"Headers: {patch_headers}")
                
                # Make PATCH request
                response = requests.patch(url, headers=patch_headers, data=json.dumps(update_data))
                
                print(f"Priority update response for {product_code}: {response.status_code}")
                    
                # Also try to get more detailed error info
                if response.status_code >= 400:
                    print(f"Error details for {product_code}:")
                    print(f"  Status: {response.status_code}")
                    print(f"  Reason: {response.reason}")
                    try:
                        error_json = response.json()
                        print(f"  Error JSON: {json.dumps(error_json, indent=2)}")
                    except:
                        print(f"  Raw response: {response.text}")
                
                if response.status_code in [200, 204]:
                    update_results.append({
                        'product_code': product_code,
                        'kline_id': kline_id,
                        'status': 'success',
                        'message': 'Line item updated successfully',
                        'original_delivery_date': delivery_date,
                        'calculated_priority_date': priority_date_str,
                        'updated_date': converted_date,
                        'address_logic': 'Special address (12 Bet)' if '12 Bet' in delivery_address else 'Default logic'
                    })
                    success_count += 1
                else:
                    update_results.append({
                        'product_code': product_code,
                        'kline_id': kline_id,
                        'status': 'failed',
                        'message': f"Update failed with status {response.status_code}: {response.text}",
                        'original_delivery_date': delivery_date,
                        'calculated_priority_date': priority_date_str,
                        'attempted_date': converted_date,
                        'address_logic': 'Special address (12 Bet)' if '12 Bet' in delivery_address else 'Default logic'
                    })
                    
            except ValueError as date_error:
                update_results.append({
                    'product_code': product_code,
                    'kline_id': kline_id,
                    'status': 'date_error',
                    'message': str(date_error)
                })
            except requests.exceptions.RequestException as req_error:
                update_results.append({
                    'product_code': product_code,
                    'kline_id': kline_id,
                    'status': 'network_error',
                    'message': f"Network error: {str(req_error)}"
                })
    
        # Add results for items that weren't found in Priority
        for item in items_data:
            product_code = item.get('product_code', '')
            delivery_date = item.get('delivery_date', '')
            
            if not product_code or not delivery_date:
                update_results.append({
                    'product_code': product_code,
                    'status': 'skipped',
                    'message': 'Missing product_code or delivery_date'
                })
                continue
            
            # Check if this item was already processed
            if not any(result['product_code'] == product_code for result in update_results):
                update_results.append({
                    'product_code': product_code,
                    'status': 'not_found',
                    'message': 'Product code not found in Priority order'
                })
    
        # Determine overall success
        valid_items_count = len([item for item in items_data if item.get('product_code') and item.get('delivery_date')])
        
        # Update final status based on validation results (second and final status update)
        print("=" * 80)
        print("UPDATING FINAL ORDER STATUS BASED ON VALIDATION RESULTS")
        print("=" * 80)
        final_status_updated = self.update_final_status(customer_po, price_validation)
        if not final_status_updated:
            print("Warning: Failed to update final order status, but continuing with response")
        
        if success_count == 0:
            return False, f"No items were updated successfully (0/{valid_items_count})", update_results, price_validation
        elif success_count < len(matched_items):
            return True, f"Partially successful: {success_count}/{len(matched_items)} matched items updated", update_results, price_validation
        else:
            return True, f"All {success_count}/{len(matched_items)} matched items updated successfully", update_results, price_validation
    
    def calculate_priority_date(self, delivery_date_str: str, delivery_address: str) -> str:
        """
        Calculate Priority date based on delivery address type and original delivery date
        
        Args:
            delivery_date_str: Original delivery date in DD.MM.YYYY format
            delivery_address: Delivery address to determine calculation logic
            
        Returns:
            Calculated date in DD/MM/YYYY format
            
        Business Logic:
        - If address contains "12 Bet" AND "St.": subtract 6 days, then use Thursday of that week
        - Everything else: subtract 6 days, but if it falls on Saturday, use Friday instead
        """
        try:
            from datetime import datetime, timedelta
            
            # Parse the original delivery date
            if '.' in delivery_date_str:
                # Convert DD.MM.YYYY to datetime
                date_parts = delivery_date_str.split('.')
                if len(date_parts) == 3:
                    original_date = datetime(int(date_parts[2]), int(date_parts[1]), int(date_parts[0]))
                else:
                    raise ValueError(f"Invalid date format: {delivery_date_str}")
            else:
                # Assume DD/MM/YYYY format
                original_date = datetime.strptime(delivery_date_str, "%d/%m/%Y")
            
            # Subtract 6 days from the original date
            calculated_date = original_date - timedelta(days=6)
            
            # Check for special address case: "12 Bet" AND "St." (abbreviated form only)
            if "12 Bet" in delivery_address and "St." in delivery_address:
                # Special case: Find Thursday of the week containing calculated_date
                # Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
                days_since_monday = calculated_date.weekday()  # 0=Monday, 6=Sunday
                days_to_thursday = 3 - days_since_monday  # Thursday is day 3 (0-indexed)
                thursday_date = calculated_date + timedelta(days=days_to_thursday)
                priority_date = thursday_date
                
                print(f"Special address (12 Bet + St.) logic:")
                print(f"  Original date: {original_date.strftime('%d.%m.%Y')} ({original_date.strftime('%A')})")
                print(f"  Minus 6 days: {calculated_date.strftime('%d.%m.%Y')} ({calculated_date.strftime('%A')})")
                print(f"  Thursday of that week: {thursday_date.strftime('%d.%m.%Y')} ({thursday_date.strftime('%A')})")
                
            else:
                # Default logic: subtract 6 days, but if it's Saturday, use Friday
                if calculated_date.weekday() == 5:  # Saturday = 5
                    # Move to Friday (subtract 1 day)
                    priority_date = calculated_date - timedelta(days=1)
                    
                    print(f"Default logic (Saturday adjustment):")
                    print(f"  Original date: {original_date.strftime('%d.%m.%Y')} ({original_date.strftime('%A')})")
                    print(f"  Minus 6 days: {calculated_date.strftime('%d.%m.%Y')} ({calculated_date.strftime('%A')})")
                    print(f"  Saturday adjusted to Friday: {priority_date.strftime('%d.%m.%Y')} ({priority_date.strftime('%A')})")
                else:
                    # Use the calculated date as-is
                    priority_date = calculated_date
                    
                    print(f"Default logic (no adjustment needed):")
                    print(f"  Original date: {original_date.strftime('%d.%m.%Y')} ({original_date.strftime('%A')})")
                    print(f"  Minus 6 days: {calculated_date.strftime('%d.%m.%Y')} ({calculated_date.strftime('%A')})")
            
            # Return in DD/MM/YYYY format for further processing
            return priority_date.strftime("%d/%m/%Y")
            
        except Exception as e:
            print(f"Error calculating priority date: {str(e)}")
            # Fallback to original date if calculation fails
            return delivery_date_str.replace('.', '/') if '.' in delivery_date_str else delivery_date_str

    def update_final_status(self, customer_po: str, price_validation: Dict) -> bool:
        """
        Update order status to final status based on complete validation results
        This should be called only once after all validations are complete
        """
        try:
            encoded_po = quote(customer_po)
            
            # Determine final status based on overall validation - STRICT VALIDATION
            # Only approve (אישור ספק) if ALL validations pass completely
            if price_validation.get("validation_attempted", False):
                # Get validation results
                overall_pass = price_validation.get("overall_validation_pass", False)
                item_validation = price_validation.get("item_validation", {})
                shipping_validation = price_validation.get("shipping_validation", {})
                
                # Check ALL validation criteria
                price_match = price_validation.get("price_match", False)
                all_items_valid = item_validation.get("all_items_valid", False)
                item_count_match = item_validation.get("item_count_match", False)
                shipping_valid = shipping_validation.get("shipping_validation_passed", True)
                no_missing_items = len(item_validation.get("mismatches", {}).get("missing_in_ai", [])) == 0
                
                print("=" * 80)
                print("FINAL STATUS DETERMINATION - STRICT VALIDATION:")
                print(f"  ✓ Price match: {'YES' if price_match else 'NO'}")
                print(f"  ✓ All items valid (price & quantity): {'YES' if all_items_valid else 'NO'}")
                print(f"  ✓ Item count match: {'YES' if item_count_match else 'NO'}")
                print(f"  ✓ No missing items in AI: {'YES' if no_missing_items else 'NO'}")
                print(f"  ✓ Shipping validation: {'YES' if shipping_valid else 'NO'}")
                print("=" * 80)
                
                # Only approve if EVERYTHING matches perfectly
                if (price_match and all_items_valid and item_count_match and 
                    shipping_valid and no_missing_items):
                    new_status = "אישור ספק"  # Supplier Approval - EVERYTHING matches
                    status_reason = "Perfect match: Total price, all item details (price & quantity), item count, and shipping all validated successfully"
                    print(f"✓ APPROVED: All validations passed - setting status to 'אישור ספק'")
                else:
                    new_status = "נשלח לספק"  # Sent to Supplier - some discrepancies found
                    
                    # Build detailed reason for rejection
                    issues = []
                    if not price_match:
                        issues.append("total price mismatch")
                    if not all_items_valid:
                        issues.append("item price/quantity discrepancies")
                    if not item_count_match:
                        issues.append("item count mismatch")
                    if not no_missing_items:
                        missing_count = len(item_validation.get("mismatches", {}).get("missing_in_ai", []))
                        issues.append(f"{missing_count} items missing in AI extraction")
                    if not shipping_valid:
                        issues.append("shipping validation failed")
                    
                    status_reason = f"Validation failed: {', '.join(issues)}"
                    print(f"✗ REJECTED: {status_reason} - setting status to 'נשלח לספק'")
            else:
                # No validation attempted - keep as draft
                print("No validation data available - keeping current status")
                return True
            
            print(f"Final status update based on validation: {new_status} ({status_reason})")
            
            status_update_url = f"{self.base_url}/odata/Priority/tabula.ini/eld0999/PORDERS(ORDNAME='{encoded_po}')"
            status_payload = json.dumps({
                "STATDES": new_status
            })
            status_headers = {
                'Content-Type': 'application/json',
                'Authorization': self.headers['Authorization']
            }
            
            print(f"Updating order status to '{new_status}' for PO: {customer_po}")
            status_response = requests.patch(status_update_url, headers=status_headers, data=status_payload)
            
            if status_response.status_code in [200, 204]:
                print(f"Successfully updated final order status to '{new_status}'")
                # Update the price validation with final status info
                price_validation["final_status_update"] = {
                    "attempted": True,
                    "success": True,
                    "final_status": new_status,
                    "reason": status_reason
                }
                return True
            else:
                print(f"Failed to update final order status: {status_response.status_code} - {status_response.text}")
                price_validation["final_status_update"] = {
                    "attempted": True,
                    "success": False,
                    "error": f"Status {status_response.status_code}: {status_response.text}",
                    "intended_status": new_status,
                    "reason": status_reason
                }
                return False
                
        except requests.exceptions.RequestException as status_error:
            print(f"Error updating final order status: {str(status_error)}")
            price_validation["final_status_update"] = {
                "attempted": True,
                "success": False,
                "error": str(status_error),
                "intended_status": new_status if 'new_status' in locals() else "unknown",
                "reason": status_reason if 'status_reason' in locals() else "Final validation based update"
            }
            return False
        except Exception as e:
            print(f"Unexpected error updating final order status: {str(e)}")
            return False

    def validate_items_detail(self, priority_items: list, ai_items: list) -> Dict:
        """
        Validate each item's price and quantity between Priority and AI extraction
        Args:
            priority_items: List of items from Priority PORDERITEMS_SUBFORM
            ai_items: List of items from AI extraction
        Returns:
            Dict with validation results for each item
        """
        validation_results = {
            "all_items_valid": True,
            "item_count_match": len(priority_items) == len(ai_items),
            "priority_item_count": len(priority_items),
            "ai_item_count": len(ai_items),
            "item_details": [],
            "mismatches": {
                "quantity": [],
                "price": [],
                "missing_in_ai": [],
                "missing_in_priority": []
            }
        }
        
        print("=" * 80)
        print("DETAILED ITEM VALIDATION (PRICE & QUANTITY):")
        print("=" * 80)
        
        # Create mappings for easy lookup
        priority_by_partname = {item.get('PARTNAME', ''): item for item in priority_items}
        ai_by_product_code = {item.get('product_code', ''): item for item in ai_items}
        
        # Validate each Priority item against AI data
        for priority_item in priority_items:
            partname = priority_item.get('PARTNAME', '')
            priority_tquant = priority_item.get('TQUANT', 0)
            priority_vatprice = priority_item.get('VATPRICE', 0)  # Total price including VAT
            
            item_result = {
                "partname": partname,
                "priority_quantity": priority_tquant,
                "priority_price": priority_vatprice,
                "quantity_match": False,
                "price_match": False,
                "validation_passed": False
            }
            
            if partname in ai_by_product_code:
                ai_item = ai_by_product_code[partname]
                ai_quantity_str = ai_item.get('quantity', '')
                ai_item_total_str = ai_item.get('item_total', '')
                
                # Extract numeric quantity from AI string (e.g., "1 EA" -> 1)
                ai_quantity_numeric = self._extract_numeric_quantity(ai_quantity_str)
                
                # Extract numeric price from AI string (e.g., "USD 383.04" -> 383.04)
                ai_price_numeric = self._extract_numeric_price(ai_item_total_str)
                
                # Validate quantity
                quantity_match = ai_quantity_numeric == priority_tquant
                
                # Validate price (allow small tolerance for rounding)
                price_difference = abs(float(priority_vatprice) - ai_price_numeric)
                price_match = price_difference < 0.01
                
                item_result.update({
                    "ai_quantity_raw": ai_quantity_str,
                    "ai_quantity_numeric": ai_quantity_numeric,
                    "ai_price_raw": ai_item_total_str,
                    "ai_price_numeric": ai_price_numeric,
                    "quantity_match": quantity_match,
                    "price_match": price_match,
                    "price_difference": price_difference,
                    "validation_passed": quantity_match and price_match
                })
                
                # Print detailed comparison
                print(f"Item: {partname}")
                print(f"  Quantity - Priority: {priority_tquant}, AI: '{ai_quantity_str}' ({ai_quantity_numeric}) - {'✓ MATCH' if quantity_match else '✗ MISMATCH'}")
                print(f"  Price - Priority: {priority_vatprice}, AI: '{ai_item_total_str}' ({ai_price_numeric}) - {'✓ MATCH' if price_match else f'✗ MISMATCH (diff: {price_difference})'}")
                print(f"  Overall: {'✓ VALID' if item_result['validation_passed'] else '✗ INVALID'}")
                print("-" * 60)
                
                # Track mismatches
                if not quantity_match:
                    validation_results["mismatches"]["quantity"].append({
                        "partname": partname,
                        "priority_quantity": priority_tquant,
                        "ai_quantity": ai_quantity_str,
                        "ai_quantity_numeric": ai_quantity_numeric
                    })
                    validation_results["all_items_valid"] = False
                
                if not price_match:
                    validation_results["mismatches"]["price"].append({
                        "partname": partname,
                        "priority_price": priority_vatprice,
                        "ai_price": ai_item_total_str,
                        "ai_price_numeric": ai_price_numeric,
                        "difference": price_difference
                    })
                    validation_results["all_items_valid"] = False
                    
                if not item_result["validation_passed"]:
                    validation_results["all_items_valid"] = False
                    
            else:
                # Item in Priority but not in AI extraction
                item_result.update({
                    "ai_quantity_raw": None,
                    "ai_quantity_numeric": None,
                    "ai_price_raw": None,
                    "ai_price_numeric": None,
                    "validation_passed": False
                })
                
                validation_results["mismatches"]["missing_in_ai"].append(partname)
                validation_results["all_items_valid"] = False
                
                print(f"Item: {partname}")
                print(f"  ✗ MISSING IN AI EXTRACTION")
                print("-" * 60)
            
            validation_results["item_details"].append(item_result)
        
        # Check for items in AI but not in Priority
        for ai_item in ai_items:
            product_code = ai_item.get('product_code', '')
            if product_code not in priority_by_partname:
                validation_results["mismatches"]["missing_in_priority"].append(product_code)
                validation_results["all_items_valid"] = False
                print(f"AI Item: {product_code}")
                print(f"  ✗ MISSING IN PRIORITY")
                print("-" * 60)
        
        # Summary
        print("VALIDATION SUMMARY:")
        print(f"  Total items valid: {'✓ YES' if validation_results['all_items_valid'] else '✗ NO'}")
        print(f"  Item count match: {'✓ YES' if validation_results['item_count_match'] else '✗ NO'} (Priority: {validation_results['priority_item_count']}, AI: {validation_results['ai_item_count']})")
        print(f"  Quantity mismatches: {len(validation_results['mismatches']['quantity'])}")
        print(f"  Price mismatches: {len(validation_results['mismatches']['price'])}")
        print(f"  Missing in AI: {len(validation_results['mismatches']['missing_in_ai'])}")
        print(f"  Missing in Priority: {len(validation_results['mismatches']['missing_in_priority'])}")
        print("=" * 80)
        
        return validation_results

    def validate_shipping_charges(self, priority_items: list, ai_items: list, ai_shipping_info: Dict = None) -> Dict:
        """
        Validate shipping charges between Priority (SH items) and AI extraction (Shipping & Handling or Expedited Handling)
        Args:
            priority_items: List of items from Priority PORDERITEMS_SUBFORM
            ai_items: List of items from AI extraction (should include shipping info)
            ai_shipping_info: Optional shipping information passed separately (e.g., from order_info level)
        Returns:
            Dict with shipping validation results
        """
        shipping_validation = {
            "shipping_validation_passed": True,
            "validation_case": "",
            "priority_shipping_items": [],
            "ai_shipping_info": None,
            "shipping_match": False,
            "price_difference": 0,
            "validation_message": ""
        }
        
        print("=" * 80)
        print("SHIPPING CHARGES VALIDATION:")
        print("=" * 80)
        
        # Find all Priority items that start with 'SH' (shipping items)
        priority_shipping_items = []
        total_priority_shipping = 0
        
        for item in priority_items:
            partname = item.get('PARTNAME', '')
            if partname.startswith('SH'):
                vatprice = item.get('VATPRICE', 0)
                priority_shipping_items.append({
                    "partname": partname,
                    "price": vatprice
                })
                total_priority_shipping += float(vatprice)
                print(f"Found Priority shipping item: {partname} - Price: {vatprice}")
        
        shipping_validation["priority_shipping_items"] = priority_shipping_items
        shipping_validation["priority_shipping_total"] = total_priority_shipping
        
        # Look for shipping information in AI extraction
        ai_shipping_extracted = ai_shipping_info or self._extract_ai_shipping_info(ai_items)
        shipping_validation["ai_shipping_info"] = ai_shipping_extracted
        
        if ai_shipping_extracted:
            ai_shipping_price = ai_shipping_extracted.get("price_numeric", 0)
            print(f"Found AI shipping info: {ai_shipping_extracted.get('raw_text', ai_shipping_extracted.get('source', 'External shipping info'))} - Price: {ai_shipping_price}")
        else:
            ai_shipping_price = 0
            print("No AI shipping information found")
        
        # Determine validation case and perform comparison
        has_priority_shipping = len(priority_shipping_items) > 0
        has_ai_shipping = ai_shipping_extracted is not None
        
        if has_priority_shipping and has_ai_shipping:
            # Case 1: Both exist - compare prices
            price_difference = abs(total_priority_shipping - ai_shipping_price)
            shipping_match = price_difference < 0.01  # Allow small tolerance for rounding
            
            shipping_validation.update({
                "validation_case": "both_exist_compare_prices",
                "shipping_match": shipping_match,
                "price_difference": price_difference,
                "shipping_validation_passed": shipping_match,
                "validation_message": f"Priority shipping ({total_priority_shipping}) vs AI shipping ({ai_shipping_price}): {'MATCH' if shipping_match else f'MISMATCH (diff: {price_difference})'}"
            })
            
            print(f"Validation Case: Both shipping charges exist")
            print(f"  Priority total shipping: {total_priority_shipping}")
            print(f"  AI shipping price: {ai_shipping_price}")
            print(f"  Price difference: {price_difference}")
            print(f"  SHIPPING MATCH: {'✓ YES' if shipping_match else '✗ NO'}")
            
        elif has_priority_shipping and not has_ai_shipping:
            # Case 2: Priority has shipping but AI doesn't
            shipping_validation.update({
                "validation_case": "priority_has_ai_missing",
                "shipping_match": False,
                "shipping_validation_passed": False,
                "validation_message": f"Priority has shipping items (total: {total_priority_shipping}) but AI extraction found no shipping charges"
            })
            
            print(f"Validation Case: Priority has shipping but AI missing")
            print(f"  Priority shipping items: {len(priority_shipping_items)}")
            print(f"  Priority total shipping: {total_priority_shipping}")
            print(f"  AI shipping: None found")
            print(f"  VALIDATION: ✗ FAILED - Missing shipping in AI")
            
        elif not has_priority_shipping and has_ai_shipping:
            # Case 3: AI has shipping but Priority doesn't
            shipping_validation.update({
                "validation_case": "ai_has_priority_missing",
                "shipping_match": False,
                "shipping_validation_passed": False,
                "validation_message": f"AI found shipping charges ({ai_shipping_price}) but Priority has no SH items"
            })
            
            print(f"Validation Case: AI has shipping but Priority missing")
            print(f"  AI shipping price: {ai_shipping_price}")
            print(f"  Priority shipping items: None found")
            print(f"  VALIDATION: ✗ FAILED - Missing shipping in Priority")
            
        else:
            # Case 4: Neither has shipping - this is valid
            shipping_validation.update({
                "validation_case": "neither_has_shipping",
                "shipping_match": True,
                "shipping_validation_passed": True,
                "validation_message": "No shipping charges found in either Priority or AI extraction"
            })
            
            print(f"Validation Case: No shipping charges in either system")
            print(f"  VALIDATION: ✓ PASSED - No shipping expected")
        
        print("=" * 80)
        
        return shipping_validation

    def _extract_ai_shipping_info(self, ai_items: list) -> Optional[Dict]:
        """
        Extract shipping information from AI items list
        Looks for items with shipping-related keywords in various fields
        """
        shipping_keywords = [
            "shipping & handling",
            "expedited handling", 
            "shipping",
            "handling",
            "freight",
            "delivery"
        ]
        
        # First, check if there's a dedicated shipping field in order_info
        # This would be handled at the main level, but we'll check items too
        
        for item in ai_items:
            # Check various possible field names where shipping might be stored
            possible_fields = ['product_code', 'description', 'item_description', 'name', 'partname']
            item_text = ""
            
            for field in possible_fields:
                if field in item:
                    item_text += f" {str(item[field])}"
            
            item_text = item_text.lower().strip()
            
            # Check if this item contains shipping keywords
            for keyword in shipping_keywords:
                if keyword in item_text:
                    # Found shipping item, extract price
                    price_raw = item.get('item_total', '') or item.get('price', '') or item.get('total_price', '')
                    price_numeric = self._extract_numeric_price(price_raw)
                    
                    return {
                        "found_keyword": keyword,
                        "raw_text": item_text,
                        "price_raw": price_raw,
                        "price_numeric": price_numeric,
                        "ai_item": item
                    }
        
        # Also check if shipping is provided as a separate field in the data structure
        # This might be in the order_info level rather than individual items
        return None

    def _extract_numeric_quantity(self, quantity_str: str) -> int:
        """Extract numeric quantity from string like '1 EA' -> 1"""
        try:
            # Extract the first number from the string
            numbers = re.findall(r'\d+', str(quantity_str))
            if numbers:
                return int(numbers[0])
            return 0
        except (ValueError, TypeError):
            return 0

    def _extract_numeric_price(self, price_str: str) -> float:
        """
        Extract numeric price from string with European formatting support
        Examples:
        - 'USD 383.04' -> 383.04
        - 'USD 7.157,16' -> 7157.16 (European format: periods as thousands, comma as decimal)
        - 'USD 1,234.56' -> 1234.56 (US format: commas as thousands, period as decimal)
        """
        try:
            if not price_str:
                return 0.0
                
            original_str = str(price_str)
            print(f"Extracting price from: '{original_str}'")
            
            # Remove currency symbols and extra spaces
            cleaned = re.sub(r'[^\d\.,]', '', original_str)
            
            if not cleaned:
                print(f"  No numeric content found, returning 0.0")
                return 0.0
            
            print(f"  After removing non-numeric: '{cleaned}'")
            
            # Determine format based on the position of commas and periods
            if ',' in cleaned and '.' in cleaned:
                # Both comma and period present - determine which is decimal separator
                last_comma = cleaned.rfind(',')
                last_period = cleaned.rfind('.')
                
                if last_comma > last_period:
                    # European format: "7.157,16" (comma is decimal separator)
                    # Remove periods (thousands separators) and replace comma with period
                    cleaned = cleaned.replace('.', '').replace(',', '.')
                    print(f"  Detected European format, converted to: '{cleaned}'")
                else:
                    # US format: "1,234.56" (period is decimal separator)
                    # Remove commas (thousands separators)
                    cleaned = cleaned.replace(',', '')
                    print(f"  Detected US format, converted to: '{cleaned}'")
                    
            elif ',' in cleaned and '.' not in cleaned:
                # Only comma present - likely European decimal separator
                # Replace comma with period for float conversion
                cleaned = cleaned.replace(',', '.')
                print(f"  Detected European decimal comma, converted to: '{cleaned}'")
                
            elif '.' in cleaned and ',' not in cleaned:
                # Only period present - could be decimal or thousands separator
                # Count digits after last period to determine
                parts = cleaned.split('.')
                if len(parts) == 2 and len(parts[1]) <= 2:
                    # Likely decimal separator (1-2 digits after period)
                    print(f"  Detected decimal period (US format): '{cleaned}'")
                    pass  # Keep as is
                else:
                    # Likely thousands separator - remove periods
                    cleaned = cleaned.replace('.', '')
                    print(f"  Detected thousands separator, converted to: '{cleaned}'")
            
            # Convert to float
            result = float(cleaned) if cleaned else 0.0
            print(f"  Final numeric value: {result}")
            return result
            
        except (ValueError, TypeError) as e:
            print(f"  Error converting '{price_str}' to numeric: {e}")
            return 0.0

    def update_order_number(self, customer_po: str, order_number: str = None, supplier_name: str = None) -> Tuple[bool, str]:
        """
        Update Priority order with new order number and supplier name
        Args:
            customer_po: The PO number (e.g., 'PO2410000285')
            order_number: The new order number (SUPORDNUM) - if None, will use from existing order data
            supplier_name: The supplier name (SUPNAME) - if None, will use from existing order data
        Returns: (success: bool, message: str)
        """
        try:
            # Get current order data to extract SUPORDNUM and SUPNAME if not provided
            if order_number is None or supplier_name is None:
                success, order_data, _ = self.get_order_data(customer_po)  # Ignore price validation here
                if not success or not order_data.get('value'):
                    return False, "Could not retrieve order data from Priority"
                
                order_info = order_data['value'][0]
                if order_number is None:
                    order_number = order_info.get('SUPORDNUM', '')
                if supplier_name is None:
                    supplier_name = order_info.get('SUPNAME', '')
            
            # Validate that both SUPORDNUM and SUPNAME are not None/empty
            if not order_number:
                return False, "SUPORDNUM cannot be null or empty - order number is required"
            
            if not supplier_name:
                return False, "SUPNAME cannot be null or empty - supplier name is required"
            
            # URL encode the PO number
            encoded_po = quote(customer_po)
            
            # Construct the URL for updating the order
            url = f"{self.base_url}/odata/Priority/tabula.ini/eld0999/PORDERS?$filter=ORDNAME eq '{encoded_po}'"
            
            # Prepare update data
            update_data = {
                "SUPORDNUM": order_number,
                "SUPNAME": supplier_name
            }
            
            # Prepare headers for PATCH request
            patch_headers = {
                'Content-Type': 'application/json',
                'Authorization': self.headers['Authorization']
            }
            
            print(f"Updating Priority order number for PO: {customer_po}")
            print(f"Order number (SUPORDNUM): {order_number}")
            print(f"Supplier name (SUPNAME): {supplier_name}")
            print(f"URL: {url}")
            print(f"Update payload: {json.dumps(update_data, indent=2)}")
            
            # Make PATCH request
            response = requests.patch(url, headers=patch_headers, data=json.dumps(update_data))
            
            print(f"Priority order number update response: {response.status_code}")
            
            # Check for detailed error info
            if response.status_code >= 400:
                print(f"Error details:")
                print(f"  Status: {response.status_code}")
                print(f"  Reason: {response.reason}")
                try:
                    error_json = response.json()
                    print(f"  Error JSON: {json.dumps(error_json, indent=2)}")
                except:
                    print(f"  Raw response: {response.text}")
                    
                return False, f"Order number update failed with status {response.status_code}: {response.text}"
            
            if response.status_code in [200, 204]:
                return True, f"Order number updated successfully: SUPORDNUM={order_number}, SUPNAME={supplier_name}"
            else:
                return False, f"Unexpected response status: {response.status_code}"
                
        except requests.exceptions.RequestException as req_error:
            error_msg = f"Network error updating order number: {str(req_error)}"
            print(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error updating order number: {str(e)}"
            print(error_msg)
            return False, error_msg