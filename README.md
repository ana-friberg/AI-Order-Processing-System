# AI Order Processing System

An automated pipeline that processes supplier order confirmation PDFs using Claude AI, validates extracted data against the Priority ERP, and updates delivery dates and order status — with no manual data entry.

## Overview

When a supplier sends an order confirmation PDF, this service:

1. Extracts the Customer PO number from the first page
2. Looks up the order in Priority ERP and retrieves the expected line items
3. Sends the full PDF to Claude for structured data extraction (quantities, prices, delivery dates)
4. Cross-validates AI-extracted values against Priority ERP data (prices, quantities, shipping)
5. Patches delivery dates on each line item in Priority
6. Updates the supplier order number on the purchase order
7. Sets the order status
8. Persists the full result to MongoDB

## Architecture

```
main.py                      Flask app entry point
api/routes.py                REST endpoints: POST /process, POST /clean-cache
core/
  claude_processor.py        PDF-to-image conversion + Claude API calls
  pdf_processor.py           Low-level PDF utilities (PyMuPDF)
  order_validator.py         Extraction result validation logic
integrations/
  priority_api.py            Priority ERP OData client (fetch, PATCH, status update)
  mongodb_handler.py         MongoDB Atlas persistence
config/
  constants.py               Tuning parameters (model, batch size, tolerances)
  secrets.py                 Loads credentials from environment / .env
utils/
  price_utils.py             Numeric price and quantity parsing helpers
  cache_manager.py           Temp-file cleanup for /clean-cache
  logging_config.py          Structured logger setup
```

## API Endpoints

### `POST /process`

Upload an order confirmation PDF and run the full pipeline.

**Auth:** HTTP Basic Authentication (configured via `AUTHORIZATION_USERNAME` / `AUTHORIZATION_PASSWORD`)

**Request:** `multipart/form-data` with a PDF file in the `files` field

**Example (curl):**
```bash
curl -X POST https://<host>/process \
  -u "apiuser:s3cr3t" \
  -F "files=@order_confirmation.pdf"
```

**Success response (200):**
```json
{
  "success": true,
  "customer_po": "PO2400000042",
  "filename": "order_confirmation.pdf",
  "extraction_validation": {
    "length_match": true,
    "expected_count": 2,
    "extracted_count": 2,
    "missing_partnames": [],
    "quantity_mismatches": [],
    "price_mismatches": []
  },
  "price_validation": {
    "validation_attempted": true,
    "price_match": true,
    "priority_totprice": 2350.0,
    "ai_total_price": 2350.0,
    "price_difference": 0.0,
    "validation_message": "Match — Priority=2350.0, AI=2350.0, diff=0.0000",
    "overall_validation_passed": true
  },
  "shipping_validation": {
    "shipping_validation_passed": true,
    "validation_case": "both_exist_compare_prices",
    "shipping_match": true,
    "priority_shipping_total": 150.0,
    "ai_shipping_price": 150.0,
    "validation_message": "Priority shipping 150.0 vs AI 150.0: MATCH"
  },
  "delivery_address": "Demo Customer Ltd\nIndustrial Zone\n5 Commerce Street\n12345 DEMO CITY\nISRAEL",
  "ai_extracted_total_price": "USD 2,350.00",
  "mongodb_saved": true
}
```

**Error responses:**

| Status | Meaning |
|--------|---------|
| 400 | No file uploaded, empty filename, or PO not found in PDF |
| 401 | Missing Authorization header |
| 403 | Invalid credentials |
| 404 | Customer PO does not exist in Priority |
| 500 | Unhandled server error (trace included in response) |

---

### `POST /clean-cache`

Remove temporary files from the `doc/` and `output/` directories.

**Request (optional JSON body):**
```json
{ "filename": "order_confirmation.pdf" }
```
Omit the body to clean all cached files.

---

## Processing Pipeline — Step by Step

### Step 1 — Extract Customer PO

The first page of the PDF is rendered as a JPEG and sent to Claude. The model looks for a 12-character PO in the format `PO` + 10 digits (e.g. `PO2400000042`).

