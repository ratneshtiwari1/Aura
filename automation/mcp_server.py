from mcp.server.fastmcp import FastMCP

from pbir_ops import inspect_report, update_page_metadata


mcp = FastMCP("power-bi-pbir")


@mcp.tool()
def inspect_pbir_report(report_path: str) -> dict:
    """Read report settings, ordered page metadata, and visual counts."""
    return inspect_report(report_path)


@mcp.tool()
def set_page_metadata(
    report_path: str,
    page_name: str,
    display_name: str,
    width: int,
    height: int,
) -> dict:
    """Update one page's display name and canvas dimensions."""
    return update_page_metadata(report_path, page_name, display_name, width, height)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()