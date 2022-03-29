from sqlalchemy.orm import Session
import pandas as pd
from fastapi import FastAPI

from core.models.database import engine, income_table
from core.schemas.schema import income_data

app = FastAPI()

@app.get("/get_income_data")
async def get_income_data() -> income_data:
    with Session(engine) as session:
        query = session.query(income_table)

    return pd.read_sql(query.statement, query.session.bind)
