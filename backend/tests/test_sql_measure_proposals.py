from backend.api.datasource import _sql_measure_proposals


def test_sql_measure_proposals_skip_identifiers_and_choose_safe_aggregation() -> None:
    proposals = _sql_measure_proposals([
        {"name": "CustomerID", "dataType": "bigint"},
        {"name": "Fiscal Year", "dataType": "int"},
        {"name": "Units", "dataType": "int"},
        {"name": "Defect Rate", "dataType": "decimal"},
        {"name": "Description", "dataType": "nvarchar"},
    ])

    assert proposals == [
        {"name": "Total Units", "column": "Units", "aggregation": "SUM", "status": "proposal"},
        {"name": "Average Defect Rate", "column": "Defect Rate", "aggregation": "AVERAGE", "status": "proposal"},
    ]
