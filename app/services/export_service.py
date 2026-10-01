import csv
import io
from typing import Any, Dict, List, Optional, Tuple
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.schemas.export import ExportFormatEnum


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
        fmt = export_format.lower().strip()
        data = extraction or {}

        if fmt in [ExportFormatEnum.XLSX.value, ExportFormatEnum.EXCEL.value]:
            file_bytes = cls.generate_excel(doc_id, document_type, data, raw_text)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"{document_type}_{doc_id}.xlsx"
        elif fmt == ExportFormatEnum.CSV.value:
            file_bytes = cls.generate_csv(doc_id, document_type, data, raw_text)
            media_type = "text/csv"
            filename = f"{document_type}_{doc_id}.csv"
        elif fmt == ExportFormatEnum.PDF.value:
            file_bytes = cls.generate_pdf(doc_id, document_type, data, raw_text)
            media_type = "application/pdf"
            filename = f"{document_type}_{doc_id}.pdf"
        else:
            raise ValueError(f"Unsupported export format '{export_format}'. Choose 'xlsx', 'csv', or 'pdf'.")

        return file_bytes, media_type, filename

    # -------------------------------------------------------------------------
    # 1. EXCEL EXPORT (openpyxl)
    # -------------------------------------------------------------------------
    @classmethod
    def generate_excel(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        raw_text: str = "",
    ) -> bytes:
        """Creates a beautifully styled Excel workbook."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Extracted Data"
        ws.views.sheetView[0].showGridLines = True

        # Styles
        navy_header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        accent_blue_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
        card_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        alt_row_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        white_title_font = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
        white_header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        bold_font = Font(name="Calibri", size=11, bold=True, color="0F172A")
        regular_font = Font(name="Calibri", size=10, color="1E293B")
        meta_label_font = Font(name="Calibri", size=10, bold=True, color="475569")
        meta_val_font = Font(name="Calibri", size=10, bold=True, color="0F172A")

        thin_side = Side(border_style="thin", color="CBD5E1")
        border_all = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        # 1. Top Title Banner
        ws.merge_cells("A1:F2")
        title_cell = ws["A1"]
        title_cell.value = f"{document_type.replace('_', ' ').upper()} - OCR EXTRACTION"
        title_cell.font = white_title_font
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        for row in ws["A1:F2"]:
            for cell in row:
                cell.fill = navy_header_fill

        # 2. Metadata Section (Rows 4-7)
        curr_row = 4
        metadata_items = cls._get_metadata_summary(document_type, extraction, doc_id)
        if metadata_items:
            for idx in range(0, len(metadata_items), 2):
                item1 = metadata_items[idx]
                ws.cell(row=curr_row, column=1, value=item1[0]).font = meta_label_font
                ws.cell(row=curr_row, column=2, value=item1[1]).font = meta_val_font
                ws.cell(row=curr_row, column=1).fill = card_fill
                ws.cell(row=curr_row, column=2).fill = card_fill

                if idx + 1 < len(metadata_items):
                    item2 = metadata_items[idx + 1]
                    ws.cell(row=curr_row, column=4, value=item2[0]).font = meta_label_font
                    ws.cell(row=curr_row, column=5, value=item2[1]).font = meta_val_font
                    ws.cell(row=curr_row, column=4).fill = card_fill
                    ws.cell(row=curr_row, column=5).fill = card_fill
                curr_row += 1
            curr_row += 1

        # 3. Table Headers & Rows
        headers, rows = cls._get_table_data(document_type, extraction)
        if headers:
            # Header row
            for col_idx, h in enumerate(headers, start=1):
                c = ws.cell(row=curr_row, column=col_idx, value=h)
                c.fill = accent_blue_fill
                c.font = white_header_font
                c.alignment = Alignment(horizontal="center", vertical="center")
                c.border = border_all
            curr_row += 1

            # Data rows
            for r_idx, r in enumerate(rows):
                fill = alt_row_fill if r_idx % 2 == 1 else PatternFill(fill_type=None)
                for col_idx, val in enumerate(r, start=1):
                    c = ws.cell(row=curr_row, column=col_idx, value=val)
                    c.font = regular_font
                    c.border = border_all
                    c.fill = fill
                    if isinstance(val, (int, float)):
                        c.number_format = "#,##0.00"
                        c.alignment = Alignment(horizontal="right")
                    else:
                        c.alignment = Alignment(horizontal="left")
                curr_row += 1

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val = str(cell.value or "")
                max_len = max(max_len, len(val))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 14)

        output = io.BytesIO()
        wb.save(output)
        return output.getvalue()

    # -------------------------------------------------------------------------
    # 2. CSV EXPORT
    # -------------------------------------------------------------------------
    @classmethod
    def generate_csv(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        raw_text: str = "",
    ) -> bytes:
        """Generates standard RFC 4180 CSV export."""
        output = io.StringIO()
        writer = csv.writer(output)

        # Metadata Header
        writer.writerow(["# Document ID", doc_id])
        writer.writerow(["# Document Type", document_type])

        metadata_items = cls._get_metadata_summary(document_type, extraction, doc_id)
        for label, val in metadata_items:
            writer.writerow([f"# {label}", val])
        writer.writerow([])

        # Table data
        headers, rows = cls._get_table_data(document_type, extraction)
        if headers:
            writer.writerow(headers)
            for r in rows:
                writer.writerow(r)
        else:
            # Fallback to key-value dump if no structured table
            writer.writerow(["Key", "Value"])
            for k, v in extraction.items():
                if not isinstance(v, (list, dict)):
                    writer.writerow([k, v])

        return output.getvalue().encode("utf-8-sig")

    # -------------------------------------------------------------------------
    # 3. PDF EXPORT (ReportLab)
    # -------------------------------------------------------------------------
    @classmethod
    def generate_pdf(
        cls,
        doc_id: str,
        document_type: str,
        extraction: Dict[str, Any],
        raw_text: str = "",
    ) -> bytes:
        """Generates a professional corporate PDF report."""
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#1E3A8A"),
            fontName="Helvetica-Bold",
        )
        subtitle_style = ParagraphStyle(
            "DocSubtitle",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#64748B"),
        )
        body_style = ParagraphStyle(
            "DocBody",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#1E293B"),
        )
        header_cell_style = ParagraphStyle(
            "HeaderCell",
            parent=styles["Normal"],
            fontSize=9,
            leading=11,
            textColor=colors.white,
            fontName="Helvetica-Bold",
            alignment=1,  # Center
        )

        story = []

        # Title & ID
        story.append(Paragraph(f"{document_type.replace('_', ' ').upper()} REPORT", title_style))
        story.append(Paragraph(f"Document ID: {doc_id} | Type: {document_type}", subtitle_style))
        story.append(Spacer(1, 12))

        # Metadata Card Table
        metadata_items = cls._get_metadata_summary(document_type, extraction, doc_id)
        if metadata_items:
            meta_data = []
            for i in range(0, len(metadata_items), 2):
                row = [
                    Paragraph(f"<b>{metadata_items[i][0]}:</b> {metadata_items[i][1]}", body_style),
                ]
                if i + 1 < len(metadata_items):
                    row.append(Paragraph(f"<b>{metadata_items[i+1][0]}:</b> {metadata_items[i+1][1]}", body_style))
                else:
                    row.append(Paragraph("", body_style))
                meta_data.append(row)

            meta_table = Table(meta_data, colWidths=[3.7 * inch, 3.7 * inch])
            meta_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
                    ("PADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ])
            )
            story.append(meta_table)
            story.append(Spacer(1, 14))

        # Table data
        headers, rows = cls._get_table_data(document_type, extraction)
        if headers and rows:
            table_data = [[Paragraph(h, header_cell_style) for h in headers]]
            for r in rows:
                row_cells = []
                for val in r:
                    val_str = f"{val:,.2f}" if isinstance(val, (int, float)) else str(val or "-")
                    row_cells.append(Paragraph(val_str, body_style))
                table_data.append(row_cells)

            col_width = (7.4 * inch) / len(headers)
            main_table = Table(table_data, colWidths=[col_width] * len(headers))
            main_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("PADDING", (0, 0), (-1, -1), 5),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ])
            )
            story.append(main_table)
        elif raw_text:
            story.append(Paragraph("<b>Extracted Content:</b>", body_style))
            story.append(Spacer(1, 6))
            story.append(Paragraph(raw_text.replace("\n", "<br/>")[:2000], body_style))

        doc.build(story)
        return buf.getvalue()

    # -------------------------------------------------------------------------
    # Helper Data Formatters
    # -------------------------------------------------------------------------
    @staticmethod
    def _get_metadata_summary(doc_type: str, extraction: Dict[str, Any], doc_id: str) -> List[Tuple[str, str]]:
        """Extracts key-value header summary tuples."""
        meta = []
        if doc_type == "bank_statement":
            if extraction.get("bank_name"):
                meta.append(("Bank Name", str(extraction["bank_name"])))
            if extraction.get("account_holder"):
                meta.append(("Account Holder", str(extraction["account_holder"])))
            if extraction.get("account_number_masked"):
                meta.append(("Account Number", str(extraction["account_number_masked"])))
            if extraction.get("currency"):
                meta.append(("Currency", str(extraction["currency"])))
            if extraction.get("statement_period"):
                meta.append(("Statement Period", str(extraction["statement_period"])))
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
            if extraction.get("subtotal") is not None:
                meta.append(("Subtotal", f"{extraction['subtotal']:,.2f}"))
            if extraction.get("tax") is not None:
                meta.append(("Tax", f"{extraction['tax']:,.2f}"))
            if extraction.get("total") is not None:
                meta.append(("Total Amount", f"{extraction['total']:,.2f}"))
            if extraction.get("payment_method"):
                meta.append(("Payment Method", str(extraction["payment_method"])))

        elif doc_type == "invoice":
            if extraction.get("invoice_number"):
                meta.append(("Invoice #", str(extraction["invoice_number"])))
            if extraction.get("invoice_date"):
                meta.append(("Invoice Date", str(extraction["invoice_date"])))
            if extraction.get("due_date"):
                meta.append(("Due Date", str(extraction["due_date"])))
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
    def _get_table_data(doc_type: str, extraction: Dict[str, Any]) -> Tuple[List[str], List[List[Any]]]:
        """Returns (headers, rows) matrix for the primary tabular data."""
        if doc_type == "bank_statement":
            txs = extraction.get("transactions", [])
            if txs:
                headers = ["Date", "Description", "Reference", "Debit", "Credit", "Balance"]
                rows = [
                    [
                        t.get("date") or "-",
                        t.get("description") or "-",
                        t.get("reference") or "-",
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
                headers = ["Description", "Quantity", "Unit Price", "Total"]
                rows = [
                    [
                        item.get("description") or "-",
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
                headers = ["Description", "Quantity", "Unit Price", "Tax Rate", "Amount"]
                rows = [
                    [
                        item.get("description") or "-",
                        item.get("quantity") if item.get("quantity") is not None else "",
                        item.get("unit_price") if item.get("unit_price") is not None else "",
                        item.get("tax_rate") if item.get("tax_rate") is not None else "",
                        item.get("amount") if item.get("amount") is not None else "",
                    ]
                    for item in items
                ]
                return headers, rows

        return [], []


from typing import Tuple
