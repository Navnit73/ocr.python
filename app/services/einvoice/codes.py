"""
Code lists, profile identifiers and identifier checksums used by the e-invoicing module (EN 16931 / ZUGFeRD / XRechnung).
"""

import re
from decimal import Decimal
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Specification identifiers (BT-24) and business process (BT-23)
# ---------------------------------------------------------------------------

URN_EN16931 = "urn:cen.eu:en16931:2017"
URN_XRECHNUNG_PREFIX = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_"
URN_PEPPOL_BILLING = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"


def xrechnung_urn(version: str) -> str:
    return f"{URN_XRECHNUNG_PREFIX}{version}"


# Specification identifier -> profile name
PROFILE_BY_URN: Dict[str, str] = {
    "urn:factur-x.eu:1p0:minimum": "MINIMUM",
    "urn:factur-x.eu:1p0:basicwl": "BASIC WL",
    "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic": "BASIC",
    URN_EN16931: "EN 16931 (COMFORT)",
    "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended": "EXTENDED",
    # ZUGFeRD 2.0 identifiers (2.1+ uses the factur-x.eu URNs above; EN 16931 is identical)
    "urn:zugferd.de:2p0:minimum": "MINIMUM",
    "urn:zugferd.de:2p0:basicwl": "BASIC WL",
    "urn:cen.eu:en16931:2017#compliant#urn:zugferd.de:2p0:basic": "BASIC",
    "urn:cen.eu:en16931:2017#conformant#urn:zugferd.de:2p0:extended": "EXTENDED",
    # Peppol BIS Billing 3.0 (valid e-invoice in Germany)
    "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0": "PEPPOL BIS 3.0",
    # ZUGFeRD 1.0 (CrossIndustryDocument)
    "urn:ferd:CrossIndustryDocument:invoice:1p0:basic": "ZUGFeRD 1.0 BASIC",
    "urn:ferd:CrossIndustryDocument:invoice:1p0:comfort": "ZUGFeRD 1.0 COMFORT",
    "urn:ferd:CrossIndustryDocument:invoice:1p0:extended": "ZUGFeRD 1.0 EXTENDED",
}

PROFILE_XSD_LEVEL: Dict[str, str] = {
    "MINIMUM": "minimum",
    "BASIC WL": "basicwl",
    "BASIC": "basic",
    "EN 16931 (COMFORT)": "en16931",
    "EXTENDED": "extended",
    # XRechnung may use the full CII D16B vocabulary (e.g. the XRechnung extension); KoSIT also uses the full schema.
    "XRECHNUNG": "cii-full",
    "PEPPOL BIS 3.0": "cii-full",
    "EN 16931 (CIUS)": "cii-full",
}

# Expected fx:ConformanceLevel in the PDF XMP metadata per profile
XMP_CONFORMANCE_LEVEL: Dict[str, str] = {
    "MINIMUM": "MINIMUM",
    "BASIC WL": "BASIC WL",
    "BASIC": "BASIC",
    "EN 16931 (COMFORT)": "EN 16931",
    "EXTENDED": "EXTENDED",
    "XRECHNUNG": "XRECHNUNG",
}

# Profiles that carry a full EN 16931 semantic invoice. MINIMUM / BASIC WL are not valid invoices under the
# German VAT Act (UStG) since the 2025 e-invoicing reform.
EN16931_COMPLIANT_PROFILES = {"BASIC", "EN 16931 (COMFORT)", "EXTENDED", "XRECHNUNG", "PEPPOL BIS 3.0", "EN 16931 (CIUS)"}


_XRECHNUNG_URN = re.compile(r"urn:(?:xeinkauf\.de:kosit|xoev-de:kosit:standard):xrechnung_([0-9]+(?:\.[0-9]+)*)")


def detect_profile(guideline_id: Optional[str]) -> Tuple[str, Optional[str]]:
    """Returns (profile, xrechnung_version) for a specification identifier.
    Covers XRechnung 1.x/2.x (xoev-de URN), 3.x (xeinkauf.de URN) and the '#conformant#…extension' variant."""
    gid = (guideline_id or "").strip()
    match = _XRECHNUNG_URN.search(gid)
    if match:
        return "XRECHNUNG", match.group(1)
    if gid in PROFILE_BY_URN:
        return PROFILE_BY_URN[gid], None
    # Any other core-invoice usage specification (CIUS) declared compliant with EN 16931
    if gid.startswith(f"{URN_EN16931}#compliant#"):
        return "EN 16931 (CIUS)", None
    return "UNKNOWN", None


# ---------------------------------------------------------------------------
# VAT categories (UNTDID 5305 subset used by EN 16931) and rule prefixes
# ---------------------------------------------------------------------------

