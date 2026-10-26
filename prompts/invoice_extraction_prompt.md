We are providing the invoice as a series of images. There may be multiple images corresponding to a single invoice. The invoice may come in any language. You need to carefully analyze all the images and follow the instructions precisely to extract the required information. Please approach this task step by step, using a Tree of Thoughts strategy to explore and reason through various possibilities for each field before finalizing the extraction.

**Instructions:**

For each field, follow a branching thought process. Consider possible sources of the data, decide how to identify it, and extract the correct value. If a field is missing or empty, DO NOT include it in the output.

**Step 1: Identify the VENDOR_NAME**  
- The vendor, seller, or supplier name typically appears at the top of the invoice.
- Only extract the name value—exclude domain extensions such as ".com", ".in", etc.

**Step 2: Identify vendor address** 
- Extract the seller's address (i.e., vendor/supplier).
- Only extract the vendor/supplier address, not billing or shipping addresses.
- Identify the section containing vendor details, which may appear:
    - Immediately below the vendor or supplier name, or
    - At the bottom of the invoice, labeled with terms like "Supplier," "Issued By," or "Sold By."
    - Do not confuse this with “Bill To” or “Ship To” sections—those belong to the customer.
- Split the vendor address into two parts: Vendor ADDRESS_LINE1 and Vendor ADDRESS_LINE2.
- Extract the following components from the vendor or supplier address:
    - VENDOR_CITY
    - VENDOR_STATE
    - VENDOR_COUNTRY_CODE: Extract the country code using the ISO 3166-1 alpha-2 standard. The code should be a valid two-letter country code (e.g., "US", "CA", "IN"). If the country code is not explicitly mentioned or is unclear, return None.
    - VENDOR_POSTAL_CODE
- Follow the steps carefully to extract the above details from the vendor or supplier information.
- Do not extract addresses under headers like “Bill To,” “Ship To,” or “Deliver To” in this step.

**Step 3: Identify the bill to address** 
- Check if a Bill To address is provided.
- If "Bill To" is not found, check for other common labels such as:
    - "Sold To"
    - "Invoicing Address"
    - "Customer Address"
    - "Invoice To"
    - "Consignee"
- From the most relevant billing-related address, extract the following:
    - BILL_TO_COMPANY_NAME : Extract the name of the company or customer being billed. This is usually the **first line** of the billing address. Ensure it refers to a legal entity or customer name. If not clearly mentioned, return `None`.
    - BILL_TO_ADDRESS: Extract the complete billing address as provided.
    - BILL_TO_CITY: Extract the city name from the selected address, otherwise return None. Ensure the city name is accurate and complete. Verify the spelling to match the original text. If the city is not found, return None.
    - BILL_TO_COUNTRY_CODE: Extract the country code using the ISO 3166-1 alpha-2 standard. The code should be a valid two-letter country code (e.g., "US", "CA", "IN"). If the country code is not explicitly mentioned or is unclear, return None.
- Bill To details are mandatory: BILL_TO_COMPANY_NAME, BILL_TO_ADDRESS, BILL_TO_CITY, and BILL_TO_COUNTRY_CODE.

**Step 4: Extract Customer Information (Name, Address, and City)**  
- Check if a Shipping Address is provided:
    - Extract CUSTOMER_NAME from this section.
    - Extract CUSTOMER_ADDRESS as the full address string.
    - Extract CUSTOMER_CITY from the CUSTOMER_ADDRESS.
- If the Shipping Address is not found, check for alternative labels such as:
    - "Ship To"
    - "Delivery Address"
    - "Recipient Address"
    - "Consignee"
- If none of the above are available, fall back to the billing-related section using these labels:
    - "Bill To"
    - "Sold To"
    - "Invoicing Address"
    - "Customer Address"
- From the most appropriate available section (in the above order of preference), extract:
    - CUSTOMER_NAME
    - CUSTOMER_ADDRESS (full address as-is)
    - CUSTOMER_CITY (derived from CUSTOMER_ADDRESS)
- Customer details are mandatory: CUSTOMER_NAME, CUSTOMER_ADDRESS, and CUSTOMER_CITY.
- Ensure that the extracted information refers to the customer or recipient of goods/services.
- Do not extract unrelated addresses (e.g., supplier, warehouse, or internal company locations).

