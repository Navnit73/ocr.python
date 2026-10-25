# German E-Invoicing API (ZUGFeRD 2.x / XRechnung)

This API creates, extracts and validates German e-invoices. It lives under `/api/einvoice` and leaves every existing route, the auth layer and the database layer untouched.

| Format | What it is | Use case | Output |
|---|---|---|---|
| **ZUGFeRD 2.x (EN 16931 / COMFORT)** | Hybrid: a readable PDF/A-3 with machine-readable CII XML embedded as `factur-x.xml` | B2B | `.pdf` |
| **XRechnung 3.x (CII syntax)** | Pure XML, no visual part. Needs a **Leitweg-ID** | B2G (public sector, e.g. ZRE/OZG-RE portals) | `.xml` |

The profile is always **EN 16931 (COMFORT)**, URN `urn:cen.eu:en16931:2017`. MINIMUM and BASIC WL are not valid invoices under the German VAT Act (UStG). `/validate` rejects them.

---

## 1. Endpoints at a glance

All endpoints need `X-API-Key: <key>` or `Authorization: Bearer <key>`, the same as the rest of the API. They are also rate-limited.

| Method & path | Input | Returns |
|---|---|---|
| `POST /api/einvoice/create-zugferd` | multipart: `invoice_data` (JSON string, ≤ 1 MB) **or** `invoice_file` (JSON file, for large invoices), optional `file` (PDF); `?template=classic\|modern\|minimal` | ZUGFeRD PDF plus a validation summary in headers. With `?output=json`: `{pdf_base64, xml, validation, archive}` |
| `POST /api/einvoice/create-xrechnung` | JSON body (same invoice schema) | XRechnung XML. With `?output=json`: `{xml, validation, archive}` |
| `POST /api/einvoice/extract` | multipart: `file` (ZUGFeRD PDF or XML) | Parsed invoice JSON, raw XML, detected profile, version and XMP data |
| `POST /api/einvoice/validate` | multipart: `file` (PDF or XML) | `{valid, errors[], warnings[], checks, profile, version}` |
| `POST /api/einvoice/convert` (also at `/api/v1/convert`) | multipart: `file` (PDF), `invoice_data` or `invoice_file` (flat form JSON); `?template=…` | ZUGFeRD PDF or XRechnung XML, chosen by `format_type` |
| `GET /api/einvoice/archive/{archive_id}` | – | Integrity check of an archived record (hashes and ledger chain) |

Interactive docs with examples (sample invoice 471102) are at **`/docs`**, under the tag *E-Invoicing (ZUGFeRD / XRechnung)*.

---

## 2. Folder structure (new files only)

```
app/
├── api/einvoice/
│   ├── __init__.py
│   └── router.py              # 6 endpoints, upload checks (10 MB, magic bytes, MIME, extension), 422 mapping
├── core/einvoice_config.py    # EINVOICE_* settings (separate from core Settings)
├── schemas/
│   ├── einvoice.py            # Pydantic models: EInvoiceData (BT-/BG- annotated), flat InvoiceDataSchema, responses
│   └── einvoice_examples.py   # Sample invoice 471102 (ZUGFeRD, XRechnung and flat variants)
└── services/einvoice/
    ├── codes.py               # Code lists (units, countries, currencies, VAT categories), IBAN/GTIN/Leitweg checksums
    ├── calculator.py          # Server-side recalculation of lines, VAT breakdown and totals
    ├── rules.py               # EN 16931 + XRechnung BR-DE + UStG §14 checks
    ├── cii_builder.py         # CII D16B XML generator (lxml)
    ├── xml_parser.py          # Secure XML parsing (no DTD/XXE), CII + UBL → invoice JSON
    ├── validator.py           # XSD → mapping → business rules → KoSIT → PDF/XMP checks
    ├── kosit.py               # KoSIT validator runner and VARL report parser
    ├── pdf.py                 # PDF rendering (reportlab), PDF/A-3 embedding (factur-x), XML/XMP extraction
    ├── archive.py             # Write-once archive with SHA-256 and a hash-chained ledger
    └── service.py             # Orchestration (create flows, flat-form mapping)
tests/test_einvoice.py         # 40 tests built on sample invoice 471102
```

