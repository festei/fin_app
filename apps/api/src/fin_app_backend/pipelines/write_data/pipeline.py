"""
This is a boilerplate pipeline 'write_data'
generated using Kedro 0.17.6
"""

from kedro.pipeline import Pipeline, node
from pyrsistent import get_in

from fin_app_backend.pipelines.write_data.nodes import (
    get_intermediate_data,
)


def create_pipeline(**kwargs):
    return Pipeline(
        [
            node(
            func=get_intermediate_data,
            inputs=dict(
                data='intermediate_income_annual'
            ),
            outputs='final_income_annual',
            name='income_export',
            ),
        ]
    )