**Step 5: Extract the CURRENCY_CODE (Critical for Invoice Processing)**  
- Scan the invoice text for valid currency identifiers conforming to the ISO 4217 standard (e.g., USD, EUR, GBP, INR, CNY, TRY).
- If a valid currency code is identified, extract and assign it to the CURRENCY_CODE field.
- If no valid currency code is present, set CURRENCY_CODE to null.
- ⚠️ Note: Accurate identification of the CURRENCY_CODE is essential for correct financial processing and conversions.

**Step 6: Extract the INVOICE_DATE**  
- The Invoice Date is mandatory and must not be empty or None.
- Look for a date explicitly labeled as 开票日期 (Invoice Date), 发票日期 (Billing Date), or 交易日期 (Transaction Date).
- If not found, consider dates under headers like 日期 (Date), 账单日期 (Billing Date), or similar fields that logically represent the invoice issue date.
- Chinese dates may appear in YYYY年MM月DD日 format. Extract and convert them into the YYYY-MM-DD format.
- If multiple dates exist, select the one that aligns best with invoice issuance or billing.
- If a date is present without an explicit label, extract the most contextually relevant date rather than returning None.

**Step 7: Extract the INVOICE_NUMBER**  
- Check the document content for any reference to an Invoice Number.
- Look for patterns such as:
    - "Invoice Number: INV-123456", "Invoice #: 456789", "Invoice No.: 789654", etc.
- If found, extract the full string of invoice number immediately following the relevant keyword.
- If no reference is found, set "INVOICE_NUMBER": None.

**Step 8: Extract the INVOICE_AMOUNT**  
- Look for a reference to the total amount of the invoice. This may be labeled as "total," "amount," "net amount," etc.  
- If found, extract the amount. If not, set `INVOICE_AMOUNT` to None.

**Step 9: Extract the PAYMENT_TERMS**  
- Find payment terms (if mentioned).
- Extract up to 60 characters; truncate only if needed.
- If not found, set to None.
- Examples:
    - "Net 30 days" → "Net 30 days"
    - "Payment due within 15 days of invoice date" → "Payment due within 15 days"
    - "Full payment required prior to shipment" → "Full payment required prior to"
    - No terms → None

**Step 10: Extract the PO_NUMBER (Purchase Order Number Only)**  
- Search the document content for a clear and explicit reference to a **Purchase Order Number (PO_NUMBER)**.  
- Common Valid identifiers for a PO_NUMBER include:
  - "PO Number:", "Purchase Order:", "P.O. #", "PO#", "Purchase Order Number:"
- **Extract the full string** immediately following these exact identifiers.
  - Example: "PO Number: 123456" → `123456`
- ❌ Do not extract values labeled with invalid identifiers, such as:
  - "Order Number", "Order Ref", "Order No:", "Sales Order Number (SO)", "PO BOX", "Account Number", "Customer No.", "Customer ID"
- ⚠️ Do not treat "Order Number" or "Account Number" as PO_NUMBERs even if they appear in similar formats.
  - Example: "Order Number: 4500123" → Not a PO_NUMBER
  - Example: "Account Number: 1387576" → Not a PO_NUMBER
- Avoid assumptions based on format, context, or position. Only extract a PO_NUMBER if it is explicitly labeled with a valid identifier.
- If no valid reference to a purchase order number is found, set `PO_NUMBER` to `None`.

**Step 11: Determine the DOCUMENT_TYPE**  
- If the `PO_NUMBER` is present, set `DOCUMENT_TYPE` to "PO".  
- If no `PO_NUMBER` is present, set `DOCUMENT_TYPE` to "NON-PO".  
- This field is mandatory and must be extracted. Default value is "NON-PO".

**Step 12: Extract the TAX_AMOUNT**  
- Look for any mention of a tax amount.  
- If found, extract it. If not, set `TAX_AMOUNT` to None.

**Step 13: Extract the IDENTIFICATION_NUMBER**  
- This step is strictly applicable to **Chinese and Japanese language invoices**.
- For **Chinese invoices**:
  - Typically include **two identification numbers**: one for the **supplier** and one for the **buyer**.
  - Extract both if available and return them in a list: `[supplier_id, buyer_id]`.
  - These may be referred to as the Unified Social Credit Code (统一社会信用代码) or the Taxpayer Identification Number (纳税人识别号).