`app/main.py` gets only additive changes: one OpenAPI tag and three `include_router` lines.

---

## 3. Setup

```bash
pip install -r requirements.txt        # adds factur-x, lxml, pypdf
```

Environment variables (all optional, prefix `EINVOICE_`):

| Variable | Default | Purpose |
|---|---|---|
| `EINVOICE_MAX_UPLOAD_SIZE_MB` | `10` | Upload limit (HTTP 413 above it) |
| `EINVOICE_ARCHIVE_DIR` | `./storage/einvoice_archive` | Tamper-evident archive. Keep it outside `STORAGE_DIR`, which has retention cleanup |
| `EINVOICE_ARCHIVE_ENABLED` | `true` | Turns archiving on or off |
| `EINVOICE_XRECHNUNG_VERSION` | `3.0` | Version written into the XRechnung URN |
| `EINVOICE_JAVA_BIN` | `java` | Java 11+ for KoSIT |
| `EINVOICE_KOSIT_JAR` | – | Path to `validator-<v>-standalone.jar` |
| `EINVOICE_KOSIT_SCENARIOS` | – | `scenarios.xml` of the XRechnung validator configuration |
| `EINVOICE_KOSIT_REPOSITORY` | – | Folder of the validator configuration |
| `EINVOICE_KOSIT_REQUIRED` | `false` | If `true`, XRechnung fails when KoSIT is missing or crashes |
| `EINVOICE_GHOSTSCRIPT_BIN` | – | If set, uploaded PDFs are converted to PDF/A-3b before embedding |

### Installing the KoSIT validator (recommended for production)

```bash
mkdir -p /opt/kosit && cd /opt/kosit
curl -LO https://github.com/itplr-kosit/validator/releases/download/v1.6.3/validator-1.6.3-standalone.jar
curl -LO https://github.com/itplr-kosit/validator-configuration-xrechnung/releases/download/v2026-08-31/xrechnung-3.0.2-validator-configuration-2026-08-31.zip
unzip xrechnung-3.0.2-validator-configuration-*.zip -d xrechnung

EINVOICE_KOSIT_JAR=/opt/kosit/validator-1.6.3-standalone.jar
EINVOICE_KOSIT_SCENARIOS=/opt/kosit/xrechnung/scenarios.xml
EINVOICE_KOSIT_REPOSITORY=/opt/kosit/xrechnung
```

The configuration also contains an **EN16931 (CII)** scenario. When KoSIT is installed, ZUGFeRD EN 16931/BASIC invoices are checked against the official schematron too. Without KoSIT, the built-in XSD and business-rule checks still run. XRechnung responses then carry a `KOSIT` warning, which becomes an error when `EINVOICE_KOSIT_REQUIRED=true`.

---

## 4. Usage

### 4.1 Create a ZUGFeRD PDF

```bash
# From your own PDF + JSON
curl -X POST http://localhost:8000/api/einvoice/create-zugferd \
  -H "X-API-Key: $KEY" \
  -F "invoice_data=<invoice_471102.json" \
  -F "file=@rechnung.pdf;type=application/pdf" \
  -o 471102_zugferd.pdf -D headers.txt

# JSON only: the server renders the visual PDF itself (design: classic | modern | minimal)
curl -X POST "http://localhost:8000/api/einvoice/create-zugferd?template=modern" \
  -H "X-API-Key: $KEY" -F "invoice_data=<invoice_471102.json" -o 471102_zugferd.pdf
```

> `template` selects the design of the rendered PDF: `classic` (default, DIN 5008 letterhead), `modern` (coloured header band)
> or `minimal` (lots of white space). It has no effect when you upload your own `file`.

> `invoice_data` is a text form field, and Starlette caps multipart text fields at **1 MB** (about 2,000 lines).
> - Send the contents with `-F "invoice_data=<file.json"`.
> - For bigger invoices, upload the JSON as a file part instead: `-F "invoice_file=@file.json;type=application/json"`.
> - Send exactly one of the two.

Response headers:

