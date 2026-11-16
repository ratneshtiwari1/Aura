"""Create a plain six-sheet planning workbook for AURA demonstrations."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableColumn


OUTPUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "AURA_Six_Sheet_Native_Tables_Demo.xlsx"


def add_rows(sheet, headers: list[str], rows: list[tuple[object, ...]]) -> None:
    add_table_at(sheet, 1, 1, headers, rows)


def add_table_at(
    sheet,
    start_row: int,
    start_column: int,
    headers: list[str],
    rows: list[tuple[object, ...]],
) -> None:
    """Write a separate, plain data region without Excel table styling."""
    for column_offset, value in enumerate(headers):
        sheet.cell(start_row, start_column + column_offset, value)
    for row_offset, row in enumerate(rows, start=1):
        for column_offset, value in enumerate(row):
            sheet.cell(start_row + row_offset, start_column + column_offset, value)
    add_native_table(sheet, start_row, start_column, len(headers), len(rows))


def add_native_table(sheet, start_row: int, start_column: int, column_count: int, row_count: int) -> None:
    """Register the range as a native Excel table without a visual table style."""
    end_row = start_row + row_count
    end_column = start_column + column_count - 1
    ref = f"{get_column_letter(start_column)}{start_row}:{get_column_letter(end_column)}{end_row}"
    safe_sheet_name = "".join(character for character in sheet.title if character.isalnum())
    display_name = f"AURA_{safe_sheet_name}_{start_row}_{start_column}"
    table = Table(displayName=display_name, ref=ref)
    table.tableColumns = [
        TableColumn(id=index + 1, name=str(sheet.cell(start_row, start_column + index).value))
        for index in range(column_count)
    ]
    sheet.add_table(table)


def main() -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)

    sales = workbook.create_sheet("Net Revenue")
    add_rows(sales, ["Fiscal Year", "Quarter", "Business Unit", "Region", "Scenario", "Net Revenue"], [
        (2025, "Q1", "Sparkling", "North", "Actual", 1280000),
        (2025, "Q2", "Sparkling", "North", "Actual", 1345000),
        (2025, "Q1", "Hydration", "South", "Actual", 890000),
        (2025, "Q2", "Hydration", "South", "Actual", 945000),
        (2026, "Q1", "Sparkling", "North", "Budget", 1420000),
        (2026, "Q2", "Sparkling", "North", "Budget", 1510000),
        (2026, "Q1", "Hydration", "South", "Budget", 1015000),
        (2026, "Q2", "Hydration", "South", "Budget", 1090000),
    ])
    add_table_at(sales, 2, 9, ["Fiscal Year", "Region", "Revenue Target", "Revenue Variance"], [
        (2025, "North", 2700000, -25000), (2025, "South", 1900000, -65000),
        (2026, "North", 3000000, -70000), (2026, "South", 2150000, -45000),
    ])
    add_table_at(sales, 14, 1, ["Business Unit", "Customer Segment", "Net Revenue"], [
        ("Sparkling", "Retail", 1820000), ("Sparkling", "Foodservice", 1110000),
        ("Hydration", "Retail", 1430000), ("Hydration", "Foodservice", 675000),
    ])

    cogs = workbook.create_sheet("COGS")
    add_rows(cogs, ["Fiscal Year", "Quarter", "Business Unit", "Product Group", "Scenario", "COGS"], [
        (2025, "Q1", "Sparkling", "Trademark Coca-Cola", "Actual", 516000),
        (2025, "Q2", "Sparkling", "Trademark Coca-Cola", "Actual", 539000),
        (2025, "Q1", "Hydration", "Water", "Actual", 356000),
        (2025, "Q2", "Hydration", "Water", "Actual", 372000),
        (2026, "Q1", "Sparkling", "Trademark Coca-Cola", "Budget", 560000),
        (2026, "Q2", "Sparkling", "Trademark Coca-Cola", "Budget", 590000),
        (2026, "Q1", "Hydration", "Water", "Budget", 401000),
        (2026, "Q2", "Hydration", "Water", "Budget", 425000),
    ])
    add_table_at(cogs, 2, 9, ["Fiscal Year", "Cost Driver", "Actual Cost", "Budget Cost"], [
        (2025, "Sugar", 255000, 269000), (2025, "Packaging", 318000, 325000),
        (2026, "Sugar", 278000, 285000), (2026, "Packaging", 349000, 360000),
    ])
    add_table_at(cogs, 14, 1, ["Business Unit", "COGS % of Revenue", "Cost Improvement %"], [
        ("Sparkling", 39.25, 1.80), ("Hydration", 39.24, 2.10),
    ])

    margin = workbook.create_sheet("Margin Decomp")
    add_rows(margin, ["Fiscal Year", "Business Unit", "Scenario", "Net Revenue", "COGS", "Gross Profit", "Gross Margin %"], [
        (2025, "Sparkling", "Actual", 2625000, 1055000, 1570000, 59.81),
        (2025, "Hydration", "Actual", 1835000, 728000, 1107000, 60.33),
        (2026, "Sparkling", "Budget", 2930000, 1150000, 1780000, 60.75),
        (2026, "Hydration", "Budget", 2105000, 826000, 1279000, 60.76),
    ])
    add_table_at(margin, 2, 10, ["Fiscal Year", "Margin Driver", "Impact"], [
        (2025, "Price", 82000), (2025, "Mix", 54000), (2025, "Input Cost", -63000),
        (2026, "Price", 97000), (2026, "Mix", 68000), (2026, "Input Cost", -51000),
    ])
    add_table_at(margin, 12, 1, ["Business Unit", "Gross Margin Target %", "Gross Margin Actual %"], [
        ("Sparkling", 60.50, 60.75), ("Hydration", 60.25, 60.76),
    ])

    market = workbook.create_sheet("Industry & Market Share")
    add_rows(market, ["Fiscal Year", "Region", "Category", "Scenario", "Industry Value", "Company Value", "Market Share %"], [
        (2025, "North", "Sparkling", "Actual", 10200000, 2675000, 26.23),
        (2025, "South", "Hydration", "Actual", 6800000, 1835000, 26.99),
        (2026, "North", "Sparkling", "Budget", 10800000, 2960000, 27.41),
        (2026, "South", "Hydration", "Budget", 7200000, 2115000, 29.38),
    ])
    add_table_at(market, 2, 10, ["Fiscal Year", "Competitor", "Category", "Market Share %"], [
        (2025, "Competitor A", "Sparkling", 31.4), (2025, "Competitor B", "Hydration", 22.8),
        (2026, "Competitor A", "Sparkling", 30.9), (2026, "Competitor B", "Hydration", 22.1),
    ])
    add_table_at(market, 12, 1, ["Region", "Population", "Per Capita Consumption"], [
        ("North", 12200000, 96.5), ("South", 9800000, 81.2),
    ])

    execution = workbook.create_sheet("Commercial Execution")
    add_rows(execution, ["Fiscal Year", "Month", "Region", "Channel", "Outlet Coverage %", "Cooler Compliance %", "Execution Score"], [
        (2025, "January", "North", "Modern Trade", 82.4, 76.8, 79.6),
        (2025, "February", "North", "Modern Trade", 83.1, 78.0, 80.6),
        (2025, "January", "South", "Traditional Trade", 71.8, 68.5, 70.2),
        (2025, "February", "South", "Traditional Trade", 73.5, 69.9, 71.7),
        (2026, "January", "North", "Modern Trade", 85.2, 80.1, 82.7),
        (2026, "February", "South", "Traditional Trade", 76.4, 73.2, 74.8),
    ])
    add_table_at(execution, 2, 10, ["Fiscal Year", "Region", "Available Outlets", "Activated Outlets"], [
        (2025, "North", 12500, 10300), (2025, "South", 9800, 7050),
        (2026, "North", 12700, 10820), (2026, "South", 10050, 7680),
    ])
    add_table_at(execution, 12, 1, ["Channel", "Display Compliance %", "Price Compliance %"], [
        ("Modern Trade", 84.2, 88.6), ("Traditional Trade", 72.4, 79.1),
    ])

    sustainability = workbook.create_sheet("Sustainability")
    add_rows(sustainability, ["Fiscal Year", "Market", "Metric", "Scenario", "Target", "Actual", "Unit"], [
        (2025, "North", "Water Replenishment", "Actual", 100, 94, "%"),
        (2025, "South", "Water Replenishment", "Actual", 100, 91, "%"),
        (2025, "North", "Recycled Content", "Actual", 50, 46, "%"),
        (2025, "South", "Recycled Content", "Actual", 50, 43, "%"),
        (2026, "North", "Water Replenishment", "Budget", 100, 98, "%"),
        (2026, "South", "Recycled Content", "Budget", 55, 51, "%"),
    ])
    add_table_at(sustainability, 2, 10, ["Fiscal Year", "Market", "Emissions Target", "Emissions Actual", "Unit"], [
        (2025, "North", 82000, 84500, "tCO2e"), (2025, "South", 63000, 65200, "tCO2e"),
        (2026, "North", 79000, 77500, "tCO2e"), (2026, "South", 60000, 59400, "tCO2e"),
    ])
    add_table_at(sustainability, 12, 1, ["Market", "Community Programs", "Beneficiaries"], [
        ("North", 14, 5200), ("South", 11, 3850),
    ])

    workbook.save(OUTPUT_PATH)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