# Category -> prefix used in EN 16931 rule IDs, e.g. BR-S-08, BR-IC-02, BR-AF-05
VAT_RULE_PREFIX: Dict[str, str] = {
    "S": "S",    # Standard rate
    "Z": "Z",    # Zero rated goods
    "E": "E",    # Exempt from tax
    "AE": "AE",  # VAT reverse charge
    "K": "IC",   # Intra-community supply
    "G": "G",    # Export outside the EU
    "O": "O",    # Services outside scope of tax
    "L": "AF",   # Canary Islands IGIC
    "M": "AG",   # Ceuta and Melilla IPSI
}
ZERO_RATE_CATEGORIES = {"Z", "E", "AE", "K", "G", "O"}
EXEMPTION_REASON_REQUIRED = {"E", "AE", "K", "G", "O"}

# ---------------------------------------------------------------------------
# Units of measure (UN/ECE Recommendation 20/21). German aliases map to the official code.
# ---------------------------------------------------------------------------

UNIT_ALIASES: Dict[str, str] = {
    "stk": "C62", "stck": "C62", "stück": "C62", "stueck": "C62", "st": "C62", "pcs": "C62", "pc": "C62",
    "piece": "C62", "pieces": "C62", "einheit": "C62", "unit": "C62", "ea": "C62",
    "std": "HUR", "h": "HUR", "stunde": "HUR", "stunden": "HUR", "hour": "HUR", "hours": "HUR",
    "min": "MIN", "minute": "MIN", "minuten": "MIN",
    "tag": "DAY", "tage": "DAY", "day": "DAY", "days": "DAY", "pt": "DAY",
    "woche": "WEE", "week": "WEE",
    "monat": "MON", "monate": "MON", "month": "MON",
    "jahr": "ANN", "year": "ANN",
    "kg": "KGM", "g": "GRM", "t": "TNE", "tonne": "TNE",
    "m": "MTR", "km": "KMT", "cm": "CMT", "mm": "MMT",
    "m2": "MTK", "m²": "MTK", "qm": "MTK", "m3": "MTQ", "m³": "MTQ", "cbm": "MTQ",
    "l": "LTR", "liter": "LTR", "ml": "MLT",
    "pauschal": "LS", "psch": "LS", "pausch": "LS", "lump sum": "LS",
    "paket": "XPK", "pkg": "XPK", "package": "XPK",
    "set": "SET", "satz": "SET", "paar": "PR", "pair": "PR",
    "kwh": "KWH", "blatt": "ST", "sheet": "ST",
}
_UNIT_CODE = re.compile(r"^[A-Z0-9]{1,3}$")


# Upper-case inputs that are user shorthands, not UN/ECE codes (everything else upper-case is passed through)
_UPPER_ALIASES = {"STK", "STCK", "STD", "PCS", "PC", "KG", "QM", "CBM", "H", "L", "M", "G", "T"}


def normalize_unit(value: str) -> Optional[str]:
    """Maps 'Stk' -> 'C62', 'Std' -> 'HUR'. Upper-case values that look like UN/ECE codes are kept as they are,
    so real codes from incoming XML (e.g. ST = sheet, PT = pint, EA = each) are never rewritten."""
    raw = (value or "").strip()
    if not raw:
        return None
    if _UNIT_CODE.match(raw) and raw not in _UPPER_ALIASES:
        return raw
    return UNIT_ALIASES.get(raw.lower())


# ---------------------------------------------------------------------------
# Invoice type codes (UNTDID 1001 subset of the EN 16931 code list, BR-CL-01)
# ---------------------------------------------------------------------------

INVOICE_TYPE_CODES = frozenset("""
71 80 81 82 83 84 102 130 202 203 204 211 218 219 261 262 295 296 308 325 326 331 380 381 382 383 384 385 386 387
388 389 390 393 394 395 396 420 456 457 458 527 532 553 575 623 633 751 780 817 870 875 876 877 935
""".split())
# BR-DE-17: XRechnung only allows these
XRECHNUNG_TYPE_CODES = frozenset({"326", "380", "381", "384", "389", "875", "876", "877"})
CREDIT_NOTE_TYPE_CODES = frozenset({"81", "83", "261", "262", "296", "308", "381", "396", "420", "458", "532"})

# ---------------------------------------------------------------------------
# Payment means (UNTDID 4461 subset relevant for German invoicing)
# ---------------------------------------------------------------------------