```
Content-Type: application/pdf
Content-Disposition: attachment; filename="471102_zugferd.pdf"
X-Validation-Valid: true
X-Validation-Warnings: 0
X-Einvoice-Profile: EN 16931 (COMFORT)
X-Content-SHA256: 430b9b99…
X-Archive-Id: ea_20261024T155024Z_a7df191907c84c8c
```

Add `?output=json` to get the full validation report together with the PDF (base64) and the XML.

**Server-side steps:**
1. Validate the input schema.
2. Recalculate all amounts and reject any mismatch.
3. Generate CII XML.
4. Run the XSD and EN 16931 rules on the XML.
5. Embed the XML as `factur-x.xml` (AFRelationship=Data), adding PDF/A-3 + Factur-X XMP and the sRGB output intent.
6. Extract and validate the final PDF again.
7. Archive the input JSON, the original PDF and the generated PDF.

### 4.2 Create an XRechnung

```bash
curl -X POST http://localhost:8000/api/einvoice/create-xrechnung \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d @invoice_471102_xrechnung.json -o 471102_xrechnung.xml
```

### 4.3 Extract

```bash
curl -X POST http://localhost:8000/api/einvoice/extract -H "X-API-Key: $KEY" -F "file=@471102_zugferd.pdf"
```

```json
{
  "filename": "471102_zugferd.pdf",
  "source_type": "pdf",
  "syntax": "CII",
  "profile": "EN 16931 (COMFORT)",
  "profile_urn": "urn:cen.eu:en16931:2017",
  "version": "ZUGFeRD 2.1+ / Factur-X 1.0 (XMP version 1.0)",
  "embedded_filename": "factur-x.xml",
  "xmp": { "pdfa_part": "3", "pdfa_conformance": "B", "document_type": "INVOICE",
           "document_file_name": "factur-x.xml", "version": "1.0", "conformance_level": "EN 16931", "namespace": "factur-x" },
  "invoice": { "invoice_number": "471102", "issue_date": "2018-03-05", "seller": { "…": "…" }, "lines": [ "…" ], "totals": { "gross_total": 529.87, "…": "…" } },
  "raw_xml": "<?xml version='1.0' encoding='UTF-8'?>\n<rsm:CrossIndustryInvoice …",
  "warnings": [],
  "archive": { "archive_id": "ea_20261024T155024Z_cfeeed87810b4f43", "files": [ { "role": "uploaded_document", "sha256": "430b9b99…" } ] }
}
```

`invoice` has the same shape that the create endpoints accept, so you can send an extracted invoice straight back to them.

Supported inputs:
- ZUGFeRD 2.0, 2.1+ / Factur-X PDFs (`factur-x.xml`, `zugferd-invoice.xml`, `xrechnung.xml`).
- CII XML.
- XRechnung UBL XML, including XRechnung 1.x/2.x/3.x and the XRechnung extension.
- Peppol BIS 3.0, and any other EN 16931 CIUS (`urn:cen.eu:en16931:2017#compliant#…`).

MINIMUM and BASIC WL files are extracted on a best-effort basis but reported as not EN 16931. ZUGFeRD 1.0 is detected, but its content is not mapped. `raw_xml` is decoded using the encoding declared in the XML, e.g. ISO-8859-1.

### 4.4 Validate

```bash
curl -X POST http://localhost:8000/api/einvoice/validate -H "X-API-Key: $KEY" -F "file=@invoice.xml"
```

Always HTTP 200:

```json
{
  "valid": false,
  "syntax": "CII",
  "profile": "XRECHNUNG",
  "profile_urn": "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
  "version": "XRechnung 3.0 (CII)",
  "errors": [
    { "rule_id": "BR-CO-15", "field": "totals.gross_total", "severity": "error", "source": "business-rules",
      "message": "Total with VAT (BT-112) = total without VAT + total VAT: expected 529.87, got 600.00",
      "expected": "529.87", "actual": "600.00", "line": null },
    { "rule_id": "BR-DE-15", "field": "/rsm:CrossIndustryInvoice[1]/…", "severity": "error", "source": "kosit",
      "message": "[BR-DE-15] Das Element \"Buyer reference\" (BT-10) muss übermittelt werden.", "line": 3 }
  ],
  "warnings": [],
  "checks": { "xml": "passed", "xsd": "passed", "business_rules": "failed", "kosit": "failed" },
  "validated_at": "2026-10-24T15:50:24Z"
}
```

