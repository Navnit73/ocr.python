"""
Receipt Extraction Schema.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class ReceiptLineItem(BaseModel):
    """Line item in a receipt."""
    description: Optional[str] = Field(default=None, description="Item name or description")
    quantity: Optional[float] = Field(default=None, description="Quantity purchased")
    unit_price: Optional[float] = Field(default=None, description="Price per unit")
    total: Optional[float] = Field(default=None, description="Total amount for item")


class ReceiptExtraction(BaseModel):
    """Structured extraction model for Receipts."""
    merchant: Optional[str] = Field(default=None, description="Merchant or store name")
    receipt_number: Optional[str] = Field(default=None, description="Receipt or transaction ID")
    date: Optional[str] = Field(default=None, description="Date in ISO 8601 (YYYY-MM-DD)")
    currency: Optional[str] = Field(default=None, description="Currency symbol or ISO code")
    subtotal: Optional[float] = Field(default=None, description="Subtotal amount before taxes/discounts")
    tax: Optional[float] = Field(default=None, description="Total tax amount (VAT, GST, Sales Tax)")
    discount: Optional[float] = Field(default=None, description="Discount amount applied")
    total: Optional[float] = Field(default=None, description="Grand total amount paid")
    payment_method: Optional[str] = Field(default=None, description="Payment method (Cash, Card, UPI, etc.)")
    line_items: List[ReceiptLineItem] = Field(default_factory=list, description="List of purchased items")