PAYMENT_MEANS: Dict[str, str] = {
    "1": "Instrument not defined",
    "10": "In cash",
    "20": "Cheque",
    "30": "Credit transfer",
    "31": "Debit transfer",
    "42": "Payment to bank account",
    "48": "Bank card",
    "49": "Direct debit",
    "54": "Credit card",
    "55": "Debit card",
    "57": "Standing agreement",
    "58": "SEPA credit transfer",
    "59": "SEPA direct debit",
    "97": "Clearing between partners",
    "ZZZ": "Mutually defined",
}
_PAYMENT_MEANS_CODE = re.compile(r"^(?:[1-9][0-9]?|ZZZ)$")


def is_payment_means_code(value: str) -> bool:
    """UNTDID 4461 codes are 1..97 plus ZZZ; the table above only names the common ones."""
    return bool(_PAYMENT_MEANS_CODE.match(value or ""))


CREDIT_TRANSFER_MEANS = {"30", "58"}
DIRECT_DEBIT_MEANS = {"49", "59"}
CARD_MEANS = {"48", "54", "55"}

# ---------------------------------------------------------------------------
# ISO 3166-1 alpha-2 (plus XI = Northern Ireland, allowed by the EN 16931 code list) and ISO 4217
# ---------------------------------------------------------------------------

COUNTRY_CODES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO
FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE
JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO
MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW
PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM
TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW XI
""".split())

CURRENCY_CODES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF CHF
CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG
HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD
RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX
USD UYU UZS VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG ZWL
""".split())

COUNTRY_NAME_ALIASES: Dict[str, str] = {
    "deutschland": "DE", "germany": "DE", "österreich": "AT", "oesterreich": "AT", "austria": "AT",
    "schweiz": "CH", "switzerland": "CH", "frankreich": "FR", "france": "FR", "niederlande": "NL",
    "netherlands": "NL", "belgien": "BE", "belgium": "BE", "luxemburg": "LU", "luxembourg": "LU",
    "italien": "IT", "italy": "IT", "spanien": "ES", "spain": "ES", "polen": "PL", "poland": "PL",
    "dänemark": "DK", "denmark": "DK", "tschechien": "CZ", "czechia": "CZ",
}

# ---------------------------------------------------------------------------
# Identifier checksums
# ---------------------------------------------------------------------------

_IBAN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_BIC = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_VAT_ID = re.compile(r"^[A-Z]{2}[0-9A-Z+*./-]{2,18}$")
_LEITWEG = re.compile(r"^[0-9]{2,12}(-[0-9A-Z]{1,30})?-[0-9]{2}$", re.IGNORECASE)


def _mod97(text: str) -> int:
    return int("".join(str(int(ch, 36)) for ch in text.upper())) % 97


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def is_valid_iban(value: str) -> bool:
    iban = compact(value)
    return bool(_IBAN.match(iban)) and _mod97(iban[4:] + iban[:4]) == 1


def is_valid_bic(value: str) -> bool:
    return bool(_BIC.match(compact(value)))


def is_valid_gtin(value: str) -> bool:
    """GTIN-8/12/13/14 and GLN (13 digits) share the GS1 mod-10 check digit."""
    digits = (value or "").strip()
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body = [int(d) for d in digits[:-1]][::-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == int(digits[-1])


def vat_id_format_ok(value: str) -> bool:
    vat = compact(value)
    if not _VAT_ID.match(vat):
        return False
    if vat.startswith("DE"):
        return bool(re.fullmatch(r"DE[0-9]{9}", vat))
    return True


def vat_id_prefix_ok(value: str) -> bool:
    """BR-CO-9: the VAT identifier must be prefixed with an ISO 3166-1 alpha-2 code ('EL' for Greece)."""
    prefix = compact(value)[:2]
    return prefix in COUNTRY_CODES or prefix == "EL"


def looks_like_leitweg_id(value: str) -> bool:
    return bool(_LEITWEG.match((value or "").strip()))


def leitweg_check_digits_ok(value: str) -> bool:
    """Leitweg-ID check digits use ISO 7064 Mod 97-10 over the address parts (letters as 10..35)."""
    parts = value.strip().split("-")
    body, check = "".join(parts[:-1]), parts[-1]
    return 98 - _mod97(body + "00") == int(check)


# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

CENT = Decimal("0.01")


def fmt_amount(value: Decimal, places: int = 2) -> str:
    """CII amounts are written without exponent and with fixed decimals."""
    quant = Decimal(1).scaleb(-places)
    return f"{value.quantize(quant):f}"


def fmt_decimal(value: Decimal) -> str:
    """Quantities/prices/rates: strip trailing zeros but keep at least 2 decimals for readability."""
    text = f"{value.normalize():f}"
    if "." not in text:
        return f"{text}.00"
    whole, frac = text.split(".")
    return f"{whole}.{frac.ljust(2, '0')}"
