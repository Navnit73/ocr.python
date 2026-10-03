"""
Accounting & ERP Direct Export Service (OFX, QBO, QIF).
Provides 1-click import into QuickBooks, Xero, Zoho Books, Tally, and Quicken.
"""

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Dict, List, Optional
from app.services.analytics_service import AnalyticsService, safe_float


class AccountingExportService:
    """Generates standard Open Financial Exchange (OFX v1.02), QuickBooks Online (QBO), and QIF files."""

    @classmethod
    def generate_ofx(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        is_qbo: bool = False,
    ) -> bytes:
        """
        Generates OFX 1.02 / QBO SGML file for bank statements and invoices.
        """
        now_dt = datetime.now(timezone.utc)
        dtserver = now_dt.strftime("%Y%m%d%H%M%S")

        currency = str(extraction.get("currency") or "INR").upper()
        bank_name = str(extraction.get("bank_name") or "Bank Institution")
        acct_num = str(extraction.get("account_number_masked") or extraction.get("account_number") or "XXXX-1234")
        acct_num_clean = re.sub(r"[^A-Za-z0-9]", "", acct_num) or "12345678"
        bank_id_clean = re.sub(r"[^A-Za-z0-9]", "", bank_name)[:8].upper() or "BANK01"

        closing_bal = extraction.get("closing_balance")
        if closing_bal is None:
            closing_bal = extraction.get("total") or 0.0

        transactions = cls._extract_transactions(document_type, extraction)

        # Dates
        dtstart = "20260101000000"
        dtend = dtserver
        if transactions:
            first_date = cls._format_ofx_date(transactions[0].get("date"))
            last_date = cls._format_ofx_date(transactions[-1].get("date"))
            if first_date:
                dtstart = first_date
            if last_date:
                dtend = last_date

        intu_bid_block = "        <INTU.BID>3000\n" if is_qbo else ""

        ofx_lines = [
            "OFXHEADER:100",
            "DATA:OFXSGML",
            "VERSION:102",
            "SECURITY:NONE",
            "ENCODING:USASCII",
            "CHARSET:1252",
            "COMPRESSION:NONE",
            "OLDFILEUID:NONE",
            "NEWFILEUID:NONE",
            "",
            "<OFX>",
            "  <SIGNONMSGSRSV1>",
            "    <SONRS>",
            "      <STATUS>",
            "        <CODE>0",
            "        <SEVERITY>INFO",
            "      </STATUS>",
            f"      <DTSERVER>{dtserver}",
            "      <LANGUAGE>ENG",
            intu_bid_block.rstrip("\n") if intu_bid_block else "",
            "    </SONRS>",
            "  </SIGNONMSGSRSV1>",
            "  <BANKMSGSRSV1>",
            "    <STMTTRNRS>",
            f"      <TRNUID>{doc_id}",
            "      <STATUS>",
            "        <CODE>0",
            "        <SEVERITY>INFO",
            "      </STATUS>",
            "      <STMTRS>",
            f"        <CURDEF>{currency}",
            "        <BANKACCTFROM>",
            f"          <BANKID>{bank_id_clean}",
            f"          <ACCTID>{acct_num_clean}",
            "          <ACCTTYPE>CHECKING",
            "        </BANKACCTFROM>",
            "        <BANKTRANLIST>",
            f"          <DTSTART>{dtstart}",
            f"          <DTEND>{dtend}",
        ]

        # Filter empty lines
        ofx_lines = [l for l in ofx_lines if l != ""]

        for idx, t in enumerate(transactions, start=1):
            t_date = cls._format_ofx_date(t.get("date")) or dtserver
            t_amt = t.get("amount", 0.0)
            t_type = "CREDIT" if t_amt >= 0 else "DEBIT"
            t_desc = str(t.get("description") or "Transaction")[:32].replace("&", "&amp;")
            t_memo = str(t.get("reference") or t.get("category") or "")[:32].replace("&", "&amp;")

            # Generate unique FITID hash
            fitid_raw = f"{doc_id}_{idx}_{t_date}_{t_amt}_{t_desc}"
            fitid = hashlib.md5(fitid_raw.encode("utf-8")).hexdigest()[:16].upper()

            ofx_lines.extend([
                "          <STMTTRN>",
                f"            <TRNTYPE>{t_type}",
                f"            <DTPOSTED>{t_date}",
                f"            <TRNAMT>{t_amt:.2f}",
                f"            <FITID>{fitid}",
                f"            <NAME>{t_desc}",
                f"            <MEMO>{t_memo}",
                "          </STMTTRN>",
            ])

        ofx_lines.extend([
            "        </BANKTRANLIST>",
            "        <LEDGERBAL>",
            f"          <BALAMT>{float(closing_bal):.2f}",
            f"          <DTASOF>{dtend}",
            "        </LEDGERBAL>",
            "      </STMTRS>",
            "    </STMTTRNRS>",
            "  </BANKMSGSRSV1>",
            "</OFX>",
        ])

        return "\r\n".join(ofx_lines).encode("utf-8")

    @classmethod
    def generate_qif(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
    ) -> bytes:
        """
        Generates Quicken Interchange Format (QIF) file.
        """
        lines = ["!Type:Bank"]
        transactions = cls._extract_transactions(document_type, extraction)

        for t in transactions:
            raw_date = str(t.get("date") or "")
            qif_date = cls._format_qif_date(raw_date)
            amt = t.get("amount", 0.0)
            desc = str(t.get("description") or "Transaction")
            ref = str(t.get("reference") or "")
            cat = str(t.get("category") or AnalyticsService.categorize_description(desc))

            lines.append(f"D{qif_date}")
            lines.append(f"T{amt:.2f}")
            lines.append(f"P{desc}")
            if ref:
                lines.append(f"N{ref}")
            if cat:
                lines.append(f"L{cat}")
            lines.append("^")

        return "\n".join(lines).encode("utf-8")

    # =========================================================================
    # Helpers
    # =========================================================================
    @staticmethod
    def _extract_transactions(doc_type: str, extraction: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = []
        if doc_type == "bank_statement":
            txs = extraction.get("transactions", [])
            for t in txs:
                deb = safe_float(t.get("debit"))
                cred = safe_float(t.get("credit"))
                amt = 0.0
                if cred is not None and cred > 0:
                    amt = cred
                elif deb is not None and deb > 0:
                    amt = -deb

                desc = str(t.get("description") or "")
                results.append({
                    "date": t.get("date"),
                    "amount": amt,
                    "description": desc,
                    "reference": t.get("reference"),
                    "category": AnalyticsService.categorize_description(desc),
                })

        elif doc_type in ["invoice", "receipt"]:
            line_items = extraction.get("line_items", [])
            for item in line_items:
                qty = safe_float(item.get("quantity")) or 1.0
                price = safe_float(item.get("unit_price")) or 0.0
                tot = safe_float(item.get("amount")) or safe_float(item.get("total")) or (qty * price)
                desc = str(item.get("description") or "Line Item")
                results.append({
                    "date": extraction.get("invoice_date") or extraction.get("date"),
                    "amount": -float(tot),
                    "description": desc,
                    "reference": extraction.get("invoice_number") or extraction.get("receipt_number"),
                    "category": AnalyticsService.categorize_description(desc),
                })
        return results

    @staticmethod
    def _format_ofx_date(raw_date: Optional[str]) -> Optional[str]:
        if not raw_date:
            return None
        clean = re.sub(r"[^0-9]", "", str(raw_date))
        if len(clean) >= 8:
            return f"{clean[:8]}120000"
        return None

    @staticmethod
    def _format_qif_date(raw_date: Optional[str]) -> str:
        if not raw_date:
            return datetime.now(timezone.utc).strftime("%m/%d/%Y")
        clean = str(raw_date).strip()
        # If YYYY-MM-DD convert to MM/DD/YYYY
        m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", clean)
        if m:
            y, month, d = m.groups()
            return f"{int(month):02d}/{int(d):02d}/{y}"
        return clean
