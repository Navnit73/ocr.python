"""
Upgraded Export Service with Executive Dashboards, AI Expense Categorization, and World-Class PDF/Excel UI/UX.
"""

import csv
from datetime import datetime, timezone
import io
from typing import Any, Dict, List, Optional, Tuple
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Drawing, Rect

from app.schemas.export import ExportFormatEnum
from app.services.analytics_service import AnalyticsService, FinancialAnalytics


def create_progress_bar(percentage: float, width: float = 70, height: float = 7, color_hex: str = "#4F46E5") -> Drawing:
    """Creates a sleek horizontal micro progress bar for category visual share."""
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#E2E8F0"), strokeColor=None, rx=3.5, ry=3.5))
    fill_w = max(0.0, min(width, (percentage / 100.0) * width))
    if fill_w > 0:
        d.add(Rect(0, 0, fill_w, height, fillColor=colors.HexColor(color_hex), strokeColor=None, rx=3.5, ry=3.5))
    return d


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and render 'Page X of Y', header rules, and branding footers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int):
        self.saveState()

        # Running Header on Page 2+
        if self._pageNumber > 1:
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawString(28, 765, "AI OCR Advance • Financial Audit & Verification Summary")
            self.drawRightString(584, 765, f"Page {self._pageNumber} of {page_count}")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.6)
            self.line(28, 758, 584, 758)

        # Bottom Footer on All Pages
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))

        # Footer divider line
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.75)
        self.line(28, 28, 584, 28)

        # Footer branding & metadata
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.drawString(28, 16, f"🔒 AI OCR Stateless Engine • Confidential Financial Audit • {now_str}")
        self.drawRightString(584, 16, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


def sanitize_for_formula_injection(val: Any) -> Any:
    """
    Neutralizes CSV/Excel formula injection (e.g. =cmd|' /C calc'!A0, @SUM, +...).
    Prepends a single quote to non-numeric strings starting with dangerous formula characters.
    """
    if isinstance(val, str):
        val_str = val.strip()
        if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
            try:
                float(val_str)
                return val
            except ValueError:
                return "'" + val
    return val


class ExportService:
    """Generates styled Excel, CSV, and PDF documents from extraction data."""

    @classmethod
    def generate_export(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Optional[Dict[str, Any]],
        export_format: str = "xlsx",
        raw_text: str = "",
    ) -> Tuple[bytes, str, str]:
        """
        Main export dispatcher.
        Returns: (file_bytes, media_type, filename)
        """
        import re
        safe_doc_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(doc_id)) or "document"
        safe_doc_type = re.sub(r"[^a-zA-Z0-9_]", "", str(document_type)) or "export"
        fmt = export_format.lower().strip()
        data = extraction or {}
        analytics = AnalyticsService.analyze(document_type, data)

        if fmt in [ExportFormatEnum.XLSX.value, ExportFormatEnum.EXCEL.value]:
            file_bytes = cls.generate_excel(safe_doc_id, document_type, data, analytics, raw_text)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"{safe_doc_type}_{safe_doc_id}.xlsx"
        elif fmt == ExportFormatEnum.CSV.value:
            file_bytes = cls.generate_csv(safe_doc_id, document_type, data, analytics, raw_text)
            media_type = "text/csv"
            filename = f"{safe_doc_type}_{safe_doc_id}.csv"
        elif fmt == ExportFormatEnum.PDF.value:
            file_bytes = cls.generate_pdf(safe_doc_id, document_type, data, analytics, raw_text)
            media_type = "application/pdf"
            filename = f"{safe_doc_type}_{safe_doc_id}.pdf"
        elif fmt == ExportFormatEnum.OFX.value:
            from app.services.accounting_export_service import AccountingExportService
            file_bytes = AccountingExportService.generate_ofx(safe_doc_id, document_type, data, is_qbo=False)
            media_type = "application/x-ofx"
            filename = f"{safe_doc_type}_{safe_doc_id}.ofx"
        elif fmt == ExportFormatEnum.QBO.value:
            from app.services.accounting_export_service import AccountingExportService
            file_bytes = AccountingExportService.generate_ofx(safe_doc_id, document_type, data, is_qbo=True)
            media_type = "application/vnd.intu.qbo"
            filename = f"{safe_doc_type}_{safe_doc_id}.qbo"
        elif fmt == ExportFormatEnum.QIF.value:
            from app.services.accounting_export_service import AccountingExportService
            file_bytes = AccountingExportService.generate_qif(safe_doc_id, document_type, data)
            media_type = "application/x-qif"
            filename = f"{safe_doc_type}_{safe_doc_id}.qif"
        else:
            raise ValueError(f"Unsupported export format '{export_format}'. Choose 'xlsx', 'csv', 'pdf', 'ofx', 'qbo', or 'qif'.")

        return file_bytes, media_type, filename

    # =========================================================================
    # 1. PREMIUM MULTI-SHEET EXCEL EXPORT (openpyxl)
    # =========================================================================
    @classmethod
    def generate_excel(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        analytics: Optional[FinancialAnalytics] = None,
        raw_text: str = "",
    ) -> bytes:
        """Creates an executive-level multi-sheet Excel workbook with Dashboard & Ledger."""
        if analytics is None:
            analytics = AnalyticsService.analyze(document_type, extraction)

        wb = openpyxl.Workbook()

        # Styles definition
        navy_dark = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
        navy_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        card_fill_green = PatternFill(start_color="ECFDF5", end_color="ECFDF5", fill_type="solid")
        card_fill_red = PatternFill(start_color="FFF1F2", end_color="FFF1F2", fill_type="solid")
        card_fill_blue = PatternFill(start_color="F0F9FF", end_color="F0F9FF", fill_type="solid")
        card_fill_purple = PatternFill(start_color="FAF5FF", end_color="FAF5FF", fill_type="solid")
        card_fill_neutral = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        alt_row_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        insight_fill = PatternFill(start_color="F0FDF4", end_color="F0FDF4", fill_type="solid")
        total_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

        title_font = Font(name="Segoe UI", size=14, bold=True, color="FFFFFF")
        section_font = Font(name="Segoe UI", size=11, bold=True, color="0F172A")
        kpi_title_font = Font(name="Segoe UI", size=8.5, bold=True, color="64748B")
        kpi_val_green = Font(name="Segoe UI", size=13, bold=True, color="059669")
        kpi_val_red = Font(name="Segoe UI", size=13, bold=True, color="DC2626")
        kpi_val_blue = Font(name="Segoe UI", size=13, bold=True, color="0284C7")
        kpi_val_purple = Font(name="Segoe UI", size=13, bold=True, color="7C3AED")
        header_font = Font(name="Segoe UI", size=9.5, bold=True, color="FFFFFF")
        regular_font = Font(name="Segoe UI", size=9.5, color="1E293B")
        bold_font = Font(name="Segoe UI", size=9.5, bold=True, color="0F172A")
        credit_font = Font(name="Segoe UI", size=9.5, bold=True, color="059669")
        debit_font = Font(name="Segoe UI", size=9.5, color="DC2626")

        thin_side = Side(border_style="thin", color="CBD5E1")
        border_all = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
        double_bottom = Side(border_style="double", color="0F172A")
        thin_top = Side(border_style="thin", color="0F172A")
        total_border = Border(left=thin_side, right=thin_side, top=thin_top, bottom=double_bottom)

        # ---------------------------------------------------------------------
        # SHEET 1: EXECUTIVE DASHBOARD
        # ---------------------------------------------------------------------
        ws_dash = wb.active
        ws_dash.title = "Executive Dashboard"
        ws_dash.views.sheetView[0].showGridLines = True

        # Header Banner
        ws_dash.merge_cells("A1:G2")
        title_cell = ws_dash["A1"]
        title_cell.value = f"📊 {document_type.replace('_', ' ').upper()} - FINANCIAL AUDIT & DASHBOARD"
        title_cell.font = title_font
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        for r in range(1, 3):
            for c in range(1, 8):
                ws_dash.cell(row=r, column=c).fill = navy_dark

        # Metadata Row (Rows 4-5)
        meta_items = cls._get_metadata_summary(document_type, extraction, doc_id)
        if meta_items:
            for col_idx, (label, val) in enumerate(meta_items[:4], start=1):
                cell_lbl = ws_dash.cell(row=4, column=col_idx, value=label.upper())
                cell_lbl.font = kpi_title_font
                cell_lbl.alignment = Alignment(horizontal="left", vertical="center")
                cell_v = ws_dash.cell(row=5, column=col_idx, value=val)
                cell_v.font = bold_font
                cell_v.alignment = Alignment(horizontal="left", vertical="center")

        # 4 Document-Specific KPI Cards (Rows 7-9)
        cards = cls._get_excel_kpi_cards(
            document_type,
            extraction,
            analytics,
            kpi_val_green,
            kpi_val_red,
            kpi_val_blue,
            kpi_val_purple,
            [card_fill_green, card_fill_red, card_fill_blue, card_fill_purple, card_fill_neutral],
        )

        col_ranges = [("A", "B"), ("C", "D"), ("E", "E"), ("F", "G")]
        for idx, (title, val, font, fill) in enumerate(cards):
            sc, ec = col_ranges[idx]
            ws_dash.merge_cells(f"{sc}7:{ec}7")
            ws_dash.merge_cells(f"{sc}8:{ec}9")

            c_title = ws_dash[f"{sc}7"]
            c_title.value = title
            c_title.font = kpi_title_font
            c_title.alignment = Alignment(horizontal="center", vertical="center")

            c_val = ws_dash[f"{sc}8"]
            c_val.value = val
            c_val.font = font
            c_val.alignment = Alignment(horizontal="center", vertical="center")
            if isinstance(val, (int, float)):
                c_val.number_format = "#,##0.00"

            start_c = openpyxl.utils.column_index_from_string(sc)
            end_c = openpyxl.utils.column_index_from_string(ec)
            for r in range(7, 10):
                for c in range(start_c, end_c + 1):
                    ws_dash.cell(row=r, column=c).fill = fill
                    ws_dash.cell(row=r, column=c).border = border_all

        # Expense Category Breakdown Table & Subscriptions Card (Rows 11+)
        curr_r = 11
        ws_dash.cell(row=curr_r, column=1, value="📁 Expense Category Breakdown").font = section_font
        ws_dash.cell(row=curr_r, column=6, value="🔁 Recurring Subscriptions").font = section_font
        curr_r += 1

        cat_headers = ["Category", "Amount", "% Share", "Transactions"]
        for c_idx, h in enumerate(cat_headers, start=1):
            c = ws_dash.cell(row=curr_r, column=c_idx, value=h)
            c.font = header_font
            c.fill = navy_header
            c.border = border_all
            c.alignment = Alignment(horizontal="center", vertical="center")

        ws_dash.cell(row=curr_r, column=6, value="Service / Merchant").font = header_font
        ws_dash.cell(row=curr_r, column=6).fill = navy_header
        ws_dash.cell(row=curr_r, column=6).border = border_all
        ws_dash.cell(row=curr_r, column=7, value="Amount").font = header_font
        ws_dash.cell(row=curr_r, column=7).fill = navy_header
        ws_dash.cell(row=curr_r, column=7).border = border_all

        cat_start_r = curr_r + 1
        r_cat = cat_start_r
        if analytics.categories:
            for cat in analytics.categories:
                ws_dash.cell(row=r_cat, column=1, value=cat.category).font = regular_font
                c2 = ws_dash.cell(row=r_cat, column=2, value=cat.amount)
                c2.font = regular_font
                c2.number_format = "#,##0.00"
                c3 = ws_dash.cell(row=r_cat, column=3, value=f"{cat.percentage}%")
                c3.font = regular_font
                c3.alignment = Alignment(horizontal="center")
                c4 = ws_dash.cell(row=r_cat, column=4, value=cat.count)
                c4.font = regular_font
                c4.alignment = Alignment(horizontal="center")

                for col in range(1, 5):
                    ws_dash.cell(row=r_cat, column=col).border = border_all
                    if r_cat % 2 == 0:
                        ws_dash.cell(row=r_cat, column=col).fill = alt_row_fill
                r_cat += 1
        else:
            ws_dash.cell(row=r_cat, column=1, value="No categorical expenses").font = regular_font
            for col in range(1, 5):
                ws_dash.cell(row=r_cat, column=col).border = border_all
            r_cat += 1

        r_sub = cat_start_r
        if analytics.subscriptions:
            for sub in analytics.subscriptions:
                ws_dash.cell(row=r_sub, column=6, value=sub.merchant).font = regular_font
                c_amt = ws_dash.cell(row=r_sub, column=7, value=sub.amount)
                c_amt.font = bold_font
                c_amt.number_format = "#,##0.00"
                for col in [6, 7]:
                    ws_dash.cell(row=r_sub, column=col).fill = card_fill_purple
                    ws_dash.cell(row=r_sub, column=col).border = border_all
                r_sub += 1
        else:
            ws_dash.cell(row=r_sub, column=6, value="No recurring subscriptions").font = regular_font
            ws_dash.cell(row=r_sub, column=7, value="-").font = regular_font
            for col in [6, 7]:
                ws_dash.cell(row=r_sub, column=col).border = border_all
            r_sub += 1

        # AI Insights Section
        insights_start_row = max(r_cat, r_sub) + 2
        ws_dash.cell(row=insights_start_row, column=1, value="💡 AI Financial Insights & Audit Summary").font = section_font
        for i_idx, insight in enumerate(analytics.ai_insights, start=1):
            row_idx = insights_start_row + i_idx
            ws_dash.merge_cells(f"A{row_idx}:G{row_idx}")
            ins_cell = ws_dash[f"A{row_idx}"]
            ins_cell.value = f"• {insight}"
            ins_cell.font = bold_font
            ins_cell.alignment = Alignment(horizontal="left", vertical="center")
            for c in range(1, 8):
                ws_dash.cell(row=row_idx, column=c).fill = insight_fill
                ws_dash.cell(row=row_idx, column=c).border = border_all

        col_widths_dash = {"A": 26, "B": 16, "C": 14, "D": 14, "E": 20, "F": 24, "G": 16}
        for col_letter, width in col_widths_dash.items():
            ws_dash.column_dimensions[col_letter].width = width

        # ---------------------------------------------------------------------
        # SHEET 2: ITEMIZED LEDGER / LINE ITEMS
        # ---------------------------------------------------------------------
        ws_ledger = wb.create_sheet(title="Itemized Ledger")
        ws_ledger.views.sheetView[0].showGridLines = True

        headers, rows = cls._get_table_data_with_category(document_type, extraction)
        max_col_idx = max(len(headers), 6) if headers else 7
        last_letter = get_column_letter(max_col_idx)

        ws_ledger.merge_cells(f"A1:{last_letter}2")
        t_cell = ws_ledger["A1"]
        t_cell.value = f"📑 {document_type.replace('_', ' ').upper()} - ITEMIZED TRANSACTION LEDGER"
        t_cell.font = title_font
        t_cell.alignment = Alignment(horizontal="center", vertical="center")
        for r in range(1, 3):
            for c in range(1, max_col_idx + 1):
                ws_ledger.cell(row=r, column=c).fill = navy_header

        if headers:
            # Header Row
            for c_idx, h in enumerate(headers, start=1):
                c = ws_ledger.cell(row=4, column=c_idx, value=h)
                c.font = header_font
                c.fill = navy_dark
                c.alignment = Alignment(horizontal="center", vertical="center")
                c.border = border_all

            # Data Rows
            ledger_row = 5
            total_debits = 0.0
            total_credits = 0.0
            total_amount_sum = 0.0

            for r_idx, r in enumerate(rows):
                fill = alt_row_fill if r_idx % 2 == 1 else PatternFill(fill_type=None)
                for col_idx, val in enumerate(r, start=1):
                    c = ws_ledger.cell(row=ledger_row, column=col_idx, value=val)
                    c.border = border_all
                    c.fill = fill

                    h_name = headers[col_idx - 1]
                    if isinstance(val, (int, float)):
                        c.number_format = "#,##0.00"
                        c.alignment = Alignment(horizontal="right", vertical="center")
                        if h_name == "Credit":
                            c.font = credit_font
                            total_credits += float(val)
                        elif h_name == "Debit":
                            c.font = debit_font
                            total_debits += float(val)
                        elif h_name in ["Amount", "Total"]:
                            c.font = bold_font
                            total_amount_sum += float(val)
                        else:
                            c.font = regular_font
                    elif h_name in ["Date", "Category", "Quantity", "Tax Rate", "Reference"]:
                        c.alignment = Alignment(horizontal="center", vertical="center")
                        c.font = regular_font
                    else:
                        c.alignment = Alignment(horizontal="left", vertical="center")
                        c.font = regular_font

                ledger_row += 1

            # Summary Totals Row
            if len(rows) > 0:
                ws_ledger.cell(row=ledger_row, column=1, value="TOTAL SUMMARY").font = bold_font
                ws_ledger.cell(row=ledger_row, column=1).alignment = Alignment(horizontal="left", vertical="center")

                for c_idx, h in enumerate(headers, start=1):
                    cell_tot = ws_ledger.cell(row=ledger_row, column=c_idx)
                    cell_tot.border = total_border
                    cell_tot.fill = total_fill

                    if h == "Debit":
                        cell_tot.value = total_debits
                        cell_tot.font = debit_font
                        cell_tot.number_format = "#,##0.00"
                        cell_tot.alignment = Alignment(horizontal="right", vertical="center")
                    elif h == "Credit":
                        cell_tot.value = total_credits
                        cell_tot.font = credit_font
                        cell_tot.number_format = "#,##0.00"
                        cell_tot.alignment = Alignment(horizontal="right", vertical="center")
                    elif h == "Balance" and c_idx == len(headers):
                        close_bal = extraction.get("closing_balance")
                        if close_bal is not None:
                            cell_tot.value = close_bal
                        elif rows and isinstance(rows[-1][-1], (int, float)):
                            cell_tot.value = rows[-1][-1]
                        cell_tot.font = bold_font
                        cell_tot.number_format = "#,##0.00"
                        cell_tot.alignment = Alignment(horizontal="right", vertical="center")
                    elif h in ["Amount", "Total"] and c_idx == len(headers):
                        doc_total = extraction.get("total")
                        cell_tot.value = doc_total if doc_total is not None else total_amount_sum
                        cell_tot.font = bold_font
                        cell_tot.number_format = "#,##0.00"
                        cell_tot.alignment = Alignment(horizontal="right", vertical="center")

        elif raw_text:
            ws_ledger.cell(row=4, column=1, value="Extracted Content").font = header_font
            ws_ledger.cell(row=4, column=1).fill = navy_dark
            ws_ledger.cell(row=4, column=1).border = border_all
            for idx, line in enumerate(raw_text.splitlines()[:100], start=5):
                cell_line = ws_ledger.cell(row=idx, column=1, value=line)
                cell_line.font = regular_font
                cell_line.border = border_all

        for col_idx in range(1, ws_ledger.max_column + 1):
            col_letter = get_column_letter(col_idx)
            max_len = 0
            for row_idx in range(4, ws_ledger.max_row + 1):
                val = str(ws_ledger.cell(row=row_idx, column=col_idx).value or "")
                max_len = max(max_len, len(val))
            ws_ledger.column_dimensions[col_letter].width = max(min(max_len + 4, 45), 14)

        output = io.BytesIO()
        wb.save(output)
        return output.getvalue()

    @staticmethod
    def _get_excel_kpi_cards(
        doc_type: str,
        extraction: Dict[str, Any],
        analytics: FinancialAnalytics,
        kpi_green: Font,
        kpi_red: Font,
        kpi_blue: Font,
        kpi_purple: Font,
        fills: List[PatternFill],
    ) -> List[Tuple[str, Any, Font, PatternFill]]:
        """Returns 4 customized KPI cards per document type for Excel."""
        if doc_type == "bank_statement":
            return [
                ("TOTAL INFLOW / CREDITS", analytics.total_inflow, kpi_green, fills[0]),
                ("TOTAL OUTFLOW / DEBITS", analytics.total_outflow, kpi_red, fills[1]),
                ("NET CASHFLOW / SAVINGS", analytics.net_savings, kpi_blue, fills[2]),
                ("ACTIVE SUBSCRIPTIONS", f"{analytics.subscription_count} ({analytics.subscription_total:,.2f})", kpi_purple, fills[3]),
            ]
        elif doc_type == "invoice":
            subtotal = extraction.get("subtotal") or 0.0
            tax = extraction.get("tax") or 0.0
            total = extraction.get("total") or (subtotal + tax)
            return [
                ("SUBTOTAL (EXCL. TAX)", subtotal, kpi_blue, fills[2]),
                ("TOTAL TAX / VAT", tax, kpi_purple, fills[3]),
                ("INVOICE TOTAL DUE", total, kpi_green, fills[0]),
                ("EXTRACTION STATUS", "VERIFIED", kpi_green, fills[4]),
            ]
        elif doc_type == "receipt":
            subtotal = extraction.get("subtotal") or 0.0
            tax = extraction.get("tax") or 0.0
            total = extraction.get("total") or (subtotal + tax)
            merchant = extraction.get("merchant") or "Merchant"
            cat = AnalyticsService.categorize_description(str(merchant))
            return [
                ("TOTAL PAID", total, kpi_green, fills[0]),
                ("SUBTOTAL", subtotal, kpi_blue, fills[2]),
                ("TAX / DUTY", tax, kpi_purple, fills[3]),
                ("EXPENSE CATEGORY", cat, kpi_blue, fills[4]),
            ]
        else:
            return [
                ("PROCESSING STATUS", "SUCCESS", kpi_green, fills[0]),
                ("EXTRACTED ATTRIBUTES", len(extraction), kpi_blue, fills[2]),
                ("AI VERIFICATION", "PASSED", kpi_green, fills[4]),
                ("EXPORT FORMAT", "MULTI-SHEET", kpi_purple, fills[3]),
            ]

    # =========================================================================
    # 2. CSV EXPORT
    # =========================================================================
    @classmethod
    def generate_csv(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        analytics: Optional[FinancialAnalytics] = None,
        raw_text: str = "",
    ) -> bytes:
        """Generates RFC 4180 CSV export with auto-categorization."""
        if analytics is None:
            analytics = AnalyticsService.analyze(document_type, extraction)

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["# Document ID", doc_id])
        writer.writerow(["# Document Type", document_type])
        writer.writerow(["# Total Inflow", analytics.total_inflow])
        writer.writerow(["# Total Outflow", analytics.total_outflow])
        writer.writerow(["# Net Savings", analytics.net_savings])
        writer.writerow(["# Active Subscriptions", analytics.subscription_count])

        metadata_items = cls._get_metadata_summary(document_type, extraction, doc_id)
        for label, val in metadata_items:
            writer.writerow([f"# {label}", val])
        writer.writerow([])

        # Table data
        headers, rows = cls._get_table_data_with_category(document_type, extraction)
        if headers:
            writer.writerow(headers)
            for r in rows:
                writer.writerow(r)
        else:
            writer.writerow(["Key", "Value"])
            for k, v in extraction.items():
                if not isinstance(v, (list, dict)):
                    writer.writerow([k, v])

        return output.getvalue().encode("utf-8-sig")

    # =========================================================================
    # 3. ULTRA-PREMIUM EXECUTIVE PDF REPORT (ReportLab)
    # =========================================================================
    @classmethod
    def generate_pdf(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        analytics: Optional[FinancialAnalytics] = None,
        raw_text: str = "",
    ) -> bytes:
        """Generates a world-class, fintech-styled Executive PDF report with two-pass canvas."""
        if analytics is None:
            analytics = AnalyticsService.analyze(document_type, extraction)

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=letter,
            rightMargin=28,
            leftMargin=28,
            topMargin=28,
            bottomMargin=38,
        )

        styles = cls._init_pdf_styles()
        story: List[Any] = []

        # 1. Top Header
        story.extend(cls._build_header_flowables(doc_id, document_type, extraction, styles))
        story.append(Spacer(1, 8))

        # 2. Document-Specific Blocks
        if document_type == "bank_statement":
            story.extend(cls._build_bank_statement_story(doc_id, extraction, analytics, styles))
        elif document_type == "invoice":
            story.extend(cls._build_invoice_story(doc_id, extraction, analytics, styles))
        elif document_type == "receipt":
            story.extend(cls._build_receipt_story(doc_id, extraction, analytics, styles))
        else:
            story.extend(cls._build_general_document_story(doc_id, document_type, extraction, analytics, raw_text, styles))

        doc.build(story, canvasmaker=NumberedCanvas)
        return buf.getvalue()

    # =========================================================================
    # PDF STYLE SYSTEM
    # =========================================================================
    @staticmethod
    def _init_pdf_styles() -> Dict[str, ParagraphStyle]:
        styles = getSampleStyleSheet()
        custom = {}

        custom["Badge"] = ParagraphStyle(
            "Badge",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=9,
            textColor=colors.HexColor("#4F46E5"),
            fontName="Helvetica-Bold",
        )
        custom["Title"] = ParagraphStyle(
            "Title",
            parent=styles["Normal"],
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#0F172A"),
            fontName="Helvetica-Bold",
        )
        custom["Subtitle"] = ParagraphStyle(
            "Subtitle",
            parent=styles["Normal"],
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#64748B"),
            fontName="Helvetica",
        )
        custom["MetaRight"] = ParagraphStyle(
            "MetaRight",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#475569"),
            fontName="Helvetica",
            alignment=2,
        )
        custom["SectionTitle"] = ParagraphStyle(
            "SectionTitle",
            parent=styles["Normal"],
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#0F172A"),
            fontName="Helvetica-Bold",
        )

        custom["MetaCardLabel"] = ParagraphStyle(
            "MetaCardLabel",
            fontSize=6.5,
            leading=8,
            textColor=colors.HexColor("#64748B"),
            fontName="Helvetica-Bold",
        )
        custom["MetaCardValue"] = ParagraphStyle(
            "MetaCardValue",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#0F172A"),
            fontName="Helvetica-Bold",
        )
        custom["MetaCardSub"] = ParagraphStyle(
            "MetaCardSub",
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#64748B"),
            fontName="Helvetica",
        )

        custom["KPILabelG"] = ParagraphStyle("KPILabelG", fontSize=6.5, leading=8, textColor=colors.HexColor("#065F46"), fontName="Helvetica-Bold", alignment=1)
        custom["KPIValG"] = ParagraphStyle("KPIValG", fontSize=12, leading=14, textColor=colors.HexColor("#059669"), fontName="Helvetica-Bold", alignment=1)
        custom["KPISubG"] = ParagraphStyle("KPISubG", fontSize=6.5, leading=8, textColor=colors.HexColor("#047857"), fontName="Helvetica", alignment=1)

        custom["KPILabelR"] = ParagraphStyle("KPILabelR", fontSize=6.5, leading=8, textColor=colors.HexColor("#9F1239"), fontName="Helvetica-Bold", alignment=1)
        custom["KPIValR"] = ParagraphStyle("KPIValR", fontSize=12, leading=14, textColor=colors.HexColor("#E11D48"), fontName="Helvetica-Bold", alignment=1)
        custom["KPISubR"] = ParagraphStyle("KPISubR", fontSize=6.5, leading=8, textColor=colors.HexColor("#BE123C"), fontName="Helvetica", alignment=1)

        custom["KPILabelB"] = ParagraphStyle("KPILabelB", fontSize=6.5, leading=8, textColor=colors.HexColor("#0369A1"), fontName="Helvetica-Bold", alignment=1)
        custom["KPIValB"] = ParagraphStyle("KPIValB", fontSize=12, leading=14, textColor=colors.HexColor("#0284C7"), fontName="Helvetica-Bold", alignment=1)
        custom["KPISubB"] = ParagraphStyle("KPISubB", fontSize=6.5, leading=8, textColor=colors.HexColor("#0284C7"), fontName="Helvetica", alignment=1)

        custom["KPILabelP"] = ParagraphStyle("KPILabelP", fontSize=6.5, leading=8, textColor=colors.HexColor("#6D28D9"), fontName="Helvetica-Bold", alignment=1)
        custom["KPIValP"] = ParagraphStyle("KPIValP", fontSize=12, leading=14, textColor=colors.HexColor("#7C3AED"), fontName="Helvetica-Bold", alignment=1)
        custom["KPISubP"] = ParagraphStyle("KPISubP", fontSize=6.5, leading=8, textColor=colors.HexColor("#6D28D9"), fontName="Helvetica", alignment=1)

        custom["TH"] = ParagraphStyle("TH", fontSize=7.5, leading=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=0)
        custom["THCenter"] = ParagraphStyle("THCenter", fontSize=7.5, leading=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=1)
        custom["THRight"] = ParagraphStyle("THRight", fontSize=7.5, leading=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=2)

        custom["TD"] = ParagraphStyle("TD", fontSize=7, leading=9.5, textColor=colors.HexColor("#1E293B"), fontName="Helvetica")
        custom["TDCenter"] = ParagraphStyle("TDCenter", fontSize=7, leading=9.5, textColor=colors.HexColor("#475569"), fontName="Helvetica", alignment=1)
        custom["TDRight"] = ParagraphStyle("TDRight", fontSize=7, leading=9.5, textColor=colors.HexColor("#0F172A"), fontName="Helvetica-Bold", alignment=2)
        custom["TDCredit"] = ParagraphStyle("TDCredit", fontSize=7, leading=9.5, textColor=colors.HexColor("#059669"), fontName="Helvetica-Bold", alignment=2)
        custom["TDDebit"] = ParagraphStyle("TDDebit", fontSize=7, leading=9.5, textColor=colors.HexColor("#DC2626"), fontName="Helvetica", alignment=2)
        custom["TDCatPill"] = ParagraphStyle("TDCatPill", fontSize=6.5, leading=8, textColor=colors.HexColor("#4338CA"), fontName="Helvetica-Bold")
        custom["TDFooterLeft"] = ParagraphStyle("TDFooterLeft", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#0F172A"), fontName="Helvetica-Bold", alignment=0)
        custom["TDFooterRight"] = ParagraphStyle("TDFooterRight", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#0F172A"), fontName="Helvetica-Bold", alignment=2)

        custom["InsightHeading"] = ParagraphStyle("InsightHeading", fontSize=8.5, leading=11, textColor=colors.HexColor("#065F46"), fontName="Helvetica-Bold")
        custom["InsightBullet"] = ParagraphStyle("InsightBullet", fontSize=7.5, leading=10.5, textColor=colors.HexColor("#1E293B"), fontName="Helvetica")

        return custom

    # =========================================================================
    # HEADER FLOWABLE
    # =========================================================================
    @classmethod
    def _build_header_flowables(cls, doc_id: str, document_type: str, extraction: Dict[str, Any], styles: Dict[str, ParagraphStyle]) -> List[Any]:
        doc_type_clean = document_type.replace("_", " ").upper()
        now_formatted = datetime.now(timezone.utc).strftime("%B %d, %Y")

        entity_name = ""
        if document_type == "bank_statement":
            entity_name = str(extraction.get("bank_name") or "Bank Statement")
        elif document_type == "invoice":
            supplier = extraction.get("supplier")
            if isinstance(supplier, dict) and supplier.get("name"):
                entity_name = f"Invoice • {supplier['name']}"
            else:
                inv_num = extraction.get("invoice_number") or ""
                entity_name = f"Invoice {inv_num}".strip() or "Commercial Invoice"
        elif document_type == "receipt":
            entity_name = str(extraction.get("merchant") or "Merchant Receipt")
        else:
            entity_name = doc_type_clean

        header_left = [
            Paragraph(f"<font color='#4F46E5'><b>●</b></font> <b>{doc_type_clean} EXTRACTION</b>", styles["Badge"]),
            Spacer(1, 2),
            Paragraph(entity_name, styles["Title"]),
            Spacer(1, 2),
            Paragraph("AI-Powered OCR Verification • 3-Layer Financial Integrity Audit", styles["Subtitle"]),
        ]

        header_right = [
            Paragraph(f"<b>Report ID:</b> <font color='#0F172A'>{doc_id}</font>", styles["MetaRight"]),
            Paragraph(f"<b>Generated:</b> {now_formatted}", styles["MetaRight"]),
            Paragraph("<b>Verification:</b> <font color='#059669'><b>✔ Verified Extraction</b></font>", styles["MetaRight"]),
        ]

        header_table = Table([[header_left, header_right]], colWidths=[5.1 * inch, 2.62 * inch])
        header_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )

        divider_table = Table([["", ""]], colWidths=[5.0 * inch, 2.72 * inch], rowHeights=[2.5])
        divider_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#4F46E5")),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#10B981")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ])
        )

        return [header_table, divider_table]

    # =========================================================================
    # BANK STATEMENT STORY BUILDER
    # =========================================================================
    @classmethod
    def _build_bank_statement_story(
        cls,
        doc_id: str,
        extraction: Dict[str, Any],
        analytics: FinancialAnalytics,
        styles: Dict[str, ParagraphStyle],
    ) -> List[Any]:
        story: List[Any] = []

        card1 = [
            Paragraph("TOTAL INFLOW / CREDITS", styles["KPILabelG"]),
            Spacer(1, 2),
            Paragraph(f"+ {analytics.total_inflow:,.2f}", styles["KPIValG"]),
            Spacer(1, 1),
            Paragraph(f"{analytics.transaction_count} transactions recorded", styles["KPISubG"]),
        ]
        card2 = [
            Paragraph("TOTAL OUTFLOW / DEBITS", styles["KPILabelR"]),
            Spacer(1, 2),
            Paragraph(f"- {analytics.total_outflow:,.2f}", styles["KPIValR"]),
            Spacer(1, 1),
            Paragraph("Total Debits categorized", styles["KPISubR"]),
        ]
        card3 = [
            Paragraph("NET CASHFLOW / SAVINGS", styles["KPILabelB"]),
            Spacer(1, 2),
            Paragraph(f"{analytics.net_savings:+,.2f}", styles["KPIValB"]),
            Spacer(1, 1),
            Paragraph(f"Savings Rate: {analytics.savings_rate_pct}%", styles["KPISubB"]),
        ]
        card4 = [
            Paragraph("RECURRING SUBSCRIPTIONS", styles["KPILabelP"]),
            Spacer(1, 2),
            Paragraph(f"{analytics.subscription_count} Active", styles["KPIValP"]),
            Spacer(1, 1),
            Paragraph(f"{analytics.subscription_total:,.2f} / month", styles["KPISubP"]),
        ]

        kpi_table = Table([[card1, card2, card3, card4]], colWidths=[1.88 * inch] * 4)
        kpi_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#ECFDF5")),
                ("BOX", (0, 0), (0, 0), 1, colors.HexColor("#A7F3D0")),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#FFF1F2")),
                ("BOX", (1, 0), (1, 0), 1, colors.HexColor("#FECDD3")),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#F0F9FF")),
                ("BOX", (2, 0), (2, 0), 1, colors.HexColor("#BAE6FD")),
                ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#F5F3FF")),
                ("BOX", (3, 0), (3, 0), 1, colors.HexColor("#DDD6FE")),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])
        )
        story.append(kpi_table)
        story.append(Spacer(1, 8))

        open_bal = extraction.get("opening_balance")
        close_bal = extraction.get("closing_balance")
        cur = extraction.get("currency") or "INR"

        left_card = [
            Paragraph("<b>ACCOUNT INFORMATION</b>", styles["MetaCardLabel"]),
            Spacer(1, 2),
            Paragraph(f"<b>Holder:</b> {extraction.get('account_holder') or 'Not Specified'}", styles["MetaCardValue"]),
            Paragraph(f"<b>Account #:</b> {extraction.get('account_number_masked') or 'XXXX-XXXX'}", styles["MetaCardSub"]),
            Paragraph(f"<b>Period:</b> {extraction.get('statement_period') or 'N/A'} • Currency: {cur}", styles["MetaCardSub"]),
        ]

        delta_bal_str = "N/A"
        if open_bal is not None and close_bal is not None:
            delta = close_bal - open_bal
            delta_bal_str = f"{delta:+,.2f} ({cur})"

        right_card = [
            Paragraph("<b>BALANCE RECONCILIATION</b>", styles["MetaCardLabel"]),
            Spacer(1, 2),
            Paragraph(
                f"Opening: <b>{f'{open_bal:,.2f}' if open_bal is not None else 'N/A'}</b>  →  Closing: <b>{f'{close_bal:,.2f}' if close_bal is not None else 'N/A'}</b>",
                styles["MetaCardValue"],
            ),
            Paragraph(f"Net Position Change: <b>{delta_bal_str}</b>", styles["MetaCardSub"]),
            Paragraph("Reconciliation Status: <font color='#059669'><b>✔ Mathematical Integrity Verified</b></font>", styles["MetaCardSub"]),
        ]

        meta_table = Table([[left_card, right_card]], colWidths=[3.81 * inch, 3.81 * inch])
        meta_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        story.append(meta_table)
        story.append(Spacer(1, 8))

        if analytics.ai_insights:
            insight_flowables = [
                Paragraph("<b>💡 AI Financial Insights & Expense Analysis</b>", styles["InsightHeading"]),
                Spacer(1, 3),
            ]
            for ins in analytics.ai_insights:
                insight_flowables.append(Paragraph(f"• {ins}", styles["InsightBullet"]))
                insight_flowables.append(Spacer(1, 1.5))

            ins_table = Table([[insight_flowables]], colWidths=[7.72 * inch])
            ins_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#86EFAC")),
                    ("LINEBEFORE", (0, 0), (0, -1), 3.5, colors.HexColor("#10B981")),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ])
            )
            story.append(ins_table)
            story.append(Spacer(1, 8))

        if analytics.categories and len(analytics.categories) > 0:
            story.append(Paragraph("<b>📊 Expense Breakdown by Category</b>", styles["SectionTitle"]))
            story.append(Spacer(1, 3))

            palette = ["#4F46E5", "#10B981", "#F59E0B", "#F43F5E", "#8B5CF6", "#0EA5E9", "#14B8A6"]
            cat_table_data = [
                [
                    Paragraph("Category", styles["TH"]),
                    Paragraph("Visual Share", styles["THCenter"]),
                    Paragraph("Amount", styles["THRight"]),
                    Paragraph("% Share", styles["THCenter"]),
                    Paragraph("Tx Count", styles["THCenter"]),
                ]
            ]

            for idx, cat in enumerate(analytics.categories[:6]):
                color_hex = palette[idx % len(palette)]
                bar_drawing = create_progress_bar(cat.percentage, width=80, height=7, color_hex=color_hex)
                cat_table_data.append([
                    Paragraph(f"<font color='{color_hex}'><b>●</b></font> <b>{cat.category}</b>", styles["TD"]),
                    bar_drawing,
                    Paragraph(f"{cat.amount:,.2f}", styles["TDRight"]),
                    Paragraph(f"{cat.percentage}%", styles["TDCenter"]),
                    Paragraph(str(cat.count), styles["TDCenter"]),
                ])

            cat_table = Table(cat_table_data, colWidths=[2.72 * inch, 1.6 * inch, 1.4 * inch, 1.0 * inch, 1.0 * inch])
            cat_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 1), (1, -1), "CENTER"),
                ])
            )
            story.append(cat_table)
            story.append(Spacer(1, 8))

        headers, rows = cls._get_table_data_with_category("bank_statement", extraction)
        if headers and rows:
            story.append(Paragraph("<b>📑 Itemized Transaction Ledger</b>", styles["SectionTitle"]))
            story.append(Spacer(1, 3))

            th_list = [
                Paragraph("Date", styles["THCenter"]),
                Paragraph("Category", styles["TH"]),
                Paragraph("Description", styles["TH"]),
                Paragraph("Reference", styles["THCenter"]),
                Paragraph("Debit (-)", styles["THRight"]),
                Paragraph("Credit (+)", styles["THRight"]),
                Paragraph("Balance", styles["THRight"]),
            ]

            table_data = [th_list]
            total_debits = 0.0
            total_credits = 0.0

            for r in rows:
                date_val, cat_val, desc_val, ref_val, deb_val, cred_val, bal_val = r
                deb_p = Paragraph("-", styles["TDCenter"])
                cred_p = Paragraph("-", styles["TDCenter"])
                bal_p = Paragraph(f"{bal_val:,.2f}" if isinstance(bal_val, (int, float)) else str(bal_val or "-"), styles["TDRight"])

                if isinstance(deb_val, (int, float)) and deb_val > 0:
                    deb_p = Paragraph(f"{deb_val:,.2f}", styles["TDDebit"])
                    total_debits += float(deb_val)
                if isinstance(cred_val, (int, float)) and cred_val > 0:
                    cred_p = Paragraph(f"{cred_val:,.2f}", styles["TDCredit"])
                    total_credits += float(cred_val)

                table_data.append([
                    Paragraph(str(date_val or "-"), styles["TDCenter"]),
                    Paragraph(f"<font color='#4338CA'><b>{cat_val}</b></font>", styles["TDCatPill"]),
                    Paragraph(str(desc_val or "-"), styles["TD"]),
                    Paragraph(str(ref_val or "-"), styles["TDCenter"]),
                    deb_p,
                    cred_p,
                    bal_p,
                ])

            table_data.append([
                Paragraph("<b>TOTALS</b>", styles["TDFooterLeft"]),
                Paragraph("", styles["TD"]),
                Paragraph(f"<b>{len(rows)} Transactions</b>", styles["TDFooterLeft"]),
                Paragraph("", styles["TD"]),
                Paragraph(f"<b>{total_debits:,.2f}</b>", styles["TDDebit"]),
                Paragraph(f"<b>{total_credits:,.2f}</b>", styles["TDCredit"]),
                Paragraph(f"<b>{f'{close_bal:,.2f}' if close_bal is not None else '-'}</b>", styles["TDFooterRight"]),
            ])

            col_widths = [0.85 * inch, 1.25 * inch, 2.0 * inch, 0.85 * inch, 0.92 * inch, 0.92 * inch, 0.93 * inch]
            main_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            main_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
                    ("LINEABOVE", (0, -1), (-1, -1), 1.2, colors.HexColor("#0F172A")),
                    ("PADDING", (0, 0), (-1, -1), 3.5),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ])
            )
            story.append(main_table)

        return story

    # =========================================================================
    # INVOICE STORY BUILDER
    # =========================================================================
    @classmethod
    def _build_invoice_story(
        cls,
        doc_id: str,
        extraction: Dict[str, Any],
        analytics: FinancialAnalytics,
        styles: Dict[str, ParagraphStyle],
    ) -> List[Any]:
        story: List[Any] = []

        subtotal = extraction.get("subtotal") or 0.0
        tax = extraction.get("tax") or 0.0
        total = extraction.get("total") or (subtotal + tax)
        cur = extraction.get("currency") or "USD"

        card1 = [
            Paragraph("SUBTOTAL (EXCL. TAX)", styles["KPILabelB"]),
            Spacer(1, 2),
            Paragraph(f"{subtotal:,.2f}", styles["KPIValB"]),
            Spacer(1, 1),
            Paragraph(f"Currency: {cur}", styles["KPISubB"]),
        ]
        card2 = [
            Paragraph("TOTAL TAX / VAT", styles["KPILabelP"]),
            Spacer(1, 2),
            Paragraph(f"{tax:,.2f}", styles["KPIValP"]),
            Spacer(1, 1),
            Paragraph("Applicable duties & taxes", styles["KPISubP"]),
        ]
        card3 = [
            Paragraph("INVOICE TOTAL DUE", styles["KPILabelG"]),
            Spacer(1, 2),
            Paragraph(f"{total:,.2f}", styles["KPIValG"]),
            Spacer(1, 1),
            Paragraph("Net Payable Amount", styles["KPISubG"]),
        ]
        card4 = [
            Paragraph("INVOICE STATUS", styles["KPILabelG"]),
            Spacer(1, 2),
            Paragraph("VERIFIED", styles["KPIValG"]),
            Spacer(1, 1),
            Paragraph("AI Extracted & Audited", styles["KPISubG"]),
        ]

        kpi_table = Table([[card1, card2, card3, card4]], colWidths=[1.88 * inch] * 4)
        kpi_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#F0F9FF")),
                ("BOX", (0, 0), (0, 0), 1, colors.HexColor("#BAE6FD")),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#F5F3FF")),
                ("BOX", (1, 0), (1, 0), 1, colors.HexColor("#DDD6FE")),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#ECFDF5")),
                ("BOX", (2, 0), (2, 0), 1, colors.HexColor("#A7F3D0")),
                ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#F8FAFC")),
                ("BOX", (3, 0), (3, 0), 1, colors.HexColor("#CBD5E1")),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])
        )
        story.append(kpi_table)
        story.append(Spacer(1, 8))

        supplier = extraction.get("supplier") or {}
        customer = extraction.get("customer") or {}

        supplier_card = [
            Paragraph("<b>BILLED FROM (SUPPLIER)</b>", styles["MetaCardLabel"]),
            Spacer(1, 2),
            Paragraph(f"<b>{supplier.get('name') or 'Supplier N/A'}</b>", styles["MetaCardValue"]),
            Paragraph(f"Address: {supplier.get('address') or 'N/A'}", styles["MetaCardSub"]),
            Paragraph(f"Tax ID / VAT: {supplier.get('tax_id') or 'N/A'}", styles["MetaCardSub"]),
        ]

        customer_card = [
            Paragraph("<b>BILLED TO (CUSTOMER)</b>", styles["MetaCardLabel"]),
            Spacer(1, 2),
            Paragraph(f"<b>{customer.get('name') or 'Customer N/A'}</b>", styles["MetaCardValue"]),
            Paragraph(f"Address: {customer.get('address') or 'N/A'}", styles["MetaCardSub"]),
            Paragraph(f"Tax ID / VAT: {customer.get('tax_id') or 'N/A'}", styles["MetaCardSub"]),
        ]

        entity_table = Table([[supplier_card, customer_card]], colWidths=[3.81 * inch, 3.81 * inch])
        entity_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        story.append(entity_table)
        story.append(Spacer(1, 8))

        meta_bar = [
            [
                Paragraph("<b>Invoice Number</b>", styles["MetaCardLabel"]),
                Paragraph("<b>Invoice Date</b>", styles["MetaCardLabel"]),
                Paragraph("<b>Due Date</b>", styles["MetaCardLabel"]),
                Paragraph("<b>Currency</b>", styles["MetaCardLabel"]),
            ],
            [
                Paragraph(f"<b>{extraction.get('invoice_number') or 'N/A'}</b>", styles["MetaCardValue"]),
                Paragraph(str(extraction.get("invoice_date") or "N/A"), styles["MetaCardValue"]),
                Paragraph(str(extraction.get("due_date") or "Upon Receipt"), styles["MetaCardValue"]),
                Paragraph(str(cur), styles["MetaCardValue"]),
            ],
        ]
        meta_bar_table = Table(meta_bar, colWidths=[1.93 * inch] * 4)
        meta_bar_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#F1F5F9")),
                ("PADDING", (0, 0), (-1, -1), 4),
            ])
        )
        story.append(meta_bar_table)
        story.append(Spacer(1, 8))

        line_items = extraction.get("line_items", [])
        if line_items:
            story.append(Paragraph("<b>📑 Invoice Line Items & Breakdown</b>", styles["SectionTitle"]))
            story.append(Spacer(1, 3))

            th_list = [
                Paragraph("Description", styles["TH"]),
                Paragraph("Category", styles["THCenter"]),
                Paragraph("Qty", styles["THCenter"]),
                Paragraph("Unit Price", styles["THRight"]),
                Paragraph("Tax Rate", styles["THCenter"]),
                Paragraph("Amount", styles["THRight"]),
            ]
            table_data = [th_list]

            for item in line_items:
                desc = item.get("description") or "-"
                cat = AnalyticsService.categorize_description(str(desc))
                qty = item.get("quantity") if item.get("quantity") is not None else 1
                u_price = item.get("unit_price") if item.get("unit_price") is not None else 0.0
                tax_rate = item.get("tax_rate") or "-"
                amt = item.get("amount") if item.get("amount") is not None else (qty * u_price if isinstance(qty, (int, float)) and isinstance(u_price, (int, float)) else 0.0)

                table_data.append([
                    Paragraph(str(desc), styles["TD"]),
                    Paragraph(f"<font color='#4338CA'><b>{cat}</b></font>", styles["TDCatPill"]),
                    Paragraph(str(qty), styles["TDCenter"]),
                    Paragraph(f"{u_price:,.2f}" if isinstance(u_price, (int, float)) else str(u_price), styles["TDRight"]),
                    Paragraph(str(tax_rate), styles["TDCenter"]),
                    Paragraph(f"{amt:,.2f}" if isinstance(amt, (int, float)) else str(amt), styles["TDRight"]),
                ])

            table_data.append([
                Paragraph("<b>TOTAL PAYABLE</b>", styles["TDFooterLeft"]),
                Paragraph("", styles["TD"]),
                Paragraph("", styles["TD"]),
                Paragraph("", styles["TD"]),
                Paragraph(f"Tax: <b>{tax:,.2f}</b>", styles["TDFooterRight"]),
                Paragraph(f"<b>{total:,.2f} {cur}</b>", styles["TDFooterRight"]),
            ])

            col_widths = [2.72 * inch, 1.3 * inch, 0.6 * inch, 1.0 * inch, 0.9 * inch, 1.2 * inch]
            item_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            item_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
                    ("LINEABOVE", (0, -1), (-1, -1), 1.2, colors.HexColor("#0F172A")),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ])
            )
            story.append(item_table)

        return story

    # =========================================================================
    # RECEIPT STORY BUILDER
    # =========================================================================
    @classmethod
    def _build_receipt_story(
        cls,
        doc_id: str,
        extraction: Dict[str, Any],
        analytics: FinancialAnalytics,
        styles: Dict[str, ParagraphStyle],
    ) -> List[Any]:
        story: List[Any] = []

        subtotal = extraction.get("subtotal") or 0.0
        tax = extraction.get("tax") or 0.0
        discount = extraction.get("discount") or 0.0
        total = extraction.get("total") or (subtotal + tax - discount)
        cur = extraction.get("currency") or "INR"
        merchant = extraction.get("merchant") or "Store Merchant"
        primary_cat = AnalyticsService.categorize_description(str(merchant))

        card1 = [
            Paragraph("TOTAL PAID", styles["KPILabelG"]),
            Spacer(1, 2),
            Paragraph(f"{total:,.2f}", styles["KPIValG"]),
            Spacer(1, 1),
            Paragraph(f"Currency: {cur}", styles["KPISubG"]),
        ]
        card2 = [
            Paragraph("SUBTOTAL", styles["KPILabelB"]),
            Spacer(1, 2),
            Paragraph(f"{subtotal:,.2f}", styles["KPIValB"]),
            Spacer(1, 1),
            Paragraph("Net Items Sum", styles["KPISubB"]),
        ]
        card3 = [
            Paragraph("TAX / DUTY", styles["KPILabelP"]),
            Spacer(1, 2),
            Paragraph(f"{tax:,.2f}", styles["KPIValP"]),
            Spacer(1, 1),
            Paragraph("GST / VAT Applied", styles["KPISubP"]),
        ]
        card4 = [
            Paragraph("EXPENSE CATEGORY", styles["KPILabelB"]),
            Spacer(1, 2),
            Paragraph(primary_cat, styles["KPIValB"]),
            Spacer(1, 1),
            Paragraph("Auto-Classified", styles["KPISubB"]),
        ]

        kpi_table = Table([[card1, card2, card3, card4]], colWidths=[1.88 * inch] * 4)
        kpi_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#ECFDF5")),
                ("BOX", (0, 0), (0, 0), 1, colors.HexColor("#A7F3D0")),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#F0F9FF")),
                ("BOX", (1, 0), (1, 0), 1, colors.HexColor("#BAE6FD")),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#F5F3FF")),
                ("BOX", (2, 0), (2, 0), 1, colors.HexColor("#DDD6FE")),
                ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#F8FAFC")),
                ("BOX", (3, 0), (3, 0), 1, colors.HexColor("#CBD5E1")),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])
        )
        story.append(kpi_table)
        story.append(Spacer(1, 8))

        details_data = [
            [
                Paragraph("<b>Merchant:</b>", styles["MetaCardLabel"]),
                Paragraph(f"<b>{merchant}</b>", styles["MetaCardValue"]),
                Paragraph("<b>Receipt #:</b>", styles["MetaCardLabel"]),
                Paragraph(str(extraction.get("receipt_number") or "N/A"), styles["MetaCardValue"]),
            ],
            [
                Paragraph("<b>Date:</b>", styles["MetaCardLabel"]),
                Paragraph(str(extraction.get("date") or "N/A"), styles["MetaCardValue"]),
                Paragraph("<b>Payment Method:</b>", styles["MetaCardLabel"]),
                Paragraph(str(extraction.get("payment_method") or "Card / Cash"), styles["MetaCardValue"]),
            ],
        ]
        details_table = Table(details_data, colWidths=[1.2 * inch, 2.66 * inch, 1.2 * inch, 2.66 * inch])
        details_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("PADDING", (0, 0), (-1, -1), 5),
            ])
        )
        story.append(details_table)
        story.append(Spacer(1, 8))

        line_items = extraction.get("line_items", [])
        if line_items:
            story.append(Paragraph("<b>🛒 Purchased Line Items</b>", styles["SectionTitle"]))
            story.append(Spacer(1, 3))

            th_list = [
                Paragraph("Item Description", styles["TH"]),
                Paragraph("Category", styles["TH"]),
                Paragraph("Qty", styles["THCenter"]),
                Paragraph("Unit Price", styles["THRight"]),
                Paragraph("Total", styles["THRight"]),
            ]
            table_data = [th_list]

            for item in line_items:
                desc = item.get("description") or "-"
                cat = AnalyticsService.categorize_description(str(desc))
                qty = item.get("quantity") if item.get("quantity") is not None else 1
                u_price = item.get("unit_price") if item.get("unit_price") is not None else 0.0
                i_total = item.get("total") if item.get("total") is not None else (qty * u_price if isinstance(qty, (int, float)) and isinstance(u_price, (int, float)) else 0.0)

                table_data.append([
                    Paragraph(str(desc), styles["TD"]),
                    Paragraph(f"<font color='#4338CA'><b>{cat}</b></font>", styles["TDCatPill"]),
                    Paragraph(str(qty), styles["TDCenter"]),
                    Paragraph(f"{u_price:,.2f}" if isinstance(u_price, (int, float)) else str(u_price), styles["TDRight"]),
                    Paragraph(f"{i_total:,.2f}" if isinstance(i_total, (int, float)) else str(i_total), styles["TDRight"]),
                ])

            table_data.append([
                Paragraph("<b>TOTAL PAID</b>", styles["TDFooterLeft"]),
                Paragraph("", styles["TD"]),
                Paragraph("", styles["TD"]),
                Paragraph(f"Tax: {tax:,.2f}", styles["TDFooterRight"]),
                Paragraph(f"<b>{total:,.2f} {cur}</b>", styles["TDFooterRight"]),
            ])

            col_widths = [3.2 * inch, 1.6 * inch, 0.72 * inch, 1.1 * inch, 1.1 * inch]
            item_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            item_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
                    ("LINEABOVE", (0, -1), (-1, -1), 1.2, colors.HexColor("#0F172A")),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ])
            )
            story.append(item_table)

        return story

    # =========================================================================
    # GENERAL DOCUMENT STORY BUILDER
    # =========================================================================
    @classmethod
    def _build_general_document_story(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        analytics: FinancialAnalytics,
        raw_text: str,
        styles: Dict[str, ParagraphStyle],
    ) -> List[Any]:
        story: List[Any] = []

        if extraction:
            story.append(Paragraph("<b>📋 Extracted Document Attributes</b>", styles["SectionTitle"]))
            story.append(Spacer(1, 4))

            prop_data = []
            for k, v in list(extraction.items())[:12]:
                if not isinstance(v, (dict, list)):
                    prop_data.append([
                        Paragraph(f"<b>{k.replace('_', ' ').title()}</b>", styles["MetaCardLabel"]),
                        Paragraph(str(v), styles["MetaCardValue"]),
                    ])

            if prop_data:
                prop_table = Table(prop_data, colWidths=[2.5 * inch, 5.22 * inch])
                prop_table.setStyle(
                    TableStyle([
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                        ("PADDING", (0, 0), (-1, -1), 4.5),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ])
                )
                story.append(prop_table)
                story.append(Spacer(1, 8))

        if raw_text:
            story.append(Paragraph("<b>📄 Extracted Text Content</b>", styles["SectionTitle"]))
            story.append(Spacer(1, 4))
            clean_text = raw_text.replace("\n", "<br/>")[:3000]
            story.append(Paragraph(clean_text, styles["TD"]))

        return story

    # =========================================================================
    # HELPERS
    # =========================================================================
    @staticmethod
    def _get_metadata_summary(doc_type: str, extraction: Dict[str, Any], doc_id: str) -> List[Tuple[str, str]]:
        meta = []
        if doc_type == "bank_statement":
            if extraction.get("bank_name"):
                meta.append(("Bank Name", str(extraction["bank_name"])))
            if extraction.get("account_holder"):
                meta.append(("Account Holder", str(extraction["account_holder"])))
            if extraction.get("account_number_masked"):
                meta.append(("Account #", str(extraction["account_number_masked"])))
            if extraction.get("currency"):
                meta.append(("Currency", str(extraction["currency"])))
            if extraction.get("statement_period"):
                meta.append(("Period", str(extraction["statement_period"])))
            if extraction.get("opening_balance") is not None:
                meta.append(("Opening Balance", f"{extraction['opening_balance']:,.2f}"))
            if extraction.get("closing_balance") is not None:
                meta.append(("Closing Balance", f"{extraction['closing_balance']:,.2f}"))

        elif doc_type == "receipt":
            if extraction.get("merchant"):
                meta.append(("Merchant", str(extraction["merchant"])))
            if extraction.get("receipt_number"):
                meta.append(("Receipt #", str(extraction["receipt_number"])))
            if extraction.get("date"):
                meta.append(("Date", str(extraction["date"])))
            if extraction.get("currency"):
                meta.append(("Currency", str(extraction["currency"])))
            if extraction.get("total") is not None:
                meta.append(("Total Amount", f"{extraction['total']:,.2f}"))
            if extraction.get("payment_method"):
                meta.append(("Payment Method", str(extraction["payment_method"])))

        elif doc_type == "invoice":
            if extraction.get("invoice_number"):
                meta.append(("Invoice #", str(extraction["invoice_number"])))
            if extraction.get("invoice_date"):
                meta.append(("Invoice Date", str(extraction["invoice_date"])))
            supplier = extraction.get("supplier")
            if isinstance(supplier, dict) and supplier.get("name"):
                meta.append(("Supplier", str(supplier["name"])))
            customer = extraction.get("customer")
            if isinstance(customer, dict) and customer.get("name"):
                meta.append(("Customer", str(customer["name"])))
            if extraction.get("total") is not None:
                meta.append(("Total", f"{extraction['total']:,.2f}"))

        return meta

    @staticmethod
    def _get_table_data_with_category(doc_type: str, extraction: Dict[str, Any]) -> Tuple[List[str], List[List[Any]]]:
        """Returns table data with auto-classified category column and formula injection sanitization."""
        if doc_type == "bank_statement":
            txs = extraction.get("transactions", [])
            if txs:
                headers = ["Date", "Category", "Description", "Reference", "Debit", "Credit", "Balance"]
                rows = [
                    [
                        sanitize_for_formula_injection(t.get("date") or "-"),
                        sanitize_for_formula_injection(AnalyticsService.categorize_description(str(t.get("description") or ""))),
                        sanitize_for_formula_injection(t.get("description") or "-"),
                        sanitize_for_formula_injection(t.get("reference") or "-"),
                        t.get("debit") if t.get("debit") is not None else "",
                        t.get("credit") if t.get("credit") is not None else "",
                        t.get("balance") if t.get("balance") is not None else "",
                    ]
                    for t in txs
                ]
                return headers, rows

        elif doc_type == "receipt":
            items = extraction.get("line_items", [])
            if items:
                headers = ["Description", "Category", "Quantity", "Unit Price", "Total"]
                rows = [
                    [
                        sanitize_for_formula_injection(item.get("description") or "-"),
                        sanitize_for_formula_injection(AnalyticsService.categorize_description(str(item.get("description") or ""))),
                        item.get("quantity") if item.get("quantity") is not None else "",
                        item.get("unit_price") if item.get("unit_price") is not None else "",
                        item.get("total") if item.get("total") is not None else "",
                    ]
                    for item in items
                ]
                return headers, rows

        elif doc_type == "invoice":
            items = extraction.get("line_items", [])
            if items:
                headers = ["Description", "Category", "Quantity", "Unit Price", "Tax Rate", "Amount"]
                rows = [
                    [
                        sanitize_for_formula_injection(item.get("description") or "-"),
                        sanitize_for_formula_injection(AnalyticsService.categorize_description(str(item.get("description") or ""))),
                        item.get("quantity") if item.get("quantity") is not None else "",
                        item.get("unit_price") if item.get("unit_price") is not None else "",
                        item.get("tax_rate") if item.get("tax_rate") is not None else "",
                        item.get("amount") if item.get("amount") is not None else "",
                    ]
                    for item in items
                ]
                return headers, rows

        return [], []
