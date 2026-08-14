from collections.abc import Mapping

import pandas as pd
import pytest

from a3docklab.platform.delta import SqlWarehouseDeltaCatalog, TableFilter


class RecordingExecutor:
    def __init__(self) -> None:
        self.query = ""
        self.parameters: Mapping[str, object] = {}

    def execute(self, query: str, parameters: Mapping[str, object]) -> pd.DataFrame:
        self.query = query
        self.parameters = parameters
        return pd.DataFrame({"event_time_ns": [10], "x_m": [1.5]})


def test_sql_catalog_pushes_down_projection_filters_and_ordering() -> None:
    executor = RecordingExecutor()
    catalog = SqlWarehouseDeltaCatalog(executor)

    result = catalog.read_table(
        "main.a3docklab.a3docklab_truth",
        filters=(
            TableFilter("run_id", "eq", "run-1"),
            TableFilter("event_time_ns", "ge", 10),
        ),
        columns=["event_time_ns", "x_m"],
        order_by=("event_time_ns",),
    )

    assert list(result.columns) == ["event_time_ns", "x_m"]
    assert executor.query == (
        "SELECT `event_time_ns`, `x_m` "
        "FROM `main`.`a3docklab`.`a3docklab_truth` "
        "WHERE `run_id` = :filter_0 AND `event_time_ns` >= :filter_1 "
        "ORDER BY `event_time_ns`"
    )
    assert executor.parameters == {"filter_0": "run-1", "filter_1": 10}


def test_sql_catalog_rejects_identifier_injection_and_writes() -> None:
    catalog = SqlWarehouseDeltaCatalog(RecordingExecutor())
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        catalog.read_table("main.safe; DROP TABLE runs")
    with pytest.raises(NotImplementedError, match="read-only"):
        catalog.append_table("main.a3docklab.truth", pd.DataFrame())
