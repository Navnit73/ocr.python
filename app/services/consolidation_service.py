"""
Consolidation Service for Multi-Month Statement Rollup & Annual Financial Reports.
"""

from collections import defaultdict
from datetime import datetime, timezone
import io
import re
from typing import Any, Dict, List, Optional, Tuple
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.schemas.batch import ConsolidationResponse, MonthlyRollup
from app.services.analytics_service import AnalyticsService, safe_float
from app.services.export_service import ExportService, sanitize_for_formula_injection


def parse_year_month(date_str: Any) -> str:
    """Robust parser for multi-format dates (YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY, DD-Mon-YYYY)."""
    if not date_str:
        return "2026-01"
    raw = str(date_str).strip()
    # 1. YYYY-MM or YYYY/MM or YYYY.MM
    m_iso = re.search(r"(\d{4})[-/.](0?[1-9]|1[0-2])", raw)
    if m_iso:
        return f"{m_iso.group(1)}-{int(m_iso.group(2)):02d}"

    # 2. DD/MM/YYYY or MM/DD/YYYY
    m_slash = re.search(r"(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](\d{4})", raw)
    if m_slash:
        p1, p2, yr = int(m_slash.group(1)), int(m_slash.group(2)), m_slash.group(3)
        # If p1 > 12, p1 is day and p2 is month; otherwise treat p2 as month
        month = p2 if p1 > 12 else p1 if p2 > 12 else p2
        return f"{yr}-{month:02d}"

    # 3. Standard text date formats like 15-Jan-2026 or Jan 15, 2026
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d-%B-%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m")
        except ValueError:
            pass

    return "2026-01"


