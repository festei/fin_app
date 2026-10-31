"""
This is a boilerplate pipeline 'extract_data'
generated using Kedro 0.17.6
"""
import pandas as pd
import yfinance as yf


def get_raw_data(data, company: str) -> pd.DataFrame:
    ics_data = data.json()
    ics_data['comp'] = company
    return pd.DataFrame(ics_data['annualReports'])


def get_yf_data(company: str) -> pd.DataFrame:
    stock = yf.Ticker(company)
    hist = stock.history(period="max")
    data = hist['Open'].reset_index()

    return data
