# Bank Reconciliation API

Compare a **bank statement** with a **business ledger** (cash book / bank book) and get back matched transactions, the gaps on each side, possible duplicates, amount and direction mismatches, and balance checks.

The flow uses one ID throughout:

```
POST /upload  ──►  202 { reconciliation_id }  ──►  poll GET /{id}  ──►  status: completed
                                                        │
                                                        ├─► GET /{id}/transactions   (paginated details)
                                                        └─► GET /{id}/export         (XLSX / CSV)
```

Base path: `/api/v1/reconciliation`

| # | Method | Path | Purpose |
|---|---|---|---|
| 1 | `POST` | `/upload` | Upload both documents and start a reconciliation |
| 2 | `GET` | `/{reconciliation_id}` | Status, progress and the full saved report |
| 3 | `GET` | `/{reconciliation_id}/transactions` | Paginated transaction details by category |
| 4 | `GET` | `/{reconciliation_id}/export` | Download the report as `xlsx` or `csv` |

There are no other reconciliation endpoints. Webhooks are outgoing calls to your URL (see [Webhooks](#webhooks)).

---

## Contents

1. [Authentication: users and guests](#authentication-users-and-guests)
2. [Supported files and limits](#supported-files-and-limits)
3. [API 1: Upload](#api-1--post-upload)
4. [API 2: Status and report](#api-2--get-reconciliation_id)
5. [API 3: Transactions](#api-3--get-reconciliation_idtransactions)
6. [API 4: Export](#api-4--get-reconciliation_idexport)
7. [Report reference](#report-reference)
   - [Currencies and number formats](#currencies-and-number-formats)
8. [How matching works](#how-matching-works)
9. [Webhooks](#webhooks)
10. [Errors](#errors)
11. [Frontend integration example](#frontend-integration-example)
12. [Data storage and retention](#data-storage-and-retention)

---

## Authentication: users and guests

The same four endpoints serve both kinds of caller.

| Caller | How to call | Who can read the result |
|---|---|---|
| **Authenticated** | Send `X-API-Key: <key>` or `Authorization: Bearer <key>` | Only callers using the **same API key** |
| **Guest** | Send no API key | Only callers presenting the **guest access token** |

**Guest access token**
- The upload response returns `access_token` **once**. Store it on the client, for example in `sessionStorage` or tied to the guest session.
- Send it on every later call as the header `X-Reconciliation-Token: <access_token>`.
- The server stores only a SHA-256 hash, so a lost token cannot be recovered.
- Guest access expires after `STORAGE_RETENTION_DAYS` (default 7 days). The upload response includes `access_expires_at`.

**Rules**
- If you send an API key, it must be valid, or the call fails with `401`. A missing key means guest mode, not an error.
- An unknown ID, a wrong token, a token used on an authenticated record, or a different API key all return the same `404`. Reconciliation IDs cannot be probed.
- Never put API keys in browser JavaScript. Browsers should either call as guests or go through your own backend, which adds the API key.
- An authenticated caller may send `user_email` on upload to tag the record, which keeps it consistent with the other upload endpoints. Access is still scoped by API key.

---

## Supported files and limits

| Format | Extensions | How it is read |
|---|---|---|
| PDF (digital or scanned) | `.pdf` | Existing OCR pipeline, as a background job |
| Images | `.jpg` `.jpeg` `.png` `.webp` `.tiff` `.tif` | Existing OCR pipeline, as a background job |
| Spreadsheets | `.csv` `.tsv` `.txt` `.xlsx` `.xlsm` `.xls` | Parsed directly (no OCR) |

The file type is detected from the file's bytes, not its name: a PDF or image saved as `.csv` goes through OCR,
an `.xlsx` saved as `.csv` is read as a workbook, and UTF-16 "Unicode text" exports plus the HTML / XML
"Excel" files many banks produce are read as spreadsheets. Every sheet is searched for the transaction header
(including headers split over two rows); if no header is recognised by name, DeepSeek maps the columns and the
values are still parsed deterministically. Scanned pages use PaddleOCR, falling back to the bundled RapidOCR models.

If a PDF/image yields no transactions, DeepSeek checks what the document is: statements and ledgers get a second,
segmented extraction pass; an invoice or receipt uploaded as the **business ledger** is reconciled as one entry for
its printed total (with a warning); anything else fails with a specific reason after the usual
`Unable to extract transactions from the <bank statement|business ledger>: ` prefix.

| Limit | Default | Setting |
|---|---|---|
| Max file size (each file) | 100 MB | `MAX_UPLOAD_SIZE_MB` |
| Max PDF pages (each file) | 200 | `MAX_PDF_PAGES` |
| Max spreadsheet rows | 100,000 | constant in `reconciliation_engine.py` |
| Rate limit | 60 requests/min per API key, or per IP for guests | `RATE_LIMIT_REQUESTS_PER_MINUTE` |

**Not supported:**
- Legacy `.xls` files.
- Files whose contents don't match their extension (signatures are checked).

**Spreadsheet layout.** The parser looks in the first 30 rows for a header row with a **date** column and at least one **amount** column. Headers are matched case- and accent-insensitively. Recognized headers include:

| Field | Example headers |
|---|---|
| Date | `Date`, `Txn Date`, `Transaction Date`, `Posting Date`, `Value Date` (lowest priority) |
| Description | `Description`, `Narration`, `Particulars`, `Details`, `Remarks`, `Memo` |
| Reference | `Reference`, `Ref No`, `Chq/Ref No`, `Cheque No`, `UTR`, `Voucher No`, `Transaction ID` |
| Debit | `Debit`, `Withdrawal`, `Withdrawal Amt`, `Dr`, `Paid Out`, `Payments` |
| Credit | `Credit`, `Deposit`, `Deposit Amt`, `Cr`, `Paid In`, `Receipts` |
| Signed amount | `Amount` (negative = money out), optionally with a `Type` / `Dr/Cr` column |
| Balance | `Balance`, `Running Balance`, `Closing Balance` |

Common German, French, Spanish, Portuguese and Dutch bank-export headers are also recognized, for example `Buchungstag`, `Verwendungszweck`, `Betrag`, `Soll` / `Haben`, `Saldo`, `Libellé`, `Montant`, `Débit` / `Crédit`, `Solde`, `Fecha`, `Concepto`, `Importe`, `Cargo` / `Abono`, `Descrição`, `Valor`, `Omschrijving`, `Bedrag`, `Af` / `Bij`.

- Rows whose description starts with `Opening Balance`, `Balance B/F` or `Brought Forward` set the opening balance.
- Rows starting with `Closing Balance`, `Balance C/F` or `Carried Forward` set the closing balance.
- Amounts may include currency symbols or codes, parentheses or a trailing minus for negatives, and `Dr` / `Cr` suffixes. See [Currencies and number formats](#currencies-and-number-formats).

---

## API 1 · `POST /upload`

Uploads both documents, queues extraction, and returns **immediately**. Every submission gets the same response, whether it's a 2-row CSV or a 200-page scanned PDF.

**Content-Type:** `multipart/form-data`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `bank_statement` | file | ✅ | — | Bank statement |
| `business_ledger` | file | ✅ | — | Business ledger / cash book |
| `webhook_url` | string | | — | HTTPS URL for `reconciliation.completed` / `reconciliation.failed` |
| `webhook_secret` | string (≤256) | | — | Secret used to sign webhook payloads (HMAC-SHA256) |
| `date_tolerance_days` | integer 0–31 | | `2` | Maximum days between matching bank and ledger dates |
| `description_matching` | boolean | | `true` | Use description similarity when scoring matches |
| `user_email` | string | | — | Authenticated callers only; ignored for guests |
| `bank_statement_password` | string (≤256) | | — | Password for a password-protected bank statement PDF |
| `business_ledger_password` | string (≤256) | | — | Password for a password-protected business ledger PDF |

**Password-protected PDFs.** Without a password the upload returns `400` with `<Label>: This PDF is password-protected. Provide its password to continue.`; a wrong password returns `<Label>: Incorrect PDF password.` (`<Label>` is `Bank statement` or `Business ledger`). The PDF is unlocked in memory and only the unencrypted copy is stored; the password is never saved or logged.

### Response: `202 Accepted`

Guest:

```json
{
  "success": true,
  "reconciliation_id": "rec_4f1c2a9be03d7781",
  "status": "queued",
  "message": "Documents uploaded. Reconciliation has been queued.",
  "status_url": "/api/v1/reconciliation/rec_4f1c2a9be03d7781",
  "access_token": "Xq3...43 url-safe characters...",
  "access_expires_at": "2026-10-22T06:00:00Z"
}
```

Authenticated callers get the same body **without** `access_token` and `access_expires_at`.

### Examples

```bash
# Guest
curl -X POST http://localhost:8000/api/v1/reconciliation/upload \
  -F "bank_statement=@statement.pdf" \
  -F "business_ledger=@ledger.xlsx" \
  -F "date_tolerance_days=2" \
  -F "description_matching=true"

# Authenticated, with webhook
curl -X POST http://localhost:8000/api/v1/reconciliation/upload \
  -H "X-API-Key: $API_KEY" \
  -F "bank_statement=@statement.pdf" \
  -F "business_ledger=@ledger.csv" \
  -F "webhook_url=https://example.com/hooks/reconciliation" \
  -F "webhook_secret=$WEBHOOK_SECRET"
```

---

## API 2 · `GET /{reconciliation_id}`

The main endpoint for the frontend. It returns the **same structure** in every state. It reads the saved record from MongoDB and never reruns OCR or matching.

```bash
curl http://localhost:8000/api/v1/reconciliation/rec_4f1c2a9be03d7781 \
  -H "X-Reconciliation-Token: $TOKEN"        # or -H "X-API-Key: $API_KEY"
```

| Field | Type | Notes |
|---|---|---|
| `success` | bool | `false` only when `status` is `failed` |
| `reconciliation_id` | string | |
| `status` | `queued` \| `processing` \| `completed` \| `failed` | |
| `progress` | int 0–100 | Extraction covers 5–85, matching is 90, done is 100 |
| `message` | string | Human-readable stage, e.g. `Extracting business ledger transactions` |
| `result` | object \| null | Full report, only when `completed` ([reference](#report-reference)) |
| `error` | string \| null | Only when `failed` |
| `created_at` | ISO datetime | |
| `completed_at` | ISO datetime \| null | Set when `completed` or `failed` |

**Processing:**

```json
{
  "success": true,
  "reconciliation_id": "rec_4f1c2a9be03d7781",
  "status": "processing",
  "progress": 45,
  "message": "Extracting business ledger transactions",
  "result": null,
  "error": null,
  "created_at": "2026-10-15T06:00:00Z",
  "completed_at": null
}
```

**Completed** (arrays shortened; see the [report reference](#report-reference) for every field):

```json
{
  "success": true,
  "reconciliation_id": "rec_4f1c2a9be03d7781",
  "status": "completed",
  "progress": 100,
  "message": "Reconciliation completed successfully.",
  "result": {
    "summary": {
      "currency": "INR",
      "amount_decimal_places": 2,
      "bank_transactions": 150,
      "ledger_transactions": 148,
      "matched": 145,
      "unmatched_bank": 5,
      "unmatched_ledger": 3,
      "possible_duplicates": 2,
      "amount_mismatches": 1,
      "amount_corrections": 0,
      "ledger_direction_convention": "bank",
      "date_tolerance_days": 2,
      "description_matching": true
    },
    "balance_check": {
      "bank_opening_balance": "10000.00",
      "bank_closing_balance": "25000.00",
      "calculated_closing_balance": "25000.00",
      "difference": "0.00",
      "status": "passed",
      "opening_balance_source": "document",
      "closing_balance_source": "document",
      "running_balance": { "status": "passed", "checked": 150, "breaks_count": 0, "breaks": [] },
      "ledger": { "...": "same shape for the ledger" },
      "book_to_bank": {
        "explained_by_discrepancies": "-700.00",
        "closing_balance_difference": "-700.00",
        "opening_balance_difference": "0.00",
        "unexplained_difference": "0.00",
        "status": "passed"
      }
    },
    "matched_transactions": [ "..." ],
    "unmatched_bank_transactions": [ "..." ],
    "unmatched_ledger_transactions": [ "..." ],
    "possible_duplicates": [ "..." ],
    "amount_mismatches": [ "..." ],
    "amount_corrections": [],
    "warnings": [ "..." ]
  },
  "error": null,
  "created_at": "2026-10-15T06:00:00Z",
  "completed_at": "2026-10-15T06:00:30Z"
}
```

**Failed** (`result` is always `null`; nothing is made up):

```json
{
  "success": false,
  "reconciliation_id": "rec_4f1c2a9be03d7781",
  "status": "failed",
  "progress": 90,
  "message": "Reconciliation failed.",
  "result": null,
  "error": "Unable to extract transactions from the business ledger.",
  "created_at": "2026-10-15T06:00:00Z",
  "completed_at": "2026-10-15T06:01:10Z"
}
```

Possible `error` values:

| Error | Meaning |
|---|---|
| `Unable to extract transactions from the bank statement.` / `... business ledger.` | OCR failed, or no transactions were found in that document |
| `Unable to extract transactions from the business ledger: Could not find a header row ...` | Spreadsheet header not recognized |
| `Bank statement currency (INR) differs from business ledger currency (USD); cross-currency reconciliation is not supported.` | Currency mismatch |
| `Unable to extract transactions from the business ledger: The file mixes several currencies (CAD, USD); upload one currency per document.` | One spreadsheet contains more than one currency |
| `The reconciliation report is too large to store. ...` | Report exceeds MongoDB's 16 MB document limit |
| `Reconciliation failed due to an internal processing error.` | Unexpected error (details are logged on the server only) |

**Polling advice:** poll every 2–3 s for small files and every 5–10 s for large PDFs. Stop at `completed` or `failed`. A 200-page PDF can take several minutes.

---

## API 3 · `GET /{reconciliation_id}/transactions`

Pages through the saved report without downloading all of it.

| Query | Values | Default |
|---|---|---|
| `status` | `matched`, `unmatched_bank`, `unmatched_ledger`, `possible_duplicates`, `amount_mismatches` | all categories |
| `page` | ≥ 1 | `1` |
| `page_size` | 1–500 | `50` |

- Without `status`, categories are returned in the order listed above, and every item has a `category` field.
- Invalid values return `422`.
- The endpoint returns `409` until the reconciliation is `completed`.

```bash
curl "http://localhost:8000/api/v1/reconciliation/rec_4f1c2a9be03d7781/transactions?status=unmatched_bank&page=1&page_size=50" \
  -H "X-API-Key: $API_KEY"
```

```json
{
  "success": true,
  "reconciliation_id": "rec_4f1c2a9be03d7781",
  "status": "unmatched_bank",
  "total": 5,
  "page": 1,
  "page_size": 50,
  "transactions": [
    {
      "category": "unmatched_bank",
      "id": "B3",
      "source": "bank",
      "row": 3,
      "date": "2026-10-05",
      "original_date": "05/10/2026",
      "description": "Bank charges",
      "reference": null,
      "debit": "50.00",
      "credit": null,
      "amount": "50.00",
      "flow": "out",
      "balance": "11950.00",
      "reason": "No ledger entry with the same amount and direction was found."
    }
  ]
}
```

`total` is the number of items across all pages. For `possible_duplicates` it counts duplicate **groups**.

---

## API 4 · `GET /{reconciliation_id}/export`

| Query | Values | Default |
|---|---|---|
| `format` | `xlsx`, `csv` | `xlsx` |

The report is built from the saved result; nothing is reprocessed. The endpoint returns `409` until the reconciliation is `completed`.

```bash
curl -OJ "http://localhost:8000/api/v1/reconciliation/rec_4f1c2a9be03d7781/export?format=xlsx" \
  -H "X-Reconciliation-Token: $TOKEN"
```

**XLSX** sheets:

| Sheet | Contents |
|---|---|
| Summary | ID, filenames, timestamps, counts, options, ledger convention |
| Balance Verification | Opening, closing and calculated balances for both documents, running-balance status, book-to-bank check, every running-balance break |
| Matched | Bank and ledger side by side, amount, direction, date difference, match basis, confidence |
| Unmatched Bank | Each unmatched bank transaction and the reason |
| Unmatched Ledger | Each unmatched ledger transaction and the reason |
| Possible Duplicates | Source, transaction IDs, date, amount, count, reason |
| Amount Mismatches | Type, both sides, both signed amounts, difference, reason |
| Amount Corrections | OCR amounts corrected from the printed text: source, transaction, field, extracted, corrected |
| Warnings | All report warnings |

**CSV** is a single file with the same sections, each starting with a `## <Section>` row.

Response headers: `Content-Disposition: attachment; filename="<reconciliation_id>.<ext>"` and `Cache-Control: no-store`.

**Safety:** text cells beginning with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'` to block spreadsheet formula injection. Control characters from OCR text are removed.

> Browser downloads: the token must go in a header, so download with `fetch()` and save a Blob instead of using a plain `<a href>` (see the [frontend example](#frontend-integration-example)).

---

## Report reference

All money values are **strings** rounded to the currency's ISO 4217 decimal places (`summary.amount_decimal_places`), for example `"5000.00"` for INR/GBP/CAD, `"120000"` for JPY, `"1.234"` for KWD. They are calculated with `Decimal`, never floats. Parse them with a decimal library if you need to do arithmetic.

### Currencies and number formats

Any ISO 4217 currency works. Both documents must be in the **same** currency; there is no conversion.

**How the currency is identified** (in this order):
1. The `currency` field extracted by OCR from a PDF or image statement.
2. For CSV/XLSX: a header like `Amount (CAD)` or `Debit £`, or a symbol or code in the amount cells (`CA$1,250`, `1.234,56 €`, `GBP 50`).

| Written as | Read as |
|---|---|
| `CAD`, `CA$`, `C$`, `CAN$` | CAD |
| `AUD`, `AU$`, `A$` | AUD |
| `GBP`, `£` | GBP |
| `EUR`, `€` | EUR |
| `USD`, `US$` | USD |
| `INR`, `₹`, `Rs`, `Rs.` | INR |
| `NZ$`, `HK$`, `S$`, `R$`, `MX$`, `NT$` | NZD, HKD, SGD, BRL, MXN, TWD |
| `₩ ₺ ₽ ₱ ₦ ฿ ₫ ₪ ₴ ₸`, `zł`, `Kč`, `Ft`, `RM` | KRW, TRY, RUB, PHP, NGN, THB, VND, ILS, UAH, KZT, PLN, CZK, HUF, MYR |
| Any 3-letter ISO 4217 code | That currency |
| `$`, `¥`, `kr` alone | **Not assumed.** Several currencies share them. A warning is added and the other document's currency is used |
| Unknown codes such as `XYZ` | Ignored, with a warning |

**When the reconciliation fails:**
- The two documents name different currencies (for example `CA$` vs `A$`).
- One spreadsheet mixes currencies (for example `CA$` and `US$` rows).

**Number formats.** The decimal separator is worked out **per document** from all of its amounts, so each file can use its own convention:

| Format | Example | Parsed |
|---|---|---|
| Dot decimal, comma grouping | `1,234.56` | 1234.56 |
| Indian grouping | `1,23,456.78` | 123456.78 |
| Comma decimal, dot grouping (EU, BR) | `1.234,56` | 1234.56 |
| Space grouping (FR, PL, Nordics) | `1 234,56` | 1234.56 |
| Apostrophe grouping (CH) | `1'234.50` | 1234.50 |
| Negatives | `-45.00`, `(45.00)`, `45.00-`, `£(45.00)` | −45.00 |

A lone value like `1.234` is ambiguous. It is read as 1234 when other amounts in the same document show a comma decimal (e.g. `10,50`), and as 1.234 otherwise.

**Decimal places** follow ISO 4217: 2 by default, 0 for JPY, KRW, VND, CLP, ISK, UGX and others, 3 for KWD, BHD, OMR, JOD, TND, LYD, IQD. XLSX exports use the matching number format.

**PDF and image statements.** These amounts come from OCR/AI extraction as numbers, so they are protected in two ways:
1. The AI extraction prompt spells out every separator convention (`1.234,56` → 1234.56, and so on).
2. Before matching, each extracted amount is **checked against the numbers printed in the OCR text**:
   - The document's convention is decided by a majority vote over all printed amounts (dates are ignored).
   - If an extracted value isn't printed anywhere but equals a wrong-separator reading of **exactly one** printed amount, it is corrected. For example, `1.23`, `1234` or `123456` extracted where `1.234,56` is printed becomes 1234.56.
   - Ambiguous cases are never guessed.
   - Every correction is listed in `amount_corrections`, counted in `summary.amount_corrections`, mentioned in `warnings`, and shown on the export's **Amount Corrections** sheet.
   - The original extracted data in the OCR job and document records is not changed.
   - If many extracted amounts can't be found in the text at all, a warning asks for a manual review.

### Transaction object

Used inside every category.

| Field | Description |
|---|---|
| `id` | `B<row>` for bank, `L<row>` for ledger (row = position in the extracted list, starting at 1) |
| `source` | `bank` or `ledger` |
| `row` | Row number in the extracted list |
| `date` | Normalized `YYYY-MM-DD`, or `null` if the date was missing or unreadable |
| `original_date` | Date exactly as extracted |
| `description`, `reference` | As extracted |
| `debit` / `credit` | Amount on the side the document recorded it (the other one is `null`) |
| `amount` | Absolute amount |
| `flow` | `in` (money into the bank account) or `out`, after applying the ledger convention |
| `balance` | Running balance shown in the document, if any (negative for an overdrawn `Dr` bank balance) |

The original extracted transactions are never changed. They remain in the OCR job and document records.

### Category items

| Category | Item shape |
|---|---|
| `matched` | `{category, bank_transaction, ledger_transaction, amount, date_difference_days, match_basis[], confidence}` |
| `unmatched_bank` / `unmatched_ledger` | transaction object + `{category, reason}` |
| `possible_duplicates` | `{category, source, count, transaction_ids[], transactions[], reason}` |
| `amount_mismatches` | `{category, type, bank_transaction, ledger_transaction, bank_amount, ledger_amount, difference, date_difference_days, reason}` |

- `match_basis` can include: `amount`, `same_date`, `date_within_N_days`, `date_unavailable`, `reference`, `description`.
- `confidence` is `high`, `medium` or `low`. It's high when the reference matches, or when the dates and descriptions agree strongly.
- Mismatch `type` is `amount_mismatch`, `direction_mismatch` or `amount_and_direction_mismatch`.
- Mismatch `bank_amount` and `ledger_amount` are **signed** (+ in / − out), and `difference = bank_amount − ledger_amount`.

### `amount_corrections`

Amounts the OCR/AI extraction misread and that were corrected from the printed text (see [Currencies and number formats](#currencies-and-number-formats)). Up to 200 are listed; `summary.amount_corrections` holds the total count.

```json
{ "source": "bank", "transaction_id": "B2", "field": "debit", "extracted": "123456.00", "corrected": "1234.56" }
```

`field` is `debit`, `credit`, `amount`, `balance`, `opening_balance` or `closing_balance`. `transaction_id` is `null` for opening and closing balances.

### `balance_check`

| Field | Description |
|---|---|
| `bank_opening_balance`, `bank_closing_balance` | Balances from the statement, or derived (see sources below) |
| `calculated_closing_balance` | Opening + Σ credits − Σ debits |
| `difference` | Closing − calculated |
| `status` | `passed`, `failed`, or `unverifiable` (opening or closing balance unavailable) |
| `opening_balance_source` | `document`, `derived_from_first_transaction_balance`, or `null` |
| `closing_balance_source` | `document`, `last_transaction_balance`, or `null` |
| `running_balance` | `{status, checked, breaks_count, breaks[]}`. Each break is `{transaction_id, date, expected_balance, stated_balance, difference}`, listing up to 100 |
| `ledger` | The same checks for the ledger |
| `book_to_bank` | Checks that `(bank closing − ledger closing) − (bank opening − ledger opening)` equals the net effect of the unmatched items and mismatches. `unexplained_difference` should be `0.00`; anything else points to missing or incorrect extracted data. `unverifiable` if any balance is missing |

Statements in reverse date order (newest first) are detected and checked oldest-first, and a warning says so.

### `warnings`

Plain-language notes, for example:
- Rows that had no usable amount
- Rows with an unreadable date
- Dates read as month-first
- Book-keeping convention detected
- Balances that were derived rather than stated
- Unknown or ambiguous currency (e.g. a bare `$`)
- Amounts using a comma as the decimal separator
- OCR amounts corrected from the printed text, or many extracted amounts not found in the text

When discrepancies exist, the warnings always include a note that discrepancies are **not, by themselves, evidence of fraud or an accounting error**.

---

## How matching works

Matching is deterministic Python with no AI. The same inputs always give the same output.

1. **Normalize.** Each row becomes a signed amount with a direction, a parsed date, a normalized reference (letters and digits only, uppercase) and description keywords.
   - Rows with zero or no amount are skipped and counted in the warnings.
   - Dates are read day-first unless the document clearly uses month-first dates (some value like `10/13/2026`).
2. **Detect the ledger convention.**
   - In "bank" convention, a ledger *debit* means money out, the same as on the bank statement.
   - Accounting books often record a bank deposit as a *debit* ("book" convention).
   - The engine tries both and switches to book convention only if it gives clearly more matches. A warning is added when it switches.
3. **Exact matching.** A bank and a ledger transaction are candidates when they have the **same amount and direction** and their dates are within `date_tolerance_days`. A missing date is allowed but lowers the score.
   - Scoring: 100 for the amount, + up to 30 for date closeness, + 50 if references match, + up to 40 for description similarity (when `description_matching` is on).
   - Pairs are assigned greedily from the highest score down. **Each transaction is matched at most once.**
4. **Mismatch detection** on what's left. A pair is reported as a mismatch (not counted as unmatched) when it is clearly linked but differs:
   - **Direction mismatch:** same amount, opposite direction, linked by reference or a similar description.
   - **Amount mismatch:** same reference, or a similar description with amounts within 20% of each other, inside the date tolerance.
5. **Unmatched.** Everything else is listed with a reason, for example the nearest same-amount entry being 6 days away, outside the ±2-day tolerance.
6. **Possible duplicates.** Within one document, entries with the same date, amount, direction, description and reference are grouped. Nothing is removed.
7. **Balances.** Opening, closing, running and book-to-bank checks, as described above.
8. **Currency.** ISO 4217 codes and unambiguous symbols are recognized, and amounts are rounded to the currency's decimal places. If the two documents state different currencies, the reconciliation fails rather than comparing amounts across currencies. See [Currencies and number formats](#currencies-and-number-formats).

---

## Webhooks

Optional. If `webhook_url` is given, the server POSTs to it once the result is final. You can always poll API 2 instead.

### Events

```json
{
  "event": "reconciliation.completed",
  "reconciliation_id": "rec_4f1c2a9be03d7781",
  "status": "completed",
  "result_url": "/api/v1/reconciliation/rec_4f1c2a9be03d7781",
  "event_id": "evt_9b1f0c2d4e5a6b7c",
  "timestamp": "2026-10-15T06:00:30.123456+00:00"
}
```

```json
{
  "event": "reconciliation.failed",
  "reconciliation_id": "rec_4f1c2a9be03d7781",
  "status": "failed",
  "error": "Unable to extract transactions from the business ledger.",
  "event_id": "evt_1a2b3c4d5e6f7a8b",
  "timestamp": "2026-10-15T06:01:10.000000+00:00"
}
```

The payload doesn't include the report. Fetch it from `result_url` using your API key or guest token.

### Headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `X-Event-Type` | `reconciliation.completed` or `reconciliation.failed` |
| `X-Event-ID` | Same as `event_id`. Use it to ignore duplicate deliveries |
| `X-Request-ID` | The `reconciliation_id` |
| `X-Webhook-Signature` | `sha256=<hex HMAC-SHA256 of the raw body>`, only when `webhook_secret` was set |

### Verifying the signature

Compute the HMAC over the **raw request body bytes**, not over re-serialized JSON.

```python
import hashlib, hmac

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

```js
import crypto from "node:crypto";

function verify(rawBody, header, secret) {
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  return header && expected.length === header.length &&
         crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(header));
}
```

### Delivery rules

- **HTTPS only.** The host must resolve to public IP addresses only. `localhost`, `.local`, `.internal`, and private, loopback, link-local (including cloud metadata `169.254.169.254`), reserved and multicast ranges are rejected at upload (`400`) and checked again before sending. URLs with embedded credentials are rejected.
- Redirects are **not** followed. Return a `2xx` directly.
- Up to **4 attempts**, waiting 1 s, 2 s, then 4 s between them. Each attempt times out after `WEBHOOK_TIMEOUT` seconds (default 15).
- Delivery never blocks the reconciliation itself. The outcome is stored on the record (`webhook.delivery_status` = `delivered` / `failed`) and logged in `webhook_deliveries`.
- The signing secret is deleted from the database after the final attempt.

---

## Errors

Errors use the API's standard envelope:

```json
{ "id": "<X-Request-ID or unknown>", "status": "error", "error": "<message>", "status_code": 404 }
```

Validation errors (`422`) use `"error": "Request validation failed"` and add a `details` array.

| Code | When | Endpoints |
|---|---|---|
| `400` | Unsupported file type, empty file, content doesn't match extension, corrupted PDF, password-protected PDF without (or with a wrong) password, too many PDF pages, invalid or private `webhook_url`, `webhook_secret` too long. File errors start with `Bank statement:` or `Business ledger:` | 1 |
| `401` | An API key was sent but is invalid | all |
| `404` | Unknown or malformed ID, or the caller isn't allowed to see it | 2, 3, 4 |
| `409` | Reconciliation not `completed` yet (or `failed`) | 3, 4 |
| `410` | Guest access expired | 2, 3, 4 |
| `413` | File larger than `MAX_UPLOAD_SIZE_MB` | 1 |
| `422` | Missing file, `date_tolerance_days` outside 0–31, invalid `status` / `page` / `page_size` / `format` | all |
| `429` | Rate limit exceeded (see the `Retry-After` header) | all |

---

## Frontend integration example

```ts
const API = "/api/v1/reconciliation";

export async function startReconciliation(bank: File, ledger: File, toleranceDays = 2) {
  const form = new FormData();
  form.append("bank_statement", bank);
  form.append("business_ledger", ledger);
  form.append("date_tolerance_days", String(toleranceDays));

  const res = await fetch(`${API}/upload`, { method: "POST", body: form });
  const body = await res.json();
  if (res.status !== 202) throw new Error(body.error ?? "Upload failed");
  if (body.access_token) {
    sessionStorage.setItem(`rec-token:${body.reconciliation_id}`, body.access_token);
  }
  return body.reconciliation_id as string;
}

function authHeaders(id: string): HeadersInit {
  const token = sessionStorage.getItem(`rec-token:${id}`);
  return token ? { "X-Reconciliation-Token": token } : {};
}

export async function pollReconciliation(id: string, onProgress: (p: number, msg: string) => void) {
  for (;;) {
    const res = await fetch(`${API}/${id}`, { headers: authHeaders(id) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error);
    onProgress(body.progress, body.message);
    if (body.status === "completed") return body.result;
    if (body.status === "failed") throw new Error(body.error);
    await new Promise((r) => setTimeout(r, 3000));
  }
}

export async function downloadReport(id: string, format: "xlsx" | "csv" = "xlsx") {
  const res = await fetch(`${API}/${id}/export?format=${format}`, { headers: authHeaders(id) });
  if (!res.ok) throw new Error((await res.json()).error);
  const url = URL.createObjectURL(await res.blob());
  Object.assign(document.createElement("a"), { href: url, download: `${id}.${format}` }).click();
  URL.revokeObjectURL(url);
}
```

---

## Data storage and retention

- **One MongoDB document per submission** in the `reconciliations` collection. It holds:
  - `reconciliation_id` (unique index)
  - `owner_id` (API-key hash) or `access_token_hash` for guests
  - Document IDs, filenames and OCR job IDs
  - Status, stage and progress
  - Options
  - The final `result`, or `error`
  - Webhook URL and delivery status
  - Timestamps
- **No OCR text or file contents** are copied into it. Extracted data stays in the existing `jobs` and `documents` records, which are referenced by ID.
- **Authenticated records** are kept until you delete them.
- **Guest records** carry `expires_at` (creation time + `STORAGE_RETENTION_DAYS`). A MongoDB TTL index removes the reconciliation and its linked OCR `jobs`, `documents` and `extractions` records. After expiry the API returns `410` until MongoDB's TTL sweep (about every 60 s) deletes the record, then `404`. Uploaded files are removed by the existing storage retention cleanup.
- Access tokens, webhook secrets and transaction amounts are never written to logs.
