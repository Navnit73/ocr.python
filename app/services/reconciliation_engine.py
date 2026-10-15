"""
Deterministic Bank Reconciliation Engine.
Normalizes bank statement and business ledger transactions, matches them one-to-one,
and reports unmatched entries, possible duplicates, amount/direction mismatches and balance checks.
All monetary arithmetic uses Decimal. No AI is involved.
"""

import csv
import io
from bisect import bisect_left, bisect_right
import os
import re
import unicodedata
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_SPREADSHEET_ROWS = 100_000
MAX_LISTED_BALANCE_BREAKS = 100

# Active ISO 4217 currency codes.
ISO_CURRENCIES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD
GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR
NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD
SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD
XCG XOF XPF YER ZAR ZMW ZWG ZWL
""".split())
# ISO 4217 minor units that differ from the default of 2.
_MINOR_UNITS = {
    **{c: 0 for c in "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX VND VUV XAF XOF XPF".split()},
    **{c: 3 for c in "BHD IQD JOD KWD LYD OMR TND".split()},
}
# Unambiguous symbols / prefixes (longest first when matching). "$", "¥", "kr" are deliberately absent:
# they are shared by several currencies, so they are treated as unknown rather than guessed.
_CURRENCY_SYMBOLS = {
    "CA$": "CAD", "C$": "CAD", "CAN$": "CAD", "AU$": "AUD", "A$": "AUD", "AUS$": "AUD", "NZ$": "NZD",
    "US$": "USD", "U$S": "USD", "HK$": "HKD", "S$": "SGD", "SG$": "SGD", "R$": "BRL", "MX$": "MXN", "NT$": "TWD",
    "£": "GBP", "€": "EUR", "₹": "INR", "RS": "INR", "RS.": "INR", "₨": "INR", "₩": "KRW", "₺": "TRY",
    "₽": "RUB", "₱": "PHP", "₦": "NGN", "฿": "THB", "₫": "VND", "₪": "ILS", "₴": "UAH", "₸": "KZT",
    "ZŁ": "PLN", "KČ": "CZK", "FT": "HUF", "RM": "MYR", "CN¥": "CNY", "JP¥": "JPY", "RMB": "CNY",
}
AMBIGUOUS_CURRENCY_SYMBOLS = frozenset({"$", "¥", "KR", "KR.", "R", "FR", "LEI"})
_SYMBOL_CHARS = re.compile(r"[$£€₹₨₩₺₽₱₦฿₫₪₴₸¥]")

# Quantum for the currency being reconciled (set by reconcile(); 0.01 by default).
_QUANTUM: ContextVar[Decimal] = ContextVar("reconciliation_quantum", default=CENT)

DISCREPANCY_NOTE = (
    "Discrepancies usually reflect timing differences, bank charges, or data-entry differences. "
    "They are not, by themselves, evidence of fraud or an accounting error."
)


class ReconciliationError(Exception):
    """User-facing reconciliation failure (safe to return in API responses and webhooks)."""


@dataclass(frozen=True)
class ReconciliationOptions:
    date_tolerance_days: int = 2
    description_matching: bool = True


@dataclass
class NormalizedTransaction:
    id: str
    source: str  # "bank" | "ledger"
    row: int
    date: Optional[date]
    original_date: Optional[str]
    amount: Decimal  # absolute value
    direction: str  # "debit" | "credit" as recorded in the source document
    reference: str  # normalized for comparison
    original_reference: Optional[str]
    description: str
    desc_key: str
    tokens: frozenset
    balance: Optional[Decimal]

    def to_dict(self, flow: str) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "row": self.row,
            "date": self.date.isoformat() if self.date else None,
            "original_date": self.original_date,
            "description": self.description or None,
            "reference": self.original_reference,
            "debit": _fmt(self.amount) if self.direction == "debit" else None,
            "credit": _fmt(self.amount) if self.direction == "credit" else None,
            "amount": _fmt(self.amount),
            "flow": flow,
            "balance": _fmt(self.balance),
        }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def minor_units(currency: Optional[str]) -> int:
    """ISO 4217 decimal places: 2 by default, 0 for JPY/KRW/..., 3 for KWD/BHD/..."""
    return _MINOR_UNITS.get(currency or "", 2)


def _fmt(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else str(value.quantize(_QUANTUM.get(), rounding=ROUND_HALF_UP))


def to_decimal(value: Any, decimal_comma: Optional[bool] = None) -> Optional[Decimal]:
    """Converts extracted numbers or strings (₹1,23,456.78, €1.234,56, (500.00), -42) into a Decimal."""
    amount, _ = parse_amount(value, decimal_comma)
    return amount


_COMMA_DECIMAL = re.compile(r"\d,\d{1,2}$|\.\d{3},\d+$")
_DOT_DECIMAL = re.compile(r"\d\.\d{1,2}$|,\d{3}\.\d+$")


def detect_decimal_comma(values: Iterable[Any]) -> Optional[bool]:
    """
    Infers a document's decimal separator from its amount strings.
    True = comma decimal (1.234,56), False = dot decimal (1,234.56), None = no clear evidence.
    """
    comma = dot = False
    for v in values:
        if not isinstance(v, str):
            continue
        core = re.sub(r"[^\d.,]", "", v.replace(" ", ""))
        comma |= bool(_COMMA_DECIMAL.search(core))
        dot |= bool(_DOT_DECIMAL.search(core))
    if comma and not dot:
        return True
    if dot and not comma:
        return False
    return None


def _resolve_separators(num: str, decimal_comma: Optional[bool]) -> Optional[str]:
    """Turns '1.234,56' / '1,234.56' / '1,23,456.78' / '1 234,56' into a plain '1234.56'."""
    has_dot, has_comma = "." in num, "," in num
    if has_dot and has_comma:
        decimal_sep = "," if num.rfind(",") > num.rfind(".") else "."
        group_sep = "." if decimal_sep == "," else ","
        num = num.replace(group_sep, "")
        return num.replace(",", ".") if decimal_sep == "," else num
    if has_comma:
        head, _, tail = num.rpartition(",")
        is_decimal = decimal_comma if decimal_comma is not None else (num.count(",") == 1 and len(tail) in (1, 2))
        if decimal_comma is True and num.count(",") > 1:
            return None  # "1,234,56" is not a valid decimal-comma amount
        return head.replace(",", "") + "." + tail if is_decimal else num.replace(",", "")
    if has_dot:
        head, _, tail = num.rpartition(".")
        if num.count(".") > 1:
            return num.replace(".", "")  # 1.234.567
        if decimal_comma is True and len(tail) == 3:
            return num.replace(".", "")  # 1.234 in a comma-decimal document
        return num
    return num


def parse_amount(value: Any, decimal_comma: Optional[bool] = None) -> Tuple[Optional[Decimal], Optional[str]]:
    """
    Returns (amount, marker) where marker is 'dr'/'cr' if the value carried a Dr/Cr suffix.
    decimal_comma: True/False forces the separator convention; None infers it from the value.
    """
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None, None
        d = Decimal(repr(value))
    else:
        s = str(value).strip()
        if not s:
            return None, None
        marker = None
        m = re.search(r"(?<![a-z])(dr|cr)\.?\s*$", s, re.IGNORECASE)
        if m:
            marker = m.group(1).lower()
            s = s[: m.start()].strip()
        s = s.replace("−", "-")  # unicode minus
        # Drop currency words/abbreviations/symbols (INR, Rs., CA$, zł) and grouping spaces/apostrophes
        s = re.sub(r"[^\W\d_]+\.?", "", s)
        s = re.sub(r"[\s'’  ]", "", s)
        s = re.sub(r"[^\d.,\-()]", "", s)
        negative = "(" in s and s.endswith(")")
        s = s.replace("(", "").replace(")", "")
        if s.endswith("-") and not s.startswith("-"):
            s = "-" + s[:-1]  # trailing minus, e.g. "500.00-"
        if s.count("-") > 1 or ("-" in s and not s.startswith("-")):
            return None, None
        cleaned = _resolve_separators(s, decimal_comma)
        if cleaned is None or cleaned in ("", "-", ".", "-."):
            return None, None
        try:
            d = Decimal(cleaned)
        except InvalidOperation:
            return None, None
        if negative:
            d = -abs(d)
        if not d.is_finite():
            return None, None
        return d.quantize(_QUANTUM.get(), rounding=ROUND_HALF_UP), marker
    if not d.is_finite():
        return None, None
    return d.quantize(_QUANTUM.get(), rounding=ROUND_HALF_UP), None


_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
    "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d-%B-%Y", "%d %b %y", "%d-%b-%y", "%d/%b/%Y",
    "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
    "%m/%d/%Y",  # month-first only as a last resort (e.g. 12/31/2026)
)


_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")


def detect_month_first(values: Iterable[Any]) -> bool:
    """True when a document's numeric dates are clearly MM/DD (some middle part > 12, no first part > 12)."""
    day_first = month_first = False
    for v in values:
        if v is None or isinstance(v, (date, datetime)):
            continue
        m = _NUMERIC_DATE.match(str(v).strip())
        if not m:
            continue
        first, second = int(m.group(1)), int(m.group(2))
        day_first |= first > 12
        month_first |= second > 12
    return month_first and not day_first


