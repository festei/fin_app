from pydantic import BaseModel

class income_data(BaseModel):
    fiscalDateEnding: str
    reportedCurrency: str
    grossProfit: str
    totalRevenue: str
    costOfRevenue: str
    costofGoodsAndServicesSold: str
    operatingIncome: str
    sellingGeneralAndAdministrative: str
    researchAndDevelopment: str
    operatingExpenses: str
    investmentIncomeNet: str
    netstrerestIncom: str
    strerestIncome: str
    strerestExpense: str
    nonstrerestIncome: str
    otherNonOperatingIncome: str
    depreciation: str
    depreciationAndAmortization: str
    incomeBeforeTax: str
    incomeTaxExpense: str
    strerestAndDebtExpense: str
    netIncomeFromContinuingOperations: str
    comprehensiveIncomeNetOfTax: str
    ebit: str
    ebitda: str
    netIncome: str

    class Config:
        orm_mode = True