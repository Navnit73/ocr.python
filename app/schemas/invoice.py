"""
Invoice Extraction Schema.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class PartyDetails(BaseModel):
    """Details of supplier or customer in an invoice."""
    name: Optional[str] = Field(default=None, description="Company or individual name")
    address: Optional[str] = Field(default=None, description="Address")
    tax_id: Optional[str] = Field(default=None, description="Tax ID / GSTIN / VAT number")
    email: Optional[str] = Field(default=None, description="Email address")
    phone: Optional[str] = Field(default=None, description="Phone number")


class InvoiceLineItem(BaseModel):
    """Line item in an invoice."""
    description: Optional[str] = Field(default=None, description="Item or service description")
    quantity: Optional[float] = Field(default=None, description="Quantity")
    unit_price: Optional[float] = Field(default=None, description="Unit price")
    tax_rate: Optional[float] = Field(default=None, description="Tax percentage or rate")
    amount: Optional[float] = Field(default=None, description="Total amount for this line")


class InvoiceExtraction(BaseModel):
    """Structured extraction model for Invoices."""
    invoice_number: Optional[str] = Field(default=None, description="Invoice reference number")
    invoice_date: Optional[str] = Field(default=None, description="Invoice issue date in ISO 8601")
    due_date: Optional[str] = Field(default=None, description="Payment due date in ISO 8601")
    supplier: Optional[PartyDetails] = Field(default=None, description="Supplier / Seller details")
    customer: Optional[PartyDetails] = Field(default=None, description="Customer / Buyer details")
    currency: Optional[str] = Field(default=None, description="Currency code (e.g. INR, USD, EUR)")
    line_items: List[InvoiceLineItem] = Field(default_factory=list, description="List of items or services")
    subtotal: Optional[float] = Field(default=None, description="Subtotal amount before tax")
    tax: Optional[float] = Field(default=None, description="Total tax amount")
    total: Optional[float] = Field(default=None, description="Total invoice amount")