- For **Japanese invoices**:
  - Usually contain **only one identification number** (typically for the **supplier**).
  - Extract and return it as a single-item list: `[supplier_id]`.
  - This may be referred to as a Corporate Number (法人番号) or another registered tax ID.
- If no valid identification number is found, set `IDENTIFICATION_NUMBER = None`.

**Step 14: Determine the INVOICE_TYPE** 
- Check the invoice text for specific keywords to identify the invoice type:
    - If the text includes the word "STANDARD", set INVOICE_TYPE to "STANDARD".
    - If the text includes the word "PREPAYMENT", set INVOICE_TYPE to "PREPAYMENT".
    - If the text includes the word "CREDIT" or if the total amount in the invoice is negative, set INVOICE_TYPE to "CREDIT".
    - If no specific invoice type is identified, set the default INVOICE_TYPE to "STANDARD".
- This field is mandatory and must be extracted.

**Step 15: Determine the SUBTOTAL** 
- check the subtotal of all lines

**Step 16: Detect Invoice Language**
- Identify the language of the invoice content (e.g., English, French, German, Spanish, Chinese).
- Return the detected language code as the field language_code with the ISO 639-1 two-letter code in uppercase (e.g., "EN" for English, "FR" for French).
- If the language cannot be confidently identified, set language_code to "UNK". This is mndatory field.
- Return only the language_code field value as a string; do not include any additional text, explanation, or formatting.

**Step 17: Extract LINE_ITEMS**  
Carefully analyze the visual layout, table structure, and text alignment to accurately extract line item details, For each line item on the invoice, perform the following:
- **Visual Analysis Guidelines for line details:**
    - Line items may not be clearly separated due to formatting, scanning, or image issues.
    - Detect table structures using text alignment, repeating rows, headers, and consistent spacing; use proximity to associate Quantity, Unit Price, Description, and Amount.
    - Line items include products, services, taxes, freight, surcharges, delivery charges, or other fees.
    - Additional financial components (e.g., surcharges, fuel surcharges, service charges, freight, VAT, or similar) may appear outside the main table, often near the Total Amount; analyze the full document to extract these as separate line items.
- **LINE_TYPE**: Identify the line type from the given information. Return only one of the following values: 'Freight,' 'Item,' 'Miscellaneous,' or 'Tax.' If no relevant information is found, return 'None'.
    - Item: Default type, typically applies to product or service lines. CGST, SGST, IGST, CESS, GST are also considered under "Item" if not explicitly classified.
    - Tax: Includes tax components such as country sales tax, state sales tax, use tax, VAT, Excise Duty, Service Tax, Withholding Tax, Customs Duty, Digital Services Tax, and similar tax charges.
    - Freight: Applies specifically to lines referring to freight.
    - Miscellaneous: Covers additional charges such as transportation, delivery fees, packaging, handling, fuel surcharges, surcharges, energy charges, and any other miscellaneous fees that do not fall under 'Item', 'Tax', or 'Freight'.
- **ITEM_CODE**: Does the text provide an item code? If yes, extract it (up to 40 characters); if not, set it to None.
- **LINE_DESCRIPTION**: If a description is visible (even if misaligned or fragmented), extract it. If no description is found, set it to None.
- **QUANTITY**: Search for a number in the column typically related to quantity (e.g., near “QUANTITY” or “UNITS”). If no quantity is found, set it to None. 
- **UNIT_OF_MEASURE**: If a unit of measure (e.g., pieces, hours) is mentioned near the quantity, extract it. If not, set it to None. convert to english
- **UNIT_PRICE**: Look for price values next to the unit or price labels. Be cautious of misalignments. If no unit price is found, set it to None.
- **LINE_AMOUNT**: Extract the amount listed next to the line item. Ensure that the amount excludes taxes. If the line item amount cannot be found, set it to None.
- **LINE_TAX_AMOUNT**: If a specific tax amount is listed for the line item, extract it. If not, set it to None.
- **TAX_RATE**: If a tax rate is mentioned, extract it. If not, set it to None.
- **LINE_NUMBER**: Assign a sequential number starting from 1 for each line item.
- **Additional Instruction for line details**:
    - Extract all line items accurately, including any additional charges such as freight, service charges, shipping and handling charges, surcharges, delivery charges, fuel surcharges, GST or any other applicable fees, as separate line items with the appropriate LINE_TYPE.
    - For each line item, extract LINE_AMOUNT (excluding taxes). If QUANTITY and UNIT_PRICE are both present, validate that LINE_AMOUNT = QUANTITY × UNIT_PRICE. If validation fails, use the extracted LINE_AMOUNT as-is or set to None if unavailable.
    - Sometimes, line item values may appear as negative amounts due to adjustments, discounts, retentions, or credit notes. Extract the negative values exactly as they appear without modification(keep the sign as is).
    - Correctly identifying and extracting each individual line item is mandatory to maintain consistency and accuracy in total matching.
    - For GST components, the percentage may vary. Therefore, extract each CGST, SGST, and IGST as separate line items based on their respective percentages. Do not combine them if the percentages differ. The LINE_TYPE must be strictly set to 'Item' for each CGST, SGST, IGST, GST component.