Check pipeline:
1. **xml**: well-formedness. DOCTYPE and entities are rejected, which blocks XXE and entity bombs.
2. **xsd**: Factur-X XSD for the detected profile, or the UBL 2.1 XSD.
3. **business_rules**: EN 16931 rules, including recalculation of amounts, plus BR-DE rules for XRechnung.
4. **kosit**: the official schematron (XRechnung, EN 16931 CII).
5. **pdf**: the XML attachment is present, PDF/A-3 is declared in XMP, and the XMP profile matches the XML.

### 4.5 Convert (flat form, for the upload UI)

```bash
curl -X POST http://localhost:8000/api/v1/convert -H "X-API-Key: $KEY" \
  -F "file=@rechnung.pdf;type=application/pdf" \
  -F 'invoice_data={"invoice_number":"471102","issue_date":"2018-03-05","format_type":"ZUGFeRD",
       "buyer_name":"Kunden AG Mitte","buyer_address":"Kundenstraße 15, 69876 Frankfurt, DE",
       "seller_name":"Lieferant GmbH","seller_address":"Lieferantenstraße 20, 80333 München, DE",
       "seller_vat_id":"DE123456789",
       "line_items":[{"item_description":"Trennblätter A4","net_price":9.90,"quantity":20,"unit":"Stk","tax_rate":19,"line_net_amount":198.00},
                     {"item_description":"Joghurt Banane","net_price":5.50,"quantity":50,"unit":"Stk","tax_rate":7,"line_net_amount":275.00}],
       "tax_basis_total":473.00,"tax_total":56.87,"grand_total":529.87,
       "payment_terms":"Zahlbar innerhalb 30 Tagen netto","delivery_date":"2018-03-05"}' \
  -o 471102_zugferd.pdf
```

Addresses use the format `"Street No, Postcode City, Country"`. The country defaults to DE.

For `format_type: "XRechnung"` you must send `routing_id` (Leitweg-ID). You should also send `seller_contact_name`, `seller_phone`, `seller_email`, `buyer_email`, `iban` and `due_date`; without them the XRechnung rules reject the invoice. Error fields are reported under the flat names, for example `invoice_data.tax_total`.

---

## 5. Invoice JSON (`EInvoiceData`)

Amounts are JSON numbers. Internally they are exact decimals with 2 places for amounts, up to 6 for prices and 4 for quantities.

**Document**

| Field | BT | Required | Notes |
|---|---|---|---|
| `invoice_number` | BT-1 | ✔ | |
| `issue_date` | BT-2 | ✔ | `YYYY-MM-DD` |
| `type_code` | BT-3 | | Any EN 16931 code (BR-CL-01): `380` invoice (default), `381` credit note, `383` debit note, `384` corrected, `389` self-billed, `326`, `875–877` … XRechnung allows only 326/380/381/384/389/875/876/877 (BR-DE-17) |
| `currency` | BT-5 | | ISO 4217, default `EUR` |
| `delivery_date` | BT-72 | (UStG) | Date of supply. A warning is raised if neither this nor a billing period is given |
| `delivery_name`, `delivery_address` | BT-70, BG-15 | (K) | Deliver-to party. **Required for intra-community supplies (BR-IC-12)**; XRechnung needs city and post code here (BR-DE-10/11) |
| `billing_period_start/_end` | BT-73/74 | | |
| `order_reference`, `contract_reference` | BT-13, BT-12 | | |
| `preceding_invoice_number/_date` | BT-25/26 | | Recommended for 381/384 |
| `notes[]` | BT-22 | | e.g. "Rechnung gemäß Bestellung vom 01.03.2018." |

