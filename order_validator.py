from typing import Dict
from decimal import Decimal
import json

class OrderValidator:
    @staticmethod
    def clean_number(value: str) -> Decimal:
        """Convert string numbers to Decimal, handling currency and commas"""
        try:
            if isinstance(value, (int, float)):
                return Decimal(str(value))
            
            if not value:  # Handle empty strings
                return Decimal('0')
            
            # Remove currency symbol and whitespace
            cleaned = value.replace("USD", "").strip()
            
            # Replace European comma with dot and remove thousands separators
            if ',' in cleaned:
                # Handle European format (e.g., "383,04")
                cleaned = cleaned.replace(".", "")  # Remove thousand separators
                cleaned = cleaned.replace(",", ".")  # Convert decimal comma to dot
            
            # Handle negative values marked with trailing dash
            if cleaned.endswith('-'):
                cleaned = '-' + cleaned[:-1]
                
            return Decimal(cleaned)
            
        except Exception as e:
            print(f"Error cleaning number '{value}': {str(e)}")
            return Decimal('0')

    @staticmethod
    def clean_quantity(value: str) -> int:
        """Convert quantity string to integer"""
        try:
            if isinstance(value, (int, float)):
                return int(value)
            
            # Extract first number from string (e.g., "1 EA" -> 1)
            number = ''.join(c for c in value.split()[0] if c.isdigit())
            return int(number) if number else 0
            
        except Exception as e:
            print(f"Error cleaning quantity '{value}': {str(e)}")
            return 0

    @classmethod
    def validate_orders(cls, agilent_data: Dict, priority_data: Dict) -> Dict:
        """Compare Agilent order data with Priority data"""
        results = {
            "status": "VALIDATING",
            "mismatches": [],
            "missing_in_priority": [],
            "missing_in_agilent": [],
            "incomplete_items": []  # Track items with missing data
        }

        # Check for incomplete items in Agilent data
        for item in agilent_data.get("items", []):
            if item.get("product_code") and not item.get("item_total"):
                results["incomplete_items"].append({
                    "product_code": item["product_code"],
                    "reason": "Missing total price"
                })

        # Create lookup dictionary for Priority items
        priority_items = {
            item["PARTNAME"]: item 
            for item in priority_data["value"][0]["PORDERITEMS_SUBFORM"]
        }

        # Create lookup dictionary for Agilent items
        agilent_items = {
            item["product_code"]: item 
            for item in agilent_data.get("items", [])
            if "product_code" in item
        }

        # Check each Agilent item against Priority
        for product_code, agilent_item in agilent_items.items():
            if product_code in priority_items:
                priority_item = priority_items[product_code]
                try:
                    agilent_quantity = cls.clean_quantity(agilent_item["quantity"])
                    priority_quantity = int(priority_item["TQUANT"])
                    
                    if "item_total" in agilent_item:
                        agilent_total = cls.clean_number(agilent_item["item_total"])
                        priority_total = Decimal(str(priority_item["VATPRICE"]))

                        if agilent_quantity != priority_quantity or abs(agilent_total - priority_total) > Decimal('0.01'):
                            results["mismatches"].append({
                                "product_code": product_code,
                                "quantity": {
                                    "agilent": agilent_quantity,
                                    "priority": priority_quantity
                                },
                                "total": {
                                    "agilent": str(agilent_total),
                                    "priority": str(priority_total)
                                }
                            })
                except (ValueError, KeyError) as e:
                    print(f"Error comparing item {product_code}: {str(e)}")
            else:
                results["missing_in_priority"].append(product_code)

        # Check for items in Priority but not in Agilent
        for partname in priority_items:
            if partname not in agilent_items:
                results["missing_in_agilent"].append(partname)

        # Update status to include incomplete items
        if results["incomplete_items"]:
            results["status"] = "INCOMPLETE_DATA"
        elif not any([results["mismatches"], 
                    results["missing_in_priority"], 
                    results["missing_in_agilent"]]):
            results["status"] = "SAME"
        else:
            results["status"] = "MISMATCH"

        return results

# Usage example
def validate_order(agilent_json_path: str, priority_response: Dict) -> Dict:
    """Validate order data between Agilent and Priority systems"""
    try:
        # Load Agilent data
        with open(agilent_json_path, 'r') as f:
            agilent_data = json.load(f)

        # Validate
        validator = OrderValidator()
        results = validator.validate_orders(agilent_data, priority_response)
        
        return results

    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e)
        }