- **Strict Financial Validation**:The total invoice amount must equal the sum of all extracted LINE_AMOUNTs, additional charges, and taxes. This validation is critical for financial accuracy — ensure all components are extracted and calculated precisely.

**Important Note about Formatting:**
- **Avoid trailing commas**: In JSON, trailing commas are **not allowed**. Be sure to **remove any trailing commas** after the last item in any list or object. For example, in the `"header"` section, **do not include a trailing comma after the last field** (e.g., after `"IDENTIFICATION_NUMBER": None`). Similarly, do not include trailing commas after the last item in the `"lineItem"` array.

After analyzing the text step by step, return just the standard JSON object with the extracted fields. Only include the fields you have identified. If a field is not present or cannot be determined, set it to `None`. Avoid including any explanations or extra text.

The output JSON should follow this structure:

{
    "header": {
        "VENDOR_NAME": "vendor_name",
        "CUSTOMER_ADDRESS": "ship to customer_address",
        "CUSTOMER_CITY": "customer city",
        "CUSTOMER_NAME": "ship to customer_name",
        "BILL_TO_COMPANY_NAME": "bill to company name",
        "BILL_TO_ADDRESS": "bill to address",
        "BILL_TO_COUNTRY_CODE":"bill to country",
        "BILL_TO_CITY":"bill to city",
        "ADDRESS_LINE1": "vendor address_line_1",
        "ADDRESS_LINE2": "vendor address_line_2",
        "VENDOR_CITY": "vendor city",
        "VENDOR_STATE": "vendor state",
        "VENDOR_COUNTRY_CODE": "vendor country_code",
        "VENDOR_POSTAL_CODE": "vendor postal_code",
        "PAYMENT_TERMS": "payment_terms",
        "PO_NUMBER": "po_number",
        "INVOICE_NUMBER": "invoice_number",
        "DOCUMENT_TYPE": "document_type",
        "INVOICE_TYPE": "invoice_type",
        "INVOICE_DATE": "invoice_date",
        "INVOICE_AMOUNT": "invoice_amount",
        "CURRENCY_CODE": "currency_code",
        "TAX_AMOUNT": "tax_amount",
        "SUBTOTAL": "subtotal",
        "LANGUAGE_CODE" : "language_code",
        "IDENTIFICATION_NUMBER": [identification_number]
    },
    "lineItems": {
        "lineItem": [
            {
                "LINE_TYPE": line_type_1,
                "LINE_AMOUNT": line_amount_1,
                "ITEM_CODE": item_code_1,
                "LINE_DESCRIPTION": line_description_1,
                "QUANTITY": quantity_1,
                "UNIT_OF_MEASURE": uom_1,
                "UNIT_PRICE": unit_price_1,
                "LINE_TAX_AMOUNT": line_tax_amount_1,
                "TAX_RATE": tax_rate_1,
                "LINE_NUMBER":line_number1
            }
        ]
    }
}

**Important Note:**
- This task directly impacts financial accuracy — strictly follow all defined instructions. Each instruction is mandatory and must be followed without exception.
- Ensure that all extractions are fully accurate and strictly reflect the original text in the invoice image. Avoid omitting characters, altering values, or introducing assumptions.
- All extracted values must be based solely on the provided text. Do not infer, guess, or hallucinate any data.
- If any field cannot be accurately extracted due to missing or ambiguous information, return null or omit the field according to the field-specific instructions.
- Penalties will apply for any incorrect, hallucinated, or assumed predictions.
- Output only the extracted JSON object, strictly following the defined JSON structure, without any additional explanation, notes, or formatting.