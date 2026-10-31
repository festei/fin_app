"""
This is a boilerplate pipeline 'extract_data'
generated using Kedro 0.17.6
"""

from kedro.pipeline import Pipeline, node

from fin_app_backend.pipelines.extract_data.nodes import (
    get_raw_data,
    get_yf_data
)


def create_pipeline(**kwargs):
    return Pipeline(
        [
            node(
                func=get_raw_data,
                inputs=dict(
                    data='raw_income_statement',
                    company='params:company'
                ),
                outputs='intermediate_income_annual',
                name='income_import',
            ),
            node(
                func=get_raw_data,
                inputs=dict(
                    data='raw_balance_statement',
                    company='params:company'
                ),
                outputs='intermediate_cashflow_annual',
                name='balance_import',
            ),
            node(
                func=get_raw_data,
                inputs=dict(
                    data='raw_cashflow_statement',
                    company='params:company'
                ),
                outputs='intermediate_balance_annual',
                name='cashflow_import',
            ),
            node(
                func=get_yf_data,
                inputs=dict(
                    company='params:company'
                ),
                outputs='yahoo_data',
                name='history_import',
            ),
        ]
    )
