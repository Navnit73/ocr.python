"""
Bank Statement Extraction Schema.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class BankTransaction(BaseModel):
    """Individual transaction item in a bank statement."""
    date: Optional[str] = Field(default=None, description="Transaction date in ISO 8601 (YYYY-MM-DD) if unambiguous")
    description: Optional[str] = Field(default=None, description="Description or narration of the transaction")
    reference: Optional[str] = Field(default=None, description="Cheque/UTR/Reference number")
    debit: Optional[float] = Field(default=None, description="Withdrawal or debit amount")
    credit: Optional[float] = Field(default=None, description="Deposit or credit amount")
    balance: Optional[float] = Field(default=None, description="Running account balance after transaction")


class BankStatementExtraction(BaseModel):
    """Structured extraction model for Bank Statements."""
    bank_name: Optional[str] = Field(default=None, description="Name of the banking institution")
    account_holder: Optional[str] = Field(default=None, description="Account holder name")
    account_number_masked: Optional[str] = Field(default=None, description="Masked or full account number")
    currency: Optional[str] = Field(default=None, description="ISO currency code (e.g. INR, USD, EUR)")
    statement_period: Optional[str] = Field(default=None, description="Statement period or date range")
    opening_balance: Optional[float] = Field(default=None, description="Opening balance amount")
    closing_balance: Optional[float] = Field(default=None, description="Closing balance amount")
    transactions: List[BankTransaction] = Field(default_factory=list, description="Ordered list of transactions")