**`seller`** (BG-4): `id` (BT-29), `global_id` (GLN, scheme 0088, checksum-validated), `name` ✔, `trading_name`, `address` ✔ (`country` ISO-2 is required by the schema; `street`, `postcode` and `city` are enforced by the rules: an **error** for German addresses (UStG §14) and for XRechnung (BR-DE-3/4/8/9), a warning for foreign addresses. Optional: `additional_street`, `address_line_3`, `country_subdivision`), `tax_number` (BT-32, Steuernummer), `vat_id` (BT-31, `DE` + 9 digits), `electronic_address` (BT-34) + `electronic_address_scheme` (auto: `EM` for an email, `0204` for a Leitweg-ID), `contact` {`name`, `phone`, `email`}, `legal` {`managing_director`, `commercial_register` (BT-30), `register_court`, `additional_info`}. The legal fields are written to BT-33 and printed on the PDF.

**`buyer`** (BG-7): `id`, `global_id`, `name` ✔, `address` ✔, `vat_id` (BT-48), `legal_registration_id` (BT-47), `buyer_reference` (BT-10, **the Leitweg-ID, mandatory for XRechnung**, check digits verified), `electronic_address` (BT-49), `contact`.

**`lines[]`** (BG-25, at least 1)

| Field | BT | Notes |
|---|---|---|
| `position` | BT-126 | Auto-numbered when omitted |
| `description` ✔ | BT-153 | Item name |
| `item_description` | BT-154 | |
| `seller_article_number` / `buyer_article_number` | BT-155 / BT-156 | |
| `gtin` | BT-157 | Scheme 0160, checksum-validated |
| `net_price` ✔ | BT-146 | ≥ 0 |
| `gross_price` | BT-148 | ≥ net. The difference becomes a price discount |
| `allowances[]` / `charges[]` | BG-27 / BG-28 | Line discounts and surcharges: `amount`, `reason` or `reason_code`, optional `base_amount` + `percent` (amount must equal base × percent; PEPPOL-EN16931-R040) |
| `price_base_quantity` | BT-149 | Default 1 |
| `quantity` ✔ | BT-129 | ≠ 0 |
| `unit` | BT-130 | UN/ECE code or alias: `Stk`→`C62`, `Std`→`HUR`, `kg`→`KGM`, `Tag`→`DAY`, `pauschal`→`LS`, … Upper-case UN/ECE codes (`ST`, `PT`, `EA`, `H87` …) are kept unchanged |
| `vat_category` | BT-151 | `S` (default), `Z`, `E`, `AE`, `K`, `G`, `O`, `L`, `M` |
| `vat_rate` ✔ | BT-152 | 19 / 7. Must be > 0 for S and 0 for Z/E/AE/K/G/O |
| `line_net_amount` | BT-131 | Optional. If sent, it must equal quantity × net price ÷ base quantity − line allowances + line charges |

Amounts with binary-float noise, such as `29.700000000000003` from a JavaScript `9.9 * 3`, are snapped to the intended cent. Prices and quantities accept up to 10 decimals.

**`allowances[]` / `charges[]`** (BG-20/21): `amount`, `reason` or `reason_code`, `vat_category`, `vat_rate`, optional `base_amount`, `percent`.

**`vat_breakdown[]`** (BG-23), optional and calculated per (category, rate): `category`, `rate`, `basis_amount`, `tax_amount`, plus `exemption_reason`/`_code`. Those two are **required** for E, AE, K, G and O.

**`totals`** (BG-22), all optional and all recalculated: `line_total`, `allowances`, `charges`, `net_total`, `vat_total`, `gross_total`, `prepaid_amount`, `rounding_amount`, `payable_amount`.

**`payment`**: `means_code` (BT-81, any UNTDID 4461 code. Default: `58` SEPA transfer if `iban` is set, `59` SEPA direct debit if `debtor_iban` is set, otherwise `1`. Codes 30/58 need an IBAN, BR-61), `due_date` (BT-9), `terms` (BT-20), `discount_days` + `discount_percent`, `iban` (BT-84, checksum-validated), `bic`, `account_name`, `payment_reference` (BT-83), and for direct debit: `mandate_reference`, `creditor_id`, `debtor_iban`. The discount is written as the XRechnung structure `#SKONTO#TAGE=10#PROZENT=3.00#` (BR-DE-18).

