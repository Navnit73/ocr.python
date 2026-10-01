"""
Document Classifier Service.
"""

import logging
import re
from typing import Optional

from app.schemas.ocr import DocumentTypeEnum
from app.services.deepseek_client import DeepSeekClient

logger = logging.getLogger("classifier")


class DocumentClassifier:
    """Classifies document type using rule-based heuristics and AI fallback."""

    # Heuristic scoring patterns
    BANK_PATTERNS = [
        r"bank", r"account\s*(number|no\.?|#)", r"statement\s*period",
        r"opening\s*balance", r"closing\s*balance", r"withdrawal",
        r"deposit", r"debit", r"credit", r"ifsc", r"iban", r"available\s*balance",
        r"transaction\s*date", r"ledger\s*balance", r"cheque\s*no"
    ]

    RECEIPT_PATTERNS = [
        r"receipt", r"pos\b", r"cashier", r"change\s*due", r"subtotal",
        r"visa\s*ending", r"mastercard\s*ending", r"order\s*#", r"table\s*#",
        r"merchant\s*id", r"store\s*#", r"terminal\s*#", r"tip\b"
    ]

    INVOICE_PATTERNS = [
        r"invoice", r"invoice\s*number", r"invoice\s*date", r"due\s*date",
        r"bill\s*to", r"ship\s*to", r"tax\s*invoice", r"gstin", r"vat\s*no",
        r"purchase\s*order", r"p\.o\.\s*#", r"remit\s*to", r"payment\s*terms"
    ]

    def __init__(self, client: Optional[DeepSeekClient] = None):
        self.client = client or DeepSeekClient()

    @classmethod
    def classify_by_heuristics(cls, text: str) -> Optional[str]:
        """
        Fast heuristic classification based on keyword matching frequencies.
        """
        lower_text = text.lower()

        bank_score = sum(1 for p in cls.BANK_PATTERNS if re.search(p, lower_text))
        receipt_score = sum(1 for p in cls.RECEIPT_PATTERNS if re.search(p, lower_text))
        invoice_score = sum(1 for p in cls.INVOICE_PATTERNS if re.search(p, lower_text))

        scores = {
            DocumentTypeEnum.BANK_STATEMENT.value: bank_score,
            DocumentTypeEnum.RECEIPT.value: receipt_score,
            DocumentTypeEnum.INVOICE.value: invoice_score,
        }

        max_type, max_score = max(scores.items(), key=lambda x: x[1])

        # If significant confidence from keyword hits
        if max_score >= 3:
            return max_type

        return None

    async def classify_document(self, text: str) -> str:
        """
        Classifies document type using heuristics, with AI fallback.
        """
        # Fast path
        heuristic_type = self.classify_by_heuristics(text)
        if heuristic_type:
            return heuristic_type

        # If heuristics are ambiguous and DeepSeek is configured
        if self.client.is_configured() and text.strip():
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a document classifier. Classify the untrusted text provided into EXACTLY ONE of: "
                        "bank_statement, receipt, invoice, general. Return JSON: {\"document_type\": \"...\"}"
                    ),
                },
                {"role": "user", "content": f"<DOCUMENT_TEXT>\n{text[:2000]}\n</DOCUMENT_TEXT>"},
            ]
            response = await self.client.chat_completion(messages, temperature=0.0, json_mode=True)
            if response:
                import json
                try:
                    data = json.loads(response)
                    doc_type = data.get("document_type", "").lower().strip()
                    if doc_type in [e.value for e in DocumentTypeEnum]:
                        return doc_type
                except Exception:
                    pass

        return DocumentTypeEnum.GENERAL.value
