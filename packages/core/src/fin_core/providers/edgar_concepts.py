"""Which XBRL tags feed which of our normalized concepts.

A company reports a concept under one of several tags depending on the era and its
accounting choices (e.g. revenue). All candidate tags are stored under the same normalized
``concept``; consumers pick per company by priority order (first tag with data wins).
Keys are ``taxonomy/tag`` exactly as in the SEC "company facts" payload.
"""

from __future__ import annotations

# concept -> ordered candidate tags (highest priority first)
CONCEPT_TAGS: dict[str, tuple[str, ...]] = {
    "Revenue": (
        "us-gaap/Revenues",
        "us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap/SalesRevenueNet",
    ),
    "NetIncome": ("us-gaap/NetIncomeLoss",),
    "OperatingIncome": ("us-gaap/OperatingIncomeLoss",),
    "EpsDiluted": ("us-gaap/EarningsPerShareDiluted",),
    "Assets": ("us-gaap/Assets",),
    "AssetsCurrent": ("us-gaap/AssetsCurrent",),
    "Liabilities": ("us-gaap/Liabilities",),
    "LiabilitiesCurrent": ("us-gaap/LiabilitiesCurrent",),
    "StockholdersEquity": (
        "us-gaap/StockholdersEquity",
        "us-gaap/StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "LongTermDebt": ("us-gaap/LongTermDebtNoncurrent", "us-gaap/LongTermDebt"),
    "Cash": ("us-gaap/CashAndCashEquivalentsAtCarryingValue",),
    "InterestExpense": ("us-gaap/InterestExpense",),
    "DividendsPerShare": (
        "us-gaap/CommonStockDividendsPerShareDeclared",
        "us-gaap/CommonStockDividendsPerShareCashPaid",
    ),
    "GrossProfit": ("us-gaap/GrossProfit",),
    "RetainedEarnings": ("us-gaap/RetainedEarningsAccumulatedDeficit",),
    "NetCashFromOperating": ("us-gaap/NetCashProvidedByUsedInOperatingActivities",),
    "Capex": ("us-gaap/PaymentsToAcquirePropertyPlantAndEquipment",),
    "DepreciationAmortization": (
        "us-gaap/DepreciationDepletionAndAmortization",
        "us-gaap/DepreciationAndAmortization",
    ),
    "ResearchAndDevelopmentExpense": ("us-gaap/ResearchAndDevelopmentExpense",),
    "InventoryNet": ("us-gaap/InventoryNet",),
    "PropertyPlantAndEquipmentNet": ("us-gaap/PropertyPlantAndEquipmentNet",),
    "LongTermDebtCurrent": ("us-gaap/LongTermDebtCurrent",),
    "SharesOutstanding": (
        "dei/EntityCommonStockSharesOutstanding",
        "us-gaap/CommonStockSharesOutstanding",
    ),
}

TAG_TO_CONCEPT: dict[str, str] = {tag: c for c, tags in CONCEPT_TAGS.items() for tag in tags}

# Periodic reports only; 8-K and registration statements carry few, inconsistent facts.
PERIODIC_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A"})