The full sample 471102 is in [app/schemas/einvoice_examples.py](app/schemas/einvoice_examples.py) and is pre-filled in `/docs`.

---

## 6. Calculation rules (server-side)

The server never trusts sent amounts. It recalculates them and rejects mismatches with HTTP 422.

```
line_net        = round(quantity × net_price ÷ base_quantity, 2)
                  − line allowances + line charges                            CALC-LINE-NET
basis[cat,rate] = Σ line_net + Σ charges − Σ allowances  (per category/rate)  BR-S-08 …
tax[cat,rate]   = round(basis × rate ÷ 100, 2)                                BR-S-09 … (±0.01 → warning)
line_total      = Σ line_net                                                  BR-CO-10
net_total       = line_total − allowances + charges                           BR-CO-13
vat_total       = Σ tax                                                       BR-CO-14
gross_total     = net_total + vat_total                                       BR-CO-15
payable         = gross_total − prepaid + rounding                            BR-CO-16
```

Sample 471102: `198.00 + 275.00 = 473.00` net; `37.62 (19 %) + 19.25 (7 %) = 56.87` VAT; `529.87` gross.

Other enforced rules:
- **EN 16931 presence rules:** BR-02/03/05/06/07/16/22/25/26.
- **EN 16931 value and code-list rules:** BR-27/28, BR-CL-01/04/14/16/23.
- **EN 16931 identification and payment:** BR-CO-9 (VAT ID prefix), BR-CO-25 (due date or terms), BR-CO-26 (seller identifiable), BR-61 (credit transfer needs an IBAN).
- **EN 16931 VAT categories:** BR-S/Z/E/AE/IC/G-02 (VAT ID requirements), BR-O-02, BR-x-05/06/07 (category vs. rate), BR-x-10 (exemption reasons), BR-IC-11/12.
- **XRechnung:** BR-DE-1 (payment instructions), BR-DE-2/5/6/7 (seller contact), BR-DE-3/4/8/9/10/11 (city/post code), BR-DE-14 (rate on category O), BR-DE-15 (Leitweg-ID), BR-DE-16, BR-DE-17 (type codes), BR-DE-18 (Skonto), BR-DE-19 (IBAN), BR-DE-30/31, PEPPOL-EN16931-R010/R020 (electronic addresses), PEPPOL-EN16931-R040 (allowance percentages).
- **German law:** UStG §14 checks on the date of supply and on complete German addresses.

---

## 7. Error format

Field-level errors return **HTTP 422** in the API's standard error envelope:

```json
{
  "id": "unknown",
  "status": "error",
  "status_code": 422,
  "error": {
    "message": "Invoice violates EN 16931 / XRechnung business rules",
    "errors": [
      { "rule_id": "BR-CO-13", "field": "totals.net_total", "severity": "error", "source": "business-rules",
        "message": "Total without VAT (BT-109) = line total − allowances + charges: expected 473.00, got 470.00",
        "expected": "473.00", "actual": "470.00", "line": null }
    ]
  }
}
```

- Schema errors in `invoice_data` (multipart) use paths like `invoice_data.payment.iban` and carry a rule ID where one applies, for example `BR-09` for a bad country code.
- For the JSON-body endpoint (`create-xrechnung`), schema errors use FastAPI's standard `details[]` (`loc`, `msg`). Business-rule errors use the format above.
- Other codes:
  - `401`: missing or invalid API key.
  - `413`: file over 10 MB. Requests whose `Content-Length` exceeds 2 × limit + 1 MB are rejected before the body is parsed.
  - `415`: the PDF/XML signature, MIME type or extension doesn't match.
  - `503`: archive not writable. In that case nothing is issued.

---

## 8. Tamper-evident archive (GoBD)

Every create, extract and convert call (and `validate?archive=true`) stores what it received and produced, **byte-for-byte**:

```
storage/einvoice_archive/
├── ledger.jsonl                                   # one hash-chained entry per record
└── 2026/10/24/ea_20261024T155024Z_a7df…/
    ├── 00_input_json__invoice_data.json           # mode 0444, created with O_EXCL
    ├── 01_original_pdf__original.pdf
    ├── 02_generated_pdf__471102_zugferd.pdf
    └── manifest.json                              # SHA-256 per file, UTC timestamp, owner, ledger hashes
```