### Step 2 — Priority ERP lookup

The extracted PO is queried against the Priority OData API. The response provides the list of expected `PARTNAME` values (product codes) for that order. Shipping line items (prefix `SH-`) are filtered out before validation.

### Step 3 — Full document extraction

All pages are rendered to JPEG images and sent to Claude with the list of PARTNAMEs.

- Documents up to 6 pages → single API call
- Longer documents → batched in groups of 4 pages

Claude returns structured JSON with order header info (PO number, order date, delivery address, total price, shipping cost) and per-line-item data (product code, quantity, unit price, extended price, delivery date).

### Step 4 — Validation

| Check | What is verified |
|-------|-----------------|
| Item count | Number of extracted items matches Priority |
| Quantities | Per-item quantity matches Priority |
| Prices | Per-item price matches Priority (tolerance: ±$0.01) |
| Total price | AI grand total matches Priority `PRICE` |
| Shipping | AI shipping charge matches the Priority `SH-*` line item |

### Step 5 — Update delivery dates

For each validated line item, a `PATCH` request updates `DATE` on the corresponding Priority order line. If the calculated date falls on a Saturday it is automatically shifted to the preceding Friday.

### Step 6 — Update order number

The supplier's own order reference number and supplier code are patched onto the Priority purchase order.

### Order status

| Outcome | Status set in Priority |
|---------|----------------------|
| All validations passed | (Supplier Approved) |
| Any discrepancy found | (Sent to Supplier) |

---

## Sample Log Trace

```
16:05:45 [INFO]  Step 1: Extracting customer PO
16:05:49 [INFO]  Extracted customer PO: PO2400000042
16:05:49 [INFO]  Step 2: Checking Priority and extracting PARTNAMEs
16:05:54 [DEBUG] Filtered out SH items: ['SH-VENDOR']
16:05:54 [INFO]  Priority items: 2 total, 1 non-SH sent for validation
16:05:54 [INFO]  Found 1 PARTNAMEs in Priority: ['ITEM-001']
16:05:54 [INFO]  Step 3: Processing full document
16:05:54 [INFO]  Processing 2 pages with 1 PARTNAMEs from order_confirmation.PDF
16:06:03 [INFO]  Step 4: Validating extraction results
16:06:03 [INFO]  Prepared 1 items for Priority update
16:06:03 [INFO]  Delivery address: Demo Customer Ltd / 5 Commerce Street / DEMO CITY
16:06:03 [INFO]  AI shipping info found: USD 150.00
16:06:03 [INFO]  Step 5: Updating Priority delivery dates
16:06:04 [INFO]  Price validation — Priority=2350.0 AI='USD 2,350.00'→2350.0 diff=0.0 match=YES
16:06:04 [DEBUG] Item ITEM-001 — qty: 1 vs 1 EA | price: 2200.0 vs USD 2,200.00 (diff=0.0) | OK
16:06:04 [INFO]  Item validation: all_valid=True count_match=True
16:06:04 [DEBUG] PATCH ITEM-001 → DATE=2024-06-28T00:00:00+03:00
16:06:07 [DEBUG] PATCH response for ITEM-001: HTTP 200
16:06:07 [INFO]  Setting order status → approved
16:06:09 [INFO]  Step 6: Updating order number
16:06:11 [INFO]  Updating order number: ORDNUM=100000001 SUB_NAMEE=DEMO-VENDOR
16:06:12 [INFO]  Order number update: ORDNUM=100000001, SUB_NAME=DEMO-VENDOR
16:06:15 [INFO]  MongoDB: Updated existing document for PO: PO2400000042
```
---

## Security Notes

- Credentials are loaded exclusively from environment variables — never hard-coded.
- The `/process` endpoint requires HTTP Basic Authentication on every request.
- Do not share patient data, proprietary formulations, or PII in API requests or logs.
- Rotate `ANTHROPIC_API_KEY_AGILENT` and `PRIORITY_TOKEN` if they are ever exposed.
