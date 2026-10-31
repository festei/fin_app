"""Project pipelines."""
from typing import Dict

from kedro.pipeline import Pipeline
from .pipelines import extract_data as ed
from .pipelines import write_data as wd

def register_pipelines() -> Dict[str, Pipeline]:
    """Register the project's pipelines.

    Returns:
        A mapping from a pipeline name to a ``Pipeline`` object.
    """
    data_input_pipeline = ed.create_pipeline()
    data_export_pipeline = wd.create_pipeline()

    return {
        "__default__": data_input_pipeline,
        "export": data_export_pipeline
        }