- Each ledger entry stores `prev_hash`, `owner_id` and `entry_hash = SHA-256(entry)`.
- `GET /api/einvoice/archive/{id}` checks the record and the ledger. Only the API key that created the record can read it. It:
  - recomputes the record's file and manifest hashes;
  - walks the **whole** ledger chain;
  - reports a deleted record directory as `intact: false`.

  It returns `intact: true/false` with a list of problems.
- Modifying, deleting, inserting or reordering a record or ledger entry is detected.
- **Truncating the newest ledger entries is not detectable on its own.** Keep a copy of the latest `entry_hash` elsewhere, for example in a daily export or with a timestamping service.
- A damaged ledger makes create calls fail with **503**, and nothing is issued.
- There is **no automatic deletion**. German retention for invoices is 8 years (§147 AO).
- For legally robust immutability, put `EINVOICE_ARCHIVE_DIR` on WORM storage, for example S3 Object Lock in compliance mode or a NetApp SnapLock volume. The module supplies the integrity evidence; the storage layer has to prevent deletion.

---

## 9. Tests

```bash
pytest tests/test_einvoice.py -q          # 60 pass; the real-KoSIT test is skipped by default

# Also run against the real KoSIT validator:
KOSIT_TEST_JAR=/opt/kosit/validator-1.6.3-standalone.jar \
KOSIT_TEST_SCENARIOS=/opt/kosit/xrechnung/scenarios.xml \
KOSIT_TEST_REPOSITORY=/opt/kosit/xrechnung \
pytest tests/test_einvoice.py -q          # 61 pass
```

Coverage:
- Totals for sample 471102.
- Mismatch rejection with rule IDs, and field-level 422s.
- Size, type and MIME limits; auth.
- ZUGFeRD with and without an uploaded PDF.
- XRechnung, including Leitweg-ID and Skonto.
- Extract → create round trip.
- Validation of tampered totals, XSD errors, MINIMUM profile and XXE.
- The flat `/convert` on both paths.
- Archive tamper detection and the ledger chain.
- KoSIT report parsing.

Verified on 2026-10-24 with KoSIT validator 1.6.3 and XRechnung configuration 3.0.2 (2026-08-31):
- Generated ZUGFeRD and XRechnung XML is **accepted** for all of these variants:
  - credit note, debit note
  - document and line allowances/charges
  - SEPA direct debit, payment code 1 without an IBAN
  - reverse charge, also with only the buyer's legal registration ID
  - intra-EU supply, exempt, not subject to VAT
  - prepaid amount, price discount, price base quantity, billing period
  - hours as the unit, negative lines, float-noise amounts, foreign address without a street
- Broken documents are rejected with the expected BR-DE rules.

---

## 10. Limitations

- **PDF/A-3 conformance of uploaded PDFs** depends on the source file. Fonts must be embedded and transparency avoided. factur-x adds XMP, the output intent and the attachment, but it does not rewrite page content. Set `EINVOICE_GHOSTSCRIPT_BIN` to normalise uploads, and check the output with [veraPDF](https://verapdf.org) if strict PDF/A matters. The response then includes a `PDFA-SOURCE` warning. PDFs that the server renders itself embed their fonts.
- **XRechnung output is CII only.** UBL XRechnung can be extracted and validated, but not generated.
- **Card payments** (BG-18) and the EXTENDED profile are not supported for creation.
- **Amounts in JSON responses are numbers (floats).** They are exact up to about 15 significant digits, roughly 9 trillion with cents.
- **The upload limit relies on `Content-Length`.** Chunked requests without that header are only caught by the per-file 10 MB check, after Starlette has buffered the part. For a hard limit, also set one at the reverse proxy (e.g. nginx `client_max_body_size 21m`).
- **0 % VAT lines** (E/AE/K/G/O) need `vat_category` and an exemption reason via the full schema; the flat `/convert` form rejects them.
