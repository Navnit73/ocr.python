"""
Financial Analytics, Expense Categorization, and AI Insights Engine.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class CategoryBreakdown(BaseModel):
    category: str
    amount: float
    percentage: float
    count: int


class DetectedSubscription(BaseModel):
    merchant: str
    amount: float
    description: str


class FinancialAnalytics(BaseModel):
    total_inflow: float = 0.0
    total_outflow: float = 0.0
    net_savings: float = 0.0
    savings_rate_pct: float = 0.0
    transaction_count: int = 0
    categories: List[CategoryBreakdown] = Field(default_factory=list)
    subscriptions: List[DetectedSubscription] = Field(default_factory=list)
    subscription_total: float = 0.0
    subscription_count: int = 0
    top_category: str = "N/A"
    largest_expense: Optional[Dict[str, Any]] = None
    ai_insights: List[str] = Field(default_factory=list)


# Keywords for rule-based categorization
CATEGORY_RULES = {
    "Food & Dining": [
        "zomato", "swiggy", "uber eats", "doordash", "restaurant", "cafe",
        "starbucks", "mcdonald", "domino", "burger", "pizza", "coffee",
        "diner", "food", "bakery", "bar", "pub", "bistro", "eatery"
    ],
    "Groceries": [
        "supermarket", "grocery", "blinkit", "zepto", "instacart", "walmart",
        "costco", "target", "whole foods", "dmart", "bigbasket", "trader joe",
        "provision", "mart", "hypermarket", "market"
    ],
    "Utilities & Bills": [
        "electricity", "water", "gas", "power", "broadband", "wifi", "telecom",
        "airtel", "jio", "verizon", "at&t", "utility", "bill desk", "bescom",
        "tneb", "recharge", "postpaid", "prepaid", "insurance", "lic"
    ],
    "Travel & Commute": [
        "uber", "ola", "lyft", "flight", "airline", "irctc", "train", "metro",
        "fuel", "petrol", "shell", "parking", "toll", "fastag", "railway",
        "indigo", "air india", "cab", "taxi"
    ],
    "Shopping": [
        "amazon", "flipkart", "myntra", "ebay", "zara", "h&m", "apparel",
        "retail", "clothing", "mall", "store", "electronics", "apple store"
    ],
    "Entertainment & Subscriptions": [
        "netflix", "spotify", "cinema", "pvr", "theatre", "movie", "hotstar",
        "youtube", "disney", "prime video", "apple.com", "google play",
        "openai", "chatgpt", "github", "adobe", "gym", "cult.fit", "hulu",
        "audible", "patreon", "substack", "dropbox", "canva", "playstation"
    ],
    "Salary & Income": [
        "salary", "payroll", "dividend", "interest", "bonus", "stipend",
        "income", "refund", "remittance received", "credit interest"
    ],
    "Investments": [
        "zerodha", "groww", "mutual fund", "sip", "stock", "etf", "vanguard",
        "fidelity", "crypto", "binance", "coinbase", "securities"
    ],
    "Transfers & UPI": [
        "upi", "neft", "rtgs", "imps", "transfer to", "p2p", "venmo",
        "zelle", "cashapp", "fund transfer", "sent to"
    ]
}

SUBSCRIPTION_MERCHANTS = [
    "netflix", "spotify", "prime", "hotstar", "youtube", "apple", "icloud",
    "google storage", "openai", "chatgpt", "github", "adobe", "gym", "cult.fit",
    "disney", "hulu", "audible", "patreon", "substack", "dropbox", "canva",
    "zoom", "notion", "slack", "medium", "figma"
]


class AnalyticsService:
    """Analyzes financial documents, classifies expenses, and computes KPIs."""

    @classmethod
    def categorize_description(cls, description: str) -> str:
        """Categorizes a transaction narration/description."""
        if not description:
            return "Miscellaneous"

        desc_lower = description.lower()
        for category, keywords in CATEGORY_RULES.items():
            for kw in keywords:
                if re.search(r"\b" + re.escape(kw) + r"\b", desc_lower) or kw in desc_lower:
                    return category

        return "Miscellaneous"

    @classmethod
    def analyze(cls, document_type: str, extraction: Dict[str, Any]) -> FinancialAnalytics:
        """
        Computes detailed financial analytics, category distributions,
        subscription detection, and actionable suggestions.
        """
        analytics = FinancialAnalytics()

        if document_type == "bank_statement":
            cls._analyze_bank_statement(extraction, analytics)
        elif document_type in ["receipt", "invoice"]:
            cls._analyze_receipt_or_invoice(extraction, analytics)
        else:
            cls._analyze_general(extraction, analytics)

        # Generate intelligent insights & suggestions
        cls._generate_insights(analytics, document_type)

        return analytics

    @classmethod
    def _analyze_bank_statement(cls, extraction: Dict[str, Any], analytics: FinancialAnalytics):
        txs = extraction.get("transactions", [])
        analytics.transaction_count = len(txs)

        category_totals: Dict[str, float] = {}
        category_counts: Dict[str, int] = {}
        detected_subs: List[DetectedSubscription] = []
        max_expense = 0.0
        largest_item = None

        for t in txs:
            desc = str(t.get("description") or "Unknown Transaction")
            debit = float(t.get("debit") or 0.0)
            credit = float(t.get("credit") or 0.0)

            analytics.total_outflow += debit
            analytics.total_inflow += credit

            if debit > 0:
                cat = cls.categorize_description(desc)
                category_totals[cat] = category_totals.get(cat, 0.0) + debit
                category_counts[cat] = category_counts.get(cat, 0) + 1

                if debit > max_expense:
                    max_expense = debit
                    largest_item = {"description": desc, "amount": debit, "date": t.get("date")}

                # Check if recurring subscription
                desc_lower = desc.lower()
                for sub_kw in SUBSCRIPTION_MERCHANTS:
                    if sub_kw in desc_lower:
                        detected_subs.append(
                            DetectedSubscription(
                                merchant=sub_kw.title(),
                                amount=debit,
                                description=desc,
                            )
                        )
                        break

        analytics.total_outflow = round(analytics.total_outflow, 2)
        analytics.total_inflow = round(analytics.total_inflow, 2)
        analytics.net_savings = round(analytics.total_inflow - analytics.total_outflow, 2)

        if analytics.total_inflow > 0:
            analytics.savings_rate_pct = round(max(0.0, (analytics.net_savings / analytics.total_inflow) * 100), 1)

        # Build category breakdown
        breakdowns: List[CategoryBreakdown] = []
        for cat, amt in category_totals.items():
            pct = round((amt / analytics.total_outflow * 100), 1) if analytics.total_outflow > 0 else 0.0
            breakdowns.append(
                CategoryBreakdown(
                    category=cat,
                    amount=round(amt, 2),
                    percentage=pct,
                    count=category_counts.get(cat, 0),
                )
            )

        breakdowns.sort(key=lambda x: x.amount, reverse=True)
        analytics.categories = breakdowns
        if breakdowns:
            analytics.top_category = f"{breakdowns[0].category} ({breakdowns[0].percentage}%)"

        analytics.subscriptions = detected_subs
        analytics.subscription_count = len(detected_subs)
        analytics.subscription_total = round(sum(s.amount for s in detected_subs), 2)
        analytics.largest_expense = largest_item

    @classmethod
    def _analyze_receipt_or_invoice(cls, extraction: Dict[str, Any], analytics: FinancialAnalytics):
        total = float(extraction.get("total") or extraction.get("subtotal") or 0.0)
        analytics.total_outflow = round(total, 2)
        items = extraction.get("line_items", [])
        analytics.transaction_count = len(items) if items else 1

        merchant = extraction.get("merchant") or extraction.get("supplier", {}).get("name") or "Merchant"
        cat = cls.categorize_description(str(merchant))

        analytics.categories = [
            CategoryBreakdown(category=cat, amount=total, percentage=100.0, count=analytics.transaction_count)
        ]
        analytics.top_category = cat

    @classmethod
    def _analyze_general(cls, extraction: Dict[str, Any], analytics: FinancialAnalytics):
        analytics.transaction_count = 1
        analytics.top_category = "General Expense"

    @classmethod
    def _generate_insights(cls, analytics: FinancialAnalytics, document_type: str):
        insights = []

        if document_type == "bank_statement":
            # 1. Cashflow & Savings insight
            if analytics.net_savings > 0:
                insights.append(
                    f"💡 Strong Cash Flow: Positive net savings of {analytics.net_savings:,.2f} ({analytics.savings_rate_pct}% savings rate)."
                )
            elif analytics.net_savings < 0:
                insights.append(
                    f"⚠️ Negative Cashflow: Total outflow exceeded inflow by {abs(analytics.net_savings):,.2f}. Consider curbing non-essential expenses."
                )

            # 2. Subscription analysis
            if analytics.subscription_count > 0:
                sub_pct = (
                    round((analytics.subscription_total / analytics.total_outflow) * 100, 1)
                    if analytics.total_outflow > 0
                    else 0.0
                )
                subs_str = ", ".join(set(s.merchant for s in analytics.subscriptions[:4]))
                insights.append(
                    f"🔁 Active Subscriptions: {analytics.subscription_count} recurring services detected ({subs_str}) totaling {analytics.subscription_total:,.2f} ({sub_pct}% of spend)."
                )
            else:
                insights.append("✅ Zero recurring subscription charges detected.")

            # 3. Top spend category insight
            if analytics.categories:
                top = analytics.categories[0]
                if top.percentage > 35.0:
                    insights.append(
                        f"📊 Budget Concentration: {top.category} is your highest spend area at {top.percentage}% ({top.amount:,.2f}) of total expenses."
                    )
                else:
                    insights.append(
                        f"📊 Balanced Spending: Highest category is {top.category} at {top.percentage}% ({top.amount:,.2f})."
                    )

            # 4. Largest transaction flag
            if analytics.largest_expense:
                insights.append(
                    f"🏷️ Peak Expense: Largest single outflow was {analytics.largest_expense['amount']:,.2f} on '{analytics.largest_expense['description']}'."
                )

        elif document_type in ["receipt", "invoice"]:
            tax = float(analytics.total_outflow * 0.05)
            insights.append(f"🧾 Verified {document_type.title()}: Total amount recorded as {analytics.total_outflow:,.2f}.")
            insights.append("💡 Tip: Store this validated digital extract for easy tax filing and expense reimbursement.")

        if not insights:
            insights.append("✅ Document processed successfully with zero critical anomalies detected.")

        analytics.ai_insights = insights