def parse_date(value: Any, month_first: bool = False) -> Optional[date]:
    """Parses common statement date formats, preferring day-first for ambiguous dates unless month_first."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = re.sub(r"\s+", " ", str(value).strip())
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}([ T].*)?$", s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            s = s[:10]
    if month_first:
        m = _NUMERIC_DATE.match(s)
        if m:
            year = int(m.group(3))
            year += 2000 if year < 100 else 0
            try:
                return date(year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_reference(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


_STOPWORDS = {"the", "to", "from", "by", "for", "and", "of", "a", "an", "in", "on", "at", "ltd", "pvt", "inc"}


def _tokens(text: str) -> frozenset:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(w for w in words if len(w) > 1 and w not in _STOPWORDS and not w.isdigit())


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _references_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 6 and shorter in longer


def normalize_currency(value: Any) -> Optional[str]:
    """Maps an ISO code or unambiguous symbol (CA$, A$, £, €, ₹, R$, zł) to an ISO 4217 code."""
    if not value:
        return None
    code = re.sub(r"\s+", "", str(value)).upper()
    if code in ISO_CURRENCIES:
        return code
    return _CURRENCY_SYMBOLS.get(code)


def detect_currency_in_text(text: Any) -> Tuple[Optional[str], bool]:
    """
    Finds a currency marker inside an amount or header string such as 'CA$1,250', '1 250,00 zł',
    'Amount (GBP)' or '$500'. Returns (iso_code, ambiguous_symbol_seen).
    """
    if not isinstance(text, str) or not text.strip():
        return None, False
    for token in re.findall(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])", text):
        if token in ISO_CURRENCIES:
            return token, False
    marker = re.sub(r"[\d\s.,'()\-+−]", "", text).upper()
    if not marker:
        return None, False
    for symbol in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if symbol in marker:
            return _CURRENCY_SYMBOLS[symbol], False
    ambiguous = marker in AMBIGUOUS_CURRENCY_SYMBOLS or bool(_SYMBOL_CHARS.search(marker))
    return None, ambiguous


# ---------------------------------------------------------------------------
# OCR amount verification
# ---------------------------------------------------------------------------

MAX_LISTED_CORRECTIONS = 200
_RAW_NUMBER = re.compile(r"\d[\d.,'\u00a0\u202f]*\d")
_FULL_DATE = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$")
_DAY_MONTH = re.compile(r"^\d{1,2}\.\d{1,2}$")
_COMMA_VOTE = re.compile(r"\d,\d{2}$|\.\d{3},\d+$")
_DOT_VOTE = re.compile(r"\d\.\d{2}$|,\d{3}\.\d+$")


def _raw_number_tokens(raw_text: str) -> List[str]:
    return [t for t in _RAW_NUMBER.findall(raw_text or "") if not _FULL_DATE.match(t)]


def _raw_text_decimal_comma(tokens: List[str]) -> Optional[bool]:
    """Majority vote over printed amounts; requires a clear winner so stray dates or IDs cannot decide it."""
    comma = dot = 0
    for t in tokens:
        if _COMMA_VOTE.search(t):
            comma += 1
        elif _DOT_VOTE.search(t) and not _DAY_MONTH.match(t):
            dot += 1
    if comma >= 3 and comma >= 4 * dot:
        return True
    if dot >= 3 and dot >= 4 * comma:
        return False
    return None


def _misreadings(token: str, decimal_comma: bool) -> Set[Decimal]:
    """Values a reader would get by applying the wrong separator convention to a printed amount."""
    quantum = _QUANTUM.get()
    dec, grp = (",", ".") if decimal_comma else (".", ",")
    integer_part = token.split(dec)[0]
    candidates = [
        re.sub(r"\D", "", token),                       # every separator treated as grouping
        integer_part.replace(grp, ""),                  # decimals dropped
    ]
    if token.count(grp) <= 1:
        candidates.append(token.replace(dec, "").replace(grp, "."))  # grouping separator taken as decimal
        candidates.append(integer_part.replace(grp, "."))            # ... and digits after it dropped
    values: Set[Decimal] = set()
    for c in candidates:
        if not c or c == ".":
            continue
        try:
            values.add(Decimal(c).quantize(quantum, rounding=ROUND_HALF_UP))
        except InvalidOperation:
            continue
    return values


def verify_amounts_against_raw_text(data: Dict[str, Any], source: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """
    Cross-checks OCR/AI-extracted numbers against the numbers printed in the document's raw text.
    An extracted value that is not printed anywhere, but equals a wrong-separator reading of exactly one
    printed amount (e.g. 1.23 or 123456 for "1.234,56"), is corrected. Returns (data, corrections, warnings);
    the input dict is not modified.
    """
    raw_text = data.get("_raw_text")
    if not raw_text:
        return data, [], []
    label = "bank statement" if source == "bank" else "business ledger"
    prefix = "B" if source == "bank" else "L"
    tokens = _raw_number_tokens(raw_text)
    decimal_comma = _raw_text_decimal_comma(tokens)
    if decimal_comma is None:
        return data, [], []

    printed: Set[Decimal] = set()
    misread_map: Dict[Decimal, Set[Decimal]] = defaultdict(set)
    for t in tokens:
        clean = t.replace("\u00a0", "").replace("\u202f", "").replace("'", "")
        value = to_decimal(clean, decimal_comma)
        if value is None:
            continue
        printed.add(value)
        if not _DAY_MONTH.match(clean):
            for wrong in _misreadings(clean, decimal_comma):
                if wrong != value:
                    misread_map[wrong].add(value)

    corrections: List[Dict[str, Any]] = []
    not_found = checked = 0

    def check(value: Any, where: Dict[str, Any]) -> Any:
        nonlocal not_found, checked
        if value is None or isinstance(value, str):
            return value  # strings are parsed later with the document's own convention
        extracted = to_decimal(value)
        if extracted is None or extracted == 0:
            return value
        checked += 1
        magnitude = abs(extracted)
        if magnitude in printed:
            return value
        fixes = misread_map.get(magnitude, set())
        if len(fixes) == 1:
            fixed = next(iter(fixes)).copy_sign(extracted)
            corrections.append({**where, "extracted": _fmt(extracted), "corrected": _fmt(fixed)})
            return fixed
        not_found += 1
        return value

    fixed_data = dict(data)
    for field in ("opening_balance", "closing_balance"):
        fixed_data[field] = check(data.get(field), {"source": source, "transaction_id": None, "field": field})
    fixed_txs = []
    for row, tx in enumerate(data.get("transactions") or [], start=1):
        if not isinstance(tx, dict):
            fixed_txs.append(tx)
            continue
        new_tx = dict(tx)
        for field in _AMOUNT_FIELDS:
            if field in tx:
                new_tx[field] = check(tx[field], {"source": source, "transaction_id": f"{prefix}{row}", "field": field})
        fixed_txs.append(new_tx)
    fixed_data["transactions"] = fixed_txs

    warnings: List[str] = []
    if corrections:
        warnings.append(
            f"{len(corrections)} {label} amount(s) were misread by OCR/AI extraction (wrong decimal separator) "
            "and were corrected from the printed values; see amount_corrections."
        )
    if checked and not_found > max(3, checked // 10):
        warnings.append(
            f"{not_found} of {checked} extracted {label} amounts could not be found in the document text; "
            "please review the extracted transactions."
        )
    return fixed_data, corrections, warnings


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_AMOUNT_FIELDS = ("debit", "credit", "amount", "balance")


def document_decimal_comma(data: Dict[str, Any]) -> Optional[bool]:
    """Decimal-separator convention for one document, inferred from all of its amount strings."""
    values: List[Any] = [data.get("opening_balance"), data.get("closing_balance")]
    for tx in data.get("transactions") or []:
        if isinstance(tx, dict):
            values.extend(tx.get(f) for f in _AMOUNT_FIELDS)
    return detect_decimal_comma(values)


def normalize_transactions(
    raw_transactions: Iterable[Dict[str, Any]],
    source: str,
    decimal_comma: Optional[bool] = None,
) -> Tuple[List[NormalizedTransaction], List[str]]:
    """Builds normalized transactions without mutating the extracted originals."""
    prefix = "B" if source == "bank" else "L"
    label = "bank statement" if source == "bank" else "business ledger"
    raw_transactions = list(raw_transactions)
    normalized: List[NormalizedTransaction] = []
    skipped_no_amount = 0
    both_sides = 0
    unparsed_dates = 0
    month_first = detect_month_first(tx.get("date") for tx in raw_transactions if isinstance(tx, dict))

    for row, tx in enumerate(raw_transactions, start=1):
        if not isinstance(tx, dict):
            skipped_no_amount += 1
            continue
        debit = to_decimal(tx.get("debit"), decimal_comma)
        credit = to_decimal(tx.get("credit"), decimal_comma)
        if debit is None and credit is None and tx.get("amount") is not None:
            amount, marker = parse_amount(tx.get("amount"), decimal_comma)
            tx_type = str(tx.get("type") or marker or "").strip().lower()
            if amount is not None:
                if tx_type.startswith(("d", "w")) or (not tx_type and amount < 0):
                    debit = abs(amount)
                else:
                    credit = abs(amount)
        if debit is not None and credit is not None and debit != 0 and credit != 0:
            both_sides += 1
        net = (abs(credit) if credit else ZERO) - (abs(debit) if debit else ZERO)
        if net == 0:
            skipped_no_amount += 1
            continue

        parsed_date = parse_date(tx.get("date"), month_first=month_first)
        if parsed_date is None:
            unparsed_dates += 1
        balance, balance_marker = parse_amount(tx.get("balance"), decimal_comma)
        if balance is not None and balance_marker == "dr" and source == "bank":
            balance = -abs(balance)  # "Dr" balance on a bank statement means overdrawn
        description = str(tx.get("description") or "").strip()
        original_ref = tx.get("reference")
        original_ref = str(original_ref).strip() if original_ref not in (None, "") else None
        normalized.append(
            NormalizedTransaction(
                id=f"{prefix}{row}",
                source=source,
                row=row,
                date=parsed_date,
                original_date=None if tx.get("date") is None else str(tx.get("date")),
                amount=abs(net),
                direction="credit" if net > 0 else "debit",
                reference=_normalize_reference(original_ref),
                original_reference=original_ref,
                description=description,
                desc_key=" ".join(sorted(_tokens(description))),
                tokens=_tokens(description),
                balance=balance,
            )
        )

    warnings: List[str] = []
    if month_first:
        warnings.append(f"{label.capitalize()} dates were interpreted as month-first (MM/DD/YYYY).")
    if skipped_no_amount:
        warnings.append(f"{skipped_no_amount} {label} row(s) had no usable debit/credit amount and were not reconciled.")
    if both_sides:
        warnings.append(f"{both_sides} {label} row(s) contained both debit and credit amounts; the net amount was used.")
    if unparsed_dates:
        warnings.append(
            f"{unparsed_dates} {label} transaction(s) have a missing or unrecognised date; "
            "they were matched on amount, reference and description only."
        )
    return normalized, warnings


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _flow(tx: NormalizedTransaction, ledger_convention: str) -> str:
    """Money direction relative to the bank account: 'in' (deposit) or 'out' (withdrawal)."""
    if tx.source == "bank" or ledger_convention == "bank":
        return "in" if tx.direction == "credit" else "out"
    return "in" if tx.direction == "debit" else "out"


def _signed(tx: NormalizedTransaction, ledger_convention: str) -> Decimal:
    return tx.amount if _flow(tx, ledger_convention) == "in" else -tx.amount


def _day_diff(a: NormalizedTransaction, b: NormalizedTransaction) -> Optional[int]:
    if a.date is None or b.date is None:
        return None
    return abs((a.date - b.date).days)


def _score_pair(
    b: NormalizedTransaction,
    l: NormalizedTransaction,
    options: ReconciliationOptions,
) -> Optional[Tuple[int, List[str], Optional[int]]]:
    dd = _day_diff(b, l)
    score = 100
    basis = ["amount"]
    if dd is None:
        score -= 20
        basis.append("date_unavailable")
    elif dd > options.date_tolerance_days:
        return None
    else:
        score += max(0, 30 - 5 * dd)
        basis.append("same_date" if dd == 0 else f"date_within_{dd}_day{'s' if dd > 1 else ''}")
    if _references_match(b.reference, l.reference):
        score += 50
        basis.append("reference")
    if options.description_matching:
        sim = _similarity(b.tokens, l.tokens)
        score += int(sim * 40)
        if sim >= 0.5:
            basis.append("description")
    return score, basis, dd


class _AmountIndex:
    """Ledger entries grouped by (amount, flow) and sorted by date for windowed lookups."""

    def __init__(self, txs: List[NormalizedTransaction], convention: str):
        self.convention = convention
        dated: Dict[Tuple[Decimal, str], List[NormalizedTransaction]] = defaultdict(list)
        self.undated: Dict[Tuple[Decimal, str], List[NormalizedTransaction]] = defaultdict(list)
        for t in txs:
            key = (t.amount, _flow(t, convention))
            (dated if t.date else self.undated)[key].append(t)
        self.dated = {k: sorted(v, key=lambda t: (t.date, t.row)) for k, v in dated.items()}
        self.ordinals = {k: [t.date.toordinal() for t in v] for k, v in self.dated.items()}

    def key_for(self, tx: NormalizedTransaction) -> Tuple[Decimal, str]:
        return (tx.amount, _flow(tx, self.convention))

    def window(self, tx: NormalizedTransaction, tolerance: int) -> List[NormalizedTransaction]:
        key = self.key_for(tx)
        dated = self.dated.get(key, [])
        if tx.date is None:
            return dated + self.undated.get(key, [])
        ords = self.ordinals.get(key, [])
        lo = bisect_left(ords, tx.date.toordinal() - tolerance)
        hi = bisect_right(ords, tx.date.toordinal() + tolerance)
        return dated[lo:hi] + self.undated.get(key, [])

    def nearest(self, tx: NormalizedTransaction) -> Optional[NormalizedTransaction]:
        key = self.key_for(tx)
        dated = self.dated.get(key, [])
        if tx.date is None or not dated:
            return dated[0] if dated else (self.undated.get(key) or [None])[0]
        ords = self.ordinals[key]
        i = bisect_left(ords, tx.date.toordinal())
        options = [dated[j] for j in (i - 1, i) if 0 <= j < len(dated)]
        return min(options, key=lambda t: (abs((t.date - tx.date).days), t.row))


def _exact_candidates(bank, ledger, convention, options):
    index = _AmountIndex(ledger, convention)
    candidates = []
    for b in bank:
        for l in index.window(b, options.date_tolerance_days):
            scored = _score_pair(b, l, options)
            if scored:
                score, basis, dd = scored
                candidates.append((score, dd, b, l, basis))
    return candidates


def _greedy_assign(candidates) -> List[Tuple[Any, ...]]:
    """One-to-one assignment, highest score first; deterministic tie-breaking by row order."""
    candidates.sort(key=lambda c: (-c[0], 999 if c[1] is None else c[1], c[2].row, c[3].row))
    used_bank, used_ledger, chosen = set(), set(), []
    for cand in candidates:
        b, l = cand[2], cand[3]
        if b.id in used_bank or l.id in used_ledger:
            continue
        used_bank.add(b.id)
        used_ledger.add(l.id)
        chosen.append(cand)
    return chosen


def _confidence(score: int) -> str:
    if score >= 150:
        return "high"
    if score >= 120:
        return "medium"
    return "low"


def _mismatch_candidates(bank, ledger, convention, options):
    """Pairs that look like the same transaction but differ in amount or direction."""
    tol = options.date_tolerance_days
    by_date: Dict[date, List[NormalizedTransaction]] = defaultdict(list)
    by_ref: Dict[str, List[NormalizedTransaction]] = defaultdict(list)
    for l in ledger:
        if l.date:
            by_date[l.date].append(l)
        if l.reference:
            by_ref[l.reference].append(l)

    candidates = []
    for b in bank:
        pool: Dict[str, NormalizedTransaction] = {}
        if b.date:
            for offset in range(-tol, tol + 1):
                for l in by_date.get(b.date + timedelta(days=offset), []):
                    pool[l.id] = l
        if b.reference:
            for l in by_ref.get(b.reference, []):
                pool[l.id] = l
        for l in pool.values():
            dd = _day_diff(b, l)
            if dd is not None and dd > tol:
                continue
            ref_match = _references_match(b.reference, l.reference)
            sim = _similarity(b.tokens, l.tokens) if options.description_matching else 0.0
            same_amount = b.amount == l.amount
            same_flow = _flow(b, convention) == _flow(l, convention)
            if same_amount and same_flow:
                continue  # would have been an exact match
            if same_amount:
                # Same-day deposit and withdrawal of equal value are common; require a real link.
                if not (ref_match or sim >= 0.5):
                    continue
                kind = "direction_mismatch"
            else:
                larger = max(b.amount, l.amount)
                relative_diff = abs(b.amount - l.amount) / larger if larger else Decimal(1)
                if not (ref_match or (sim >= 0.6 and relative_diff <= Decimal("0.2"))):
                    continue
                kind = "amount_mismatch" if same_flow else "amount_and_direction_mismatch"
            score = (100 if ref_match else 0) + int(sim * 40) - (dd or 0) * 5
            candidates.append((score, dd, b, l, kind))
    return candidates


def _mismatch_reason(kind, b, l, b_flow, l_flow, ref_match) -> str:
    link = "same reference" if ref_match else "similar description and date"
    if kind == "direction_mismatch":
        return (
            f"Linked by {link}; amount {_fmt(b.amount)} agrees but the bank records money {b_flow} "
            f"while the ledger records money {l_flow}."
        )
    text = (
        f"Linked by {link}; bank amount {_fmt(b.amount)} differs from ledger amount {_fmt(l.amount)} "
        f"by {_fmt(abs(b.amount - l.amount))}."
    )
    if kind == "amount_and_direction_mismatch":
        text += f" Direction also differs (bank: money {b_flow}, ledger: money {l_flow})."
    return text


def _unmatched_reason(tx, other_index: _AmountIndex, options, other_label) -> str:
    near = other_index.nearest(tx)
    if near is None:
        return f"No {other_label} entry with the same amount and direction was found."
    dd = _day_diff(tx, near)
    if dd is not None and dd > options.date_tolerance_days:
        return (
            f"Closest {other_label} entry with the same amount ({near.id}, {near.date.isoformat()}) is {dd} days apart, "
            f"outside the ±{options.date_tolerance_days}-day tolerance."
        )
    return (
        f"The {other_label} entries with the same amount and direction were already matched to other transactions."
    )


def _find_duplicates(txs: List[NormalizedTransaction], convention: str) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[Any, ...], List[NormalizedTransaction]] = defaultdict(list)
    for tx in txs:
        if tx.date is None:
            continue
        groups[(tx.date, tx.amount, tx.direction, tx.desc_key, tx.reference)].append(tx)
    label = "bank statement" if txs and txs[0].source == "bank" else "business ledger"
    duplicates = []
    for group in groups.values():
        if len(group) < 2:
            continue
        first = group[0]
        duplicates.append({
            "category": "possible_duplicates",
            "source": first.source,
            "count": len(group),
            "transaction_ids": [t.id for t in group],
            "transactions": [t.to_dict(_flow(t, convention)) for t in group],
            "reason": (
                f"{len(group)} {label} entries share the same date ({first.date.isoformat()}), amount "
                f"({_fmt(first.amount)}), direction, description and reference. Nothing was removed; please review."
            ),
        })
    duplicates.sort(key=lambda d: (d["source"], d["transactions"][0]["row"]))
    return duplicates


# ---------------------------------------------------------------------------
# Balance verification
# ---------------------------------------------------------------------------

def _chronological(txs: List[NormalizedTransaction]) -> Tuple[List[NormalizedTransaction], bool]:
    dated = [t.date for t in txs if t.date]
    if len(dated) >= 2 and dated[0] > dated[-1]:
        return list(reversed(txs)), True
    return txs, False


def _balance_check(
    txs: List[NormalizedTransaction],
    opening: Optional[Decimal],
    closing: Optional[Decimal],
    convention: str,
    label: str,
) -> Dict[str, Any]:
    notes: List[str] = []
    ordered, reversed_order = _chronological(txs)
    if reversed_order:
        notes.append(f"{label.capitalize()} transactions are in reverse chronological order; balances were verified oldest-first.")

    opening_source = "document" if opening is not None else None
    closing_source = "document" if closing is not None else None
    if opening is None and ordered and ordered[0].balance is not None:
        opening = ordered[0].balance - _signed(ordered[0], convention)
        opening_source = "derived_from_first_transaction_balance"
        notes.append(f"{label.capitalize()} opening balance was not stated; it was derived from the first running balance.")
    if closing is None and ordered and ordered[-1].balance is not None:
        closing = ordered[-1].balance
        closing_source = "last_transaction_balance"

    net = sum((_signed(t, convention) for t in ordered), ZERO)
    calculated = opening + net if opening is not None else None
    if opening is None or closing is None:
        status = "unverifiable"
        difference = None
        missing = [n for n, v in (("opening balance", opening), ("closing balance", closing)) if v is None]
        notes.append(f"{label.capitalize()} {' and '.join(missing)} not available; closing balance could not be verified.")
    else:
        difference = closing - calculated
        status = "passed" if difference == 0 else "failed"

    breaks: List[Dict[str, Any]] = []
    checked = 0
    prev = opening
    for t in ordered:
        expected = prev + _signed(t, convention) if prev is not None else None
        if t.balance is not None:
            if expected is not None:
                checked += 1
                if t.balance != expected:
                    breaks.append({
                        "transaction_id": t.id,
                        "date": t.date.isoformat() if t.date else None,
                        "expected_balance": _fmt(expected),
                        "stated_balance": _fmt(t.balance),
                        "difference": _fmt(t.balance - expected),
                    })
            prev = t.balance
        else:
            prev = expected
    if checked == 0:
        running_status = "unverifiable"
    else:
        running_status = "passed" if not breaks else "failed"

    return {
        "opening_balance": _fmt(opening),
        "closing_balance": _fmt(closing),
        "calculated_closing_balance": _fmt(calculated),
        "difference": _fmt(difference),
        "status": status,
        "opening_balance_source": opening_source,
        "closing_balance_source": closing_source,
        "running_balance": {
            "status": running_status,
            "checked": checked,
            "breaks_count": len(breaks),
            "breaks": breaks[:MAX_LISTED_BALANCE_BREAKS],
        },
        "notes": notes,
        "_opening": opening,
        "_closing": closing,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def reconcile(
    bank_data: Dict[str, Any],
    ledger_data: Dict[str, Any],
    options: ReconciliationOptions = ReconciliationOptions(),
) -> Dict[str, Any]:
    """
    Reconciles two statement-shaped dicts (transactions, opening_balance, closing_balance, currency).
    Returns a JSON-serializable report. Raises ReconciliationError for unrecoverable input problems.
    Amounts are rounded to the currency's ISO 4217 minor units (2 unless the currency says otherwise).
    """
    warnings: List[str] = []

    bank_currency_raw, ledger_currency_raw = bank_data.get("currency"), ledger_data.get("currency")
    bank_currency, ledger_currency = normalize_currency(bank_currency_raw), normalize_currency(ledger_currency_raw)
    for raw, parsed, label in ((bank_currency_raw, bank_currency, "bank statement"), (ledger_currency_raw, ledger_currency, "business ledger")):
        if raw and not parsed:
            shown = str(raw).strip()[:10]
            if shown.upper() in AMBIGUOUS_CURRENCY_SYMBOLS:
                warnings.append(
                    f"The {label} currency symbol '{shown}' is shared by several currencies; "
                    "it was not used to identify the currency."
                )
            else:
                warnings.append(f"Unrecognised {label} currency '{shown}' was ignored.")
    if bank_currency and ledger_currency and bank_currency != ledger_currency:
        raise ReconciliationError(
            f"Bank statement currency ({bank_currency}) differs from business ledger currency ({ledger_currency}); "
            "cross-currency reconciliation is not supported."
        )
    currency = bank_currency or ledger_currency
    if not currency:
        warnings.append("Currency could not be determined from either document; amounts were compared as plain numbers.")

    token = _QUANTUM.set(Decimal(1).scaleb(-minor_units(currency)))
    try:
        return _reconcile_amounts(bank_data, ledger_data, options, currency, warnings)
    finally:
        _QUANTUM.reset(token)


def _reconcile_amounts(
    bank_data: Dict[str, Any],
    ledger_data: Dict[str, Any],
    options: ReconciliationOptions,
    currency: Optional[str],
    warnings: List[str],
) -> Dict[str, Any]:
    bank_data, bank_fixes, bank_fix_warnings = verify_amounts_against_raw_text(bank_data, "bank")
    ledger_data, ledger_fixes, ledger_fix_warnings = verify_amounts_against_raw_text(ledger_data, "ledger")
    amount_corrections = bank_fixes + ledger_fixes
    warnings.extend(bank_fix_warnings + ledger_fix_warnings)
    bank_comma, ledger_comma = document_decimal_comma(bank_data), document_decimal_comma(ledger_data)
    for flag, label in ((bank_comma, "Bank statement"), (ledger_comma, "Business ledger")):
        if flag:
            warnings.append(f"{label} amounts use a comma as the decimal separator (e.g. 1.234,56).")

    bank, bank_warnings = normalize_transactions(bank_data.get("transactions") or [], "bank", bank_comma)
    ledger, ledger_warnings = normalize_transactions(ledger_data.get("transactions") or [], "ledger", ledger_comma)
    warnings += bank_warnings + ledger_warnings
    if not bank:
        raise ReconciliationError("Unable to extract transactions from the bank statement.")
    if not ledger:
        raise ReconciliationError("Unable to extract transactions from the business ledger.")

    # Ledgers exported from accounting books record bank deposits as debits; detect which convention fits.
    bank_conv = _greedy_assign(_exact_candidates(bank, ledger, "bank", options))
    book_conv = _greedy_assign(_exact_candidates(bank, ledger, "book", options))
    # Require a clear margin so one coincidental same-amount pair cannot flip every direction.
    margin = max(2, min(len(bank), len(ledger)) // 10)
    if len(book_conv) - len(bank_conv) >= margin:
        convention, exact = "book", book_conv
        warnings.append(
            "The business ledger uses book-keeping convention (ledger debit = money into the bank); "
            "debit/credit directions were interpreted accordingly."
        )
    else:
        convention, exact = "bank", bank_conv

    matched_ids = set()
    matched = []
    for score, dd, b, l, basis in exact:
        matched_ids.update((b.id, l.id))
        matched.append({
            "category": "matched",
            "bank_transaction": b.to_dict(_flow(b, convention)),
            "ledger_transaction": l.to_dict(_flow(l, convention)),
            "amount": _fmt(b.amount),
            "date_difference_days": dd,
            "match_basis": basis,
            "confidence": _confidence(score),
        })

    rem_bank = [b for b in bank if b.id not in matched_ids]
    rem_ledger = [l for l in ledger if l.id not in matched_ids]
    mismatches = []
    for score, dd, b, l, kind in _greedy_assign(_mismatch_candidates(rem_bank, rem_ledger, convention, options)):
        matched_ids.update((b.id, l.id))
        b_flow, l_flow = _flow(b, convention), _flow(l, convention)
        mismatches.append({
            "category": "amount_mismatches",
            "type": kind,
            "bank_transaction": b.to_dict(b_flow),
            "ledger_transaction": l.to_dict(l_flow),
            "bank_amount": _fmt(_signed(b, convention)),
            "ledger_amount": _fmt(_signed(l, convention)),
            "difference": _fmt(_signed(b, convention) - _signed(l, convention)),
            "date_difference_days": dd,
            "reason": _mismatch_reason(kind, b, l, b_flow, l_flow, _references_match(b.reference, l.reference)),
        })
    mismatches.sort(key=lambda m: m["bank_transaction"]["row"])

    ledger_index, bank_index = _AmountIndex(ledger, convention), _AmountIndex(bank, convention)
    unmatched_bank = [
        {"category": "unmatched_bank", **b.to_dict(_flow(b, convention)),
         "reason": _unmatched_reason(b, ledger_index, options, "ledger")}
        for b in bank if b.id not in matched_ids
    ]
    unmatched_ledger = [
        {"category": "unmatched_ledger", **l.to_dict(_flow(l, convention)),
         "reason": _unmatched_reason(l, bank_index, options, "bank statement")}
        for l in ledger if l.id not in matched_ids
    ]
    duplicates = _find_duplicates(bank, convention) + _find_duplicates(ledger, convention)
    matched.sort(key=lambda m: m["bank_transaction"]["row"])

    bank_bal = _balance_check(
        bank, to_decimal(bank_data.get("opening_balance"), bank_comma),
        to_decimal(bank_data.get("closing_balance"), bank_comma), convention, "bank statement",
    )
    ledger_bal = _balance_check(
        ledger, to_decimal(ledger_data.get("opening_balance"), ledger_comma),
        to_decimal(ledger_data.get("closing_balance"), ledger_comma), convention, "business ledger",
    )

    # Book-to-bank: the closing difference should be fully explained by unreconciled items.
    unmatched_effect = (
        sum((_signed(b, convention) for b in bank if b.id not in matched_ids), ZERO)
        - sum((_signed(l, convention) for l in ledger if l.id not in matched_ids), ZERO)
        + sum((Decimal(m["difference"]) for m in mismatches), ZERO)
    )
    book_to_bank: Dict[str, Any] = {"explained_by_discrepancies": _fmt(unmatched_effect)}
    if None in (bank_bal["_opening"], bank_bal["_closing"], ledger_bal["_opening"], ledger_bal["_closing"]):
        book_to_bank.update({"status": "unverifiable", "closing_balance_difference": None, "unexplained_difference": None})
    else:
        closing_diff = bank_bal["_closing"] - ledger_bal["_closing"]
        opening_diff = bank_bal["_opening"] - ledger_bal["_opening"]
        unexplained = closing_diff - opening_diff - unmatched_effect
        book_to_bank.update({
            "closing_balance_difference": _fmt(closing_diff),
            "opening_balance_difference": _fmt(opening_diff),
            "unexplained_difference": _fmt(unexplained),
            "status": "passed" if unexplained == 0 else "failed",
        })

    for bal in (bank_bal, ledger_bal):
        bal.pop("_opening")
        bal.pop("_closing")
        warnings.extend(bal["notes"])

    if unmatched_bank or unmatched_ledger or mismatches or duplicates:
        warnings.append(DISCREPANCY_NOTE)

    return {
        "summary": {
            "currency": currency,
            "amount_decimal_places": minor_units(currency),
            "amount_corrections": len(amount_corrections),
            "bank_transactions": len(bank),
            "ledger_transactions": len(ledger),
            "matched": len(matched),
            "unmatched_bank": len(unmatched_bank),
            "unmatched_ledger": len(unmatched_ledger),
            "possible_duplicates": len(duplicates),
            "amount_mismatches": len(mismatches),
            "ledger_direction_convention": convention,
            "date_tolerance_days": options.date_tolerance_days,
            "description_matching": options.description_matching,
        },
        "balance_check": {
            "bank_opening_balance": bank_bal["opening_balance"],
            "bank_closing_balance": bank_bal["closing_balance"],
            "calculated_closing_balance": bank_bal["calculated_closing_balance"],
            "difference": bank_bal["difference"],
            "status": bank_bal["status"],
            "opening_balance_source": bank_bal["opening_balance_source"],
            "closing_balance_source": bank_bal["closing_balance_source"],
            "running_balance": bank_bal["running_balance"],
            "ledger": {k: v for k, v in ledger_bal.items() if k != "notes"},
            "book_to_bank": book_to_bank,
        },
        "matched_transactions": matched,
        "unmatched_bank_transactions": unmatched_bank,
        "unmatched_ledger_transactions": unmatched_ledger,
        "possible_duplicates": duplicates,
        "amount_mismatches": mismatches,
        "amount_corrections": amount_corrections[:MAX_LISTED_CORRECTIONS],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CSV / XLSX ledger & statement parsing
# ---------------------------------------------------------------------------

# English first, then common German / French / Spanish / Portuguese / Dutch bank export headers
# (headers are compared lower-case with accents removed).
_HEADER_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "date": ("date", "txn date", "transaction date", "tran date", "posting date", "entry date", "voucher date",
             "buchungstag", "buchungsdatum", "datum", "date operation", "fecha", "fecha operacion", "data", "data movimento",
             "value date", "valuta", "wertstellung", "date valeur", "fecha valor"),
    "description": ("description", "narration", "particulars", "details", "transaction details", "remarks", "memo", "payee",
                    "transaction description", "verwendungszweck", "buchungstext", "beschreibung", "libelle", "concepto",
                    "descripcion", "descricao", "historico", "omschrijving", "mededelingen"),
    "reference": ("reference", "ref", "ref no", "reference no", "reference number", "cheque no", "chq no", "chq ref no",
                  "cheque number", "utr", "utr no", "voucher no", "vch no", "instrument no", "transaction id", "txn id",
                  "referenz", "reference operation", "referencia", "documento", "kenmerk"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "dr", "debit amount", "withdrawal amt", "withdrawal amount",
              "paid out", "money out", "payments", "soll", "belastung", "lastschrift", "debe", "cargo", "cargos", "debito", "af"),
    "credit": ("credit", "credits", "deposit", "deposits", "cr", "credit amount", "deposit amt", "deposit amount",
               "paid in", "money in", "receipts", "haben", "gutschrift", "haber", "abono", "abonos", "credito", "bij"),
    "amount": ("amount", "transaction amount", "amt", "net amount", "betrag", "umsatz", "montant", "importe", "valor", "bedrag"),
    "type": ("type", "dr cr", "cr dr", "transaction type", "debit credit", "txn type", "soll haben", "af bij", "sens"),
    "balance": ("balance", "running balance", "closing balance", "balance amount", "available balance",
                "saldo", "kontostand", "solde"),
}
_HEADER_CONTAINS: Tuple[Tuple[str, str], ...] = (
    ("withdraw", "debit"), ("deposit", "credit"), ("narration", "description"), ("particular", "description"),
    ("description", "description"), ("balance", "balance"), ("cheque", "reference"), ("chq", "reference"),
    ("utr", "reference"), ("ref", "reference"), ("debit", "debit"), ("credit", "credit"), ("amount", "amount"), ("date", "date"),
)


def _norm_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _map_header(row: List[Any]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    headers = [_norm_header(c) for c in row]
    for field, names in _HEADER_SYNONYMS.items():
        for name in names:  # synonym priority wins over column order (e.g. "Txn Date" before "Value Date")
            idx = next((i for i, h in enumerate(headers) if h == name and i not in mapping.values()), None)
            if idx is not None:
                mapping[field] = idx
                break
    for idx, h in enumerate(headers):
        if not h or idx in mapping.values():
            continue
        for needle, field in _HEADER_CONTAINS:
            if needle in h and field not in mapping:
                mapping[field] = idx
                break
    return mapping


def _read_rows(content: bytes, ext: str) -> List[List[Any]]:
    if ext == ".xlsx":
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as e:
            raise ReconciliationError("The spreadsheet could not be opened; it may be corrupted or password protected.") from e
        try:
            ws = wb.active or (wb.worksheets[0] if wb.worksheets else None)
            if ws is None:
                raise ReconciliationError("The spreadsheet contains no worksheets.")
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) > MAX_SPREADSHEET_ROWS:
                    raise ReconciliationError(f"Spreadsheet exceeds the maximum of {MAX_SPREADSHEET_ROWS} rows.")
            return rows
        finally:
            wb.close()

    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        rows.append(row)
        if len(rows) > MAX_SPREADSHEET_ROWS:
            raise ReconciliationError(f"CSV exceeds the maximum of {MAX_SPREADSHEET_ROWS} rows.")
    return rows


def _cell(row: List[Any], idx: Optional[int]) -> Any:
    if idx is None or idx >= len(row):
        return None
    value = row[idx]
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def parse_spreadsheet(content: bytes, filename: str) -> Dict[str, Any]:
    """Parses a CSV/XLSX statement or ledger into the same shape as BankStatementExtraction."""
    ext = os.path.splitext(filename.lower())[1]
    rows = _read_rows(content, ext)

    header_idx, mapping = None, {}
    for idx, row in enumerate(rows[:30]):
        candidate = _map_header(row)
        if "date" in candidate and ({"debit", "credit", "amount"} & candidate.keys()):
            header_idx, mapping = idx, candidate
            break
    if header_idx is None:
        raise ReconciliationError(
            "Could not find a header row with date and amount columns "
            "(expected columns such as Date, Description, Debit, Credit or Amount)."
        )

    transactions: List[Dict[str, Any]] = []
    opening = closing = None
    found_currencies: set = set()
    ambiguous_symbol = False
    # Headers such as "Amount (CAD)" or "Debit £" state the currency for the whole sheet.
    for cell in rows[header_idx]:
        code, ambiguous = detect_currency_in_text(cell)
        if code:
            found_currencies.add(code)
        ambiguous_symbol |= ambiguous
    for row in rows[header_idx + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        description = _cell(row, mapping.get("description"))
        desc_text = str(description or "").lower()
        raw_date = _cell(row, mapping.get("date"))
        amount_cells = [_cell(row, mapping.get(k)) for k in ("debit", "credit", "amount")]
        balance = _cell(row, mapping.get("balance"))

        desc_text = desc_text.strip(" *-:")
        if desc_text.startswith(("opening balance", "balance b/f", "balance brought forward", "brought forward")):
            opening = balance if balance is not None else next((c for c in amount_cells if c is not None), None)
            continue
        if desc_text.startswith(("closing balance", "balance c/f", "balance carried forward", "carried forward")):
            closing = balance if balance is not None else next((c for c in amount_cells if c is not None), None)
            continue
        if raw_date is None and desc_text.startswith("total"):
            continue
        for c in amount_cells + [balance]:
            code, ambiguous = detect_currency_in_text(c)
            if code:
                found_currencies.add(code)
            ambiguous_symbol |= ambiguous

        if isinstance(raw_date, (datetime, date)):
            raw_date = (raw_date.date() if isinstance(raw_date, datetime) else raw_date).isoformat()
        transactions.append({
            "date": raw_date,
            "description": None if description is None else str(description),
            "reference": _cell(row, mapping.get("reference")),
            "debit": amount_cells[0],
            "credit": amount_cells[1],
            "amount": amount_cells[2],
            "type": _cell(row, mapping.get("type")),
            "balance": balance,
        })

    if len(found_currencies) > 1:
        raise ReconciliationError(
            f"The file mixes several currencies ({', '.join(sorted(found_currencies))}); "
            "upload one currency per document."
        )
    currency = next(iter(found_currencies), None)
    return {
        "currency": currency or ("$" if ambiguous_symbol else None),
        "opening_balance": opening,
        "closing_balance": closing,
        "transactions": transactions,
    }
