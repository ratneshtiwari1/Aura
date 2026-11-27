import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = Path(__file__).with_name("mcp_server.py")


async def call_tool(tool_name: str, arguments: dict) -> str:
    server = StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)])
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            if result.isError:
                raise RuntimeError(f"MCP tool failed: {result.content}")
            return "\n".join(item.text for item in result.content if hasattr(item, "text"))


def list_fabric_workspaces() -> str:
    api_url = os.environ.get("FABRIC_API_URL", "http://127.0.0.1:8000").rstrip("/")
    with urlopen(f"{api_url}/v1/workspaces", timeout=30) as response:
        return json.dumps(json.load(response), indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Coordinate Power BI PBIR authoring tools")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser("inspect", help="Inspect a PBIR report through MCP")
    inspect_parser.add_argument("report_path", help="Report folder relative to the workspace root")

    page_parser = commands.add_parser("set-page", help="Set PBIR page metadata through MCP")
    page_parser.add_argument("report_path", help="Report folder relative to the workspace root")
    page_parser.add_argument("page_name")
    page_parser.add_argument("display_name")
    page_parser.add_argument("--width", type=int, default=1280)
    page_parser.add_argument("--height", type=int, default=720)
    commands.add_parser("workspaces", help="List Fabric workspaces through the local FastAPI service")

    arguments = parser.parse_args()
    if arguments.command == "workspaces":
        print(list_fabric_workspaces())
        return
    if arguments.command == "inspect":
        tool_name = "inspect_pbir_report"
        tool_arguments = {"report_path": arguments.report_path}
    else:
        tool_name = "set_page_metadata"
        tool_arguments = {
            "report_path": arguments.report_path,
            "page_name": arguments.page_name,
            "display_name": arguments.display_name,
            "width": arguments.width,
            "height": arguments.height,
        }

    print(asyncio.run(call_tool(tool_name, tool_arguments)))


if __name__ == "__main__":
    main()