class ConsolidationService:
    """Merges and consolidates multiple financial extractions into 12-month / annual P&L reports."""

    @classmethod
    def _extract_normalized_transactions(cls, extractions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
        """Extracts and deduplicates transactions across all statements and line items."""
        all_transactions: List[Dict[str, Any]] = []
        seen_tx_hashes = set()
        currency = "INR"

        for ext in extractions:
            if not ext:
                continue
            cur = ext.get("currency")
            if cur:
                currency = str(cur).upper()

            txs = ext.get("transactions") or []
            # Handle invoice / receipt line items if transactions array is absent
            if not txs and ext.get("line_items"):
                inv_date = ext.get("invoice_date") or ext.get("date") or "2026-01-01"
                for item in ext.get("line_items", []):
                    tot = safe_float(item.get("amount")) or safe_float(item.get("total")) or 0.0
                    txs.append({
                        "date": inv_date,
                        "description": item.get("description") or "Item",
                        "reference": ext.get("invoice_number") or ext.get("receipt_number"),
                        "debit": tot,
                        "credit": None,
                        "balance": None,
                    })

            for t in txs:
                d = str(t.get("date") or "2026-01-01")
                deb = safe_float(t.get("debit")) or 0.0
                cred = safe_float(t.get("credit")) or 0.0
                desc = str(t.get("description") or "")
                ref = str(t.get("reference") or "")

                # Deduplication key
                tx_hash = f"{d}_{deb:.2f}_{cred:.2f}_{desc[:20]}_{ref}"
                if tx_hash in seen_tx_hashes:
                    continue
                seen_tx_hashes.add(tx_hash)

                all_transactions.append({
                    "date": t.get("date") or d,
                    "description": desc,
                    "reference": ref,
                    "debit": safe_float(t.get("debit")),
                    "credit": safe_float(t.get("credit")),
                    "balance": safe_float(t.get("balance")),
                    "category": AnalyticsService.categorize_description(desc),
                })

        return all_transactions, currency

    @classmethod
    def consolidate(
        cls,
        extractions: List[Dict[str, Any]],
        consolidation_id: str = "consolidation_report",
        title: str = "Annual Financial Consolidation",
    ) -> ConsolidationResponse:
        """Computes multi-statement consolidated trends, monthly rollups, and macro metrics."""
        all_transactions, currency = cls._extract_normalized_transactions(extractions)

        total_inflow = 0.0
        total_outflow = 0.0
        monthly_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"inflow": 0.0, "outflow": 0.0, "count": 0})

        for t in all_transactions:
            cred = safe_float(t.get("credit")) or 0.0
            deb = safe_float(t.get("debit")) or 0.0
            total_inflow += cred
            total_outflow += deb

            month_key = parse_year_month(t.get("date"))
            monthly_map[month_key]["inflow"] += cred
            monthly_map[month_key]["outflow"] += deb
            monthly_map[month_key]["count"] += 1

        # Sort monthly rollups chronologically
        monthly_trends: List[MonthlyRollup] = []
        for m_key in sorted(monthly_map.keys()):
            inf = monthly_map[m_key]["inflow"]
            outf = monthly_map[m_key]["outflow"]
            monthly_trends.append(
                MonthlyRollup(
                    month=m_key,
                    inflow=round(inf, 2),
                    outflow=round(outf, 2),
                    net_savings=round(inf - outf, 2),
                    transaction_count=monthly_map[m_key]["count"],
                )
            )

        net_savings = round(total_inflow - total_outflow, 2)
        savings_rate = round((net_savings / total_inflow * 100.0), 1) if total_inflow > 0 else 0.0

        ai_insights = [
            f"Consolidated across {len(extractions)} statements containing {len(all_transactions)} deduplicated transactions.",
            f"Total combined inflow: {total_inflow:,.2f} {currency}, total outflow: {total_outflow:,.2f} {currency}.",
            f"Overall net cashflow: {net_savings:+,.2f} {currency} ({savings_rate}% retention rate).",
        ]
        if monthly_trends:
            highest_spend_month = max(monthly_trends, key=lambda m: m.outflow)
            ai_insights.append(f"Peak outflow month was {highest_spend_month.month} with {highest_spend_month.outflow:,.2f} {currency} in debits.")

        return ConsolidationResponse(
            consolidation_id=consolidation_id,
            statement_count=len(extractions),
            total_transactions=len(all_transactions),
            currency=currency,
            consolidated_inflow=round(total_inflow, 2),
            consolidated_outflow=round(total_outflow, 2),
            net_savings=net_savings,
            overall_savings_rate_pct=savings_rate,
            monthly_trends=monthly_trends,
            ai_insights=ai_insights,
        )

    @classmethod
    def generate_consolidated_excel(
        cls,
        consolidation: ConsolidationResponse,
        extractions: List[Dict[str, Any]],
    ) -> bytes:
        """Generates an executive multi-sheet Annual / Multi-Month Consolidation Excel workbook."""
        wb = openpyxl.Workbook()

        # Sheet 1: Annual Executive Summary & Monthly P&L
        ws_sum = wb.active
        ws_sum.title = "Annual Consolidation"
        ws_sum.views.sheetView[0].showGridLines = True

        navy_dark = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
        navy_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        card_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        alt_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        insight_fill = PatternFill(start_color="F0FDF4", end_color="F0FDF4", fill_type="solid")

        title_font = Font(name="Segoe UI", size=14, bold=True, color="FFFFFF")
        section_font = Font(name="Segoe UI", size=11, bold=True, color="0F172A")
        header_font = Font(name="Segoe UI", size=9.5, bold=True, color="FFFFFF")
        regular_font = Font(name="Segoe UI", size=9.5, color="1E293B")
        bold_font = Font(name="Segoe UI", size=9.5, bold=True, color="0F172A")
        green_font = Font(name="Segoe UI", size=12, bold=True, color="059669")
        red_font = Font(name="Segoe UI", size=12, bold=True, color="DC2626")
        blue_font = Font(name="Segoe UI", size=12, bold=True, color="0284C7")
        purple_font = Font(name="Segoe UI", size=12, bold=True, color="7C3AED")

        thin_side = Side(border_style="thin", color="CBD5E1")
        border_all = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        # Header
        ws_sum.merge_cells("A1:G2")
        title_cell = ws_sum["A1"]
        title_cell.value = f"📊 ANNUAL FINANCIAL CONSOLIDATION — {consolidation.statement_count} STATEMENTS"
        title_cell.font = title_font
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        for r in range(1, 3):
            for c in range(1, 8):
                ws_sum.cell(row=r, column=c).fill = navy_dark

        # 4 KPI Cards (Rows 4-6)
        kpis = [
            ("TOTAL COMBINED INFLOW", consolidation.consolidated_inflow, green_font, "A", "B"),
            ("TOTAL COMBINED OUTFLOW", consolidation.consolidated_outflow, red_font, "C", "D"),
            ("NET SAVINGS / CASHFLOW", consolidation.net_savings, blue_font, "E", "E"),
            ("OVERALL SAVINGS RATE", f"{consolidation.overall_savings_rate_pct}%", purple_font, "F", "G"),
        ]
        for title, val, font, sc, ec in kpis:
            ws_sum.merge_cells(f"{sc}4:{ec}4")
            ws_sum.merge_cells(f"{sc}5:{ec}6")

            ws_sum[f"{sc}4"].value = title
            ws_sum[f"{sc}4"].font = Font(name="Segoe UI", size=8.5, bold=True, color="64748B")
            ws_sum[f"{sc}4"].alignment = Alignment(horizontal="center", vertical="center")

            c_v = ws_sum[f"{sc}5"]
            c_v.value = val
            c_v.font = font
            c_v.alignment = Alignment(horizontal="center", vertical="center")
            if isinstance(val, (int, float)):
                c_v.number_format = "#,##0.00"

            start_c = openpyxl.utils.column_index_from_string(sc)
            end_c = openpyxl.utils.column_index_from_string(ec)
            for r in range(4, 7):
                for c in range(start_c, end_c + 1):
                    ws_sum.cell(row=r, column=c).fill = card_fill
                    ws_sum.cell(row=r, column=c).border = border_all

        # Monthly Breakdown Table (Rows 8+)
        ws_sum.cell(row=8, column=1, value="📅 Monthly Cashflow & P&L Breakdown").font = section_font
        m_headers = ["Month", "Total Inflow", "Total Outflow", "Net Cashflow", "Transactions", "Savings %"]
        for c_idx, h in enumerate(m_headers, start=1):
            c = ws_sum.cell(row=9, column=c_idx, value=h)
            c.font = header_font
            c.fill = navy_header
            c.border = border_all
            c.alignment = Alignment(horizontal="center", vertical="center")

        curr_r = 10
        for m in consolidation.monthly_trends:
            ws_sum.cell(row=curr_r, column=1, value=m.month).font = bold_font
            ws_sum.cell(row=curr_r, column=1).alignment = Alignment(horizontal="center")

            c2 = ws_sum.cell(row=curr_r, column=2, value=m.inflow)
            c2.font = regular_font
            c2.number_format = "#,##0.00"

            c3 = ws_sum.cell(row=curr_r, column=3, value=m.outflow)
            c3.font = regular_font
            c3.number_format = "#,##0.00"

            c4 = ws_sum.cell(row=curr_r, column=4, value=m.net_savings)
            c4.font = bold_font
            c4.number_format = "#,##0.00"

            c5 = ws_sum.cell(row=curr_r, column=5, value=m.transaction_count)
            c5.font = regular_font
            c5.alignment = Alignment(horizontal="center")

            rate = round((m.net_savings / m.inflow * 100), 1) if m.inflow > 0 else 0.0
            c6 = ws_sum.cell(row=curr_r, column=6, value=f"{rate}%")
            c6.font = regular_font
            c6.alignment = Alignment(horizontal="center")

            for col in range(1, 7):
                ws_sum.cell(row=curr_r, column=col).border = border_all
                if curr_r % 2 == 1:
                    ws_sum.cell(row=curr_r, column=col).fill = alt_fill
            curr_r += 1

        # Summary row
        ws_sum.cell(row=curr_r, column=1, value="ANNUAL TOTAL").font = bold_font
        ws_sum.cell(row=curr_r, column=2, value=consolidation.consolidated_inflow).font = green_font
        ws_sum.cell(row=curr_r, column=2).number_format = "#,##0.00"
        ws_sum.cell(row=curr_r, column=3, value=consolidation.consolidated_outflow).font = red_font
        ws_sum.cell(row=curr_r, column=3).number_format = "#,##0.00"
        ws_sum.cell(row=curr_r, column=4, value=consolidation.net_savings).font = blue_font
        ws_sum.cell(row=curr_r, column=4).number_format = "#,##0.00"
        ws_sum.cell(row=curr_r, column=5, value=consolidation.total_transactions).font = bold_font
        ws_sum.cell(row=curr_r, column=6, value=f"{consolidation.overall_savings_rate_pct}%").font = bold_font
        for col in range(1, 7):
            ws_sum.cell(row=curr_r, column=col).border = border_all

        # AI Insights Section
        ins_r = curr_r + 2
        ws_sum.cell(row=ins_r, column=1, value="💡 AI Annual Financial Intelligence").font = section_font
        for i_idx, ins in enumerate(consolidation.ai_insights, start=1):
            row_idx = ins_r + i_idx
            ws_sum.merge_cells(f"A{row_idx}:G{row_idx}")
            ins_cell = ws_sum[f"A{row_idx}"]
            ins_cell.value = f"• {ins}"
            ins_cell.font = bold_font
            for c in range(1, 8):
                ws_sum.cell(row=row_idx, column=c).fill = insight_fill
                ws_sum.cell(row=row_idx, column=c).border = border_all

        for col_letter in ["A", "B", "C", "D", "E", "F", "G"]:
            ws_sum.column_dimensions[col_letter].width = 20

        # Sheet 2: Master Consolidated Ledger
        ws_ledg = wb.create_sheet(title="Master Ledger")
        ws_ledg.views.sheetView[0].showGridLines = True
        ws_ledg.merge_cells("A1:G2")
        ws_ledg["A1"].value = "📑 CONSOLIDATED MASTER TRANSACTION LEDGER"
        ws_ledg["A1"].font = title_font
        ws_ledg["A1"].alignment = Alignment(horizontal="center", vertical="center")
        for r in range(1, 3):
            for c in range(1, 8):
                ws_ledg.cell(row=r, column=c).fill = navy_header

        headers = ["Date", "Category", "Description", "Reference", "Debit (-)", "Credit (+)", "Balance"]
        for c_idx, h in enumerate(headers, start=1):
            c = ws_ledg.cell(row=4, column=c_idx, value=h)
            c.font = header_font
            c.fill = navy_dark
            c.border = border_all
            c.alignment = Alignment(horizontal="center", vertical="center")

        all_txs, _ = cls._extract_normalized_transactions(extractions)
        l_row = 5
        tot_deb = 0.0
        tot_cred = 0.0

        for r_idx, t in enumerate(all_txs):
            deb = t.get("debit")
            cred = t.get("credit")
            bal = t.get("balance")
            desc = str(t.get("description") or "-")
            cat = str(t.get("category") or AnalyticsService.categorize_description(desc))

            c_date = ws_ledg.cell(row=l_row, column=1, value=sanitize_for_formula_injection(t.get("date") or "-"))
            c_date.alignment = Alignment(horizontal="center", vertical="center")
            c_date.font = regular_font

            c_cat = ws_ledg.cell(row=l_row, column=2, value=sanitize_for_formula_injection(cat))
            c_cat.alignment = Alignment(horizontal="center", vertical="center")
            c_cat.font = regular_font

            c_desc = ws_ledg.cell(row=l_row, column=3, value=sanitize_for_formula_injection(desc))
            c_desc.alignment = Alignment(horizontal="left", vertical="center")
            c_desc.font = regular_font

            c_ref = ws_ledg.cell(row=l_row, column=4, value=sanitize_for_formula_injection(t.get("reference") or "-"))
            c_ref.alignment = Alignment(horizontal="center", vertical="center")
            c_ref.font = regular_font

            c_deb = ws_ledg.cell(row=l_row, column=5, value=deb if deb is not None else "")
            c_deb.alignment = Alignment(horizontal="right", vertical="center")
            if isinstance(deb, (int, float)):
                c_deb.number_format = "#,##0.00"
                c_deb.font = red_font
                tot_deb += float(deb)
            else:
                c_deb.font = regular_font

            c_cred = ws_ledg.cell(row=l_row, column=6, value=cred if cred is not None else "")
            c_cred.alignment = Alignment(horizontal="right", vertical="center")
            if isinstance(cred, (int, float)):
                c_cred.number_format = "#,##0.00"
                c_cred.font = green_font
                tot_cred += float(cred)
            else:
                c_cred.font = regular_font

            c_bal = ws_ledg.cell(row=l_row, column=7, value=bal if bal is not None else "")
            c_bal.alignment = Alignment(horizontal="right", vertical="center")
            if isinstance(bal, (int, float)):
                c_bal.number_format = "#,##0.00"
                c_bal.font = bold_font
            else:
                c_bal.font = regular_font

            fill_row = alt_fill if r_idx % 2 == 1 else PatternFill(fill_type=None)
            for c in range(1, 8):
                ws_ledg.cell(row=l_row, column=c).border = border_all
                if r_idx % 2 == 1:
                    ws_ledg.cell(row=l_row, column=c).fill = fill_row
            l_row += 1

        # Summary Totals Row in Master Ledger
        if all_txs:
            ws_ledg.cell(row=l_row, column=1, value="TOTAL SUMMARY").font = bold_font
            ws_ledg.cell(row=l_row, column=1).alignment = Alignment(horizontal="left", vertical="center")
            
            c_sum_deb = ws_ledg.cell(row=l_row, column=5, value=round(tot_deb, 2))
            c_sum_deb.font = red_font
            c_sum_deb.number_format = "#,##0.00"
            c_sum_deb.alignment = Alignment(horizontal="right", vertical="center")

            c_sum_cred = ws_ledg.cell(row=l_row, column=6, value=round(tot_cred, 2))
            c_sum_cred.font = green_font
            c_sum_cred.number_format = "#,##0.00"
            c_sum_cred.alignment = Alignment(horizontal="right", vertical="center")

            double_bottom = Side(border_style="double", color="0F172A")
            thin_top = Side(border_style="thin", color="0F172A")
            tot_border = Border(left=thin_side, right=thin_side, top=thin_top, bottom=double_bottom)
            total_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

            for c in range(1, 8):
                cell_sum = ws_ledg.cell(row=l_row, column=c)
                cell_sum.border = tot_border
                cell_sum.fill = total_fill

        for col_idx in range(1, 8):
            col_letter = get_column_letter(col_idx)
            max_len = 0
            for row_idx in range(4, ws_ledg.max_row + 1):
                val_str = str(ws_ledg.cell(row=row_idx, column=col_idx).value or "")
                max_len = max(max_len, len(val_str))
            ws_ledg.column_dimensions[col_letter].width = max(min(max_len + 4, 45), 15)

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
