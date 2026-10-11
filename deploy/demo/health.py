"""Container readiness probes: never query business data or an external provider."""

import asyncio
import json
import sys
from urllib.request import urlopen


def check_api() -> None:
    with urlopen("http://127.0.0.1:8000/api/demo/health", timeout=5) as response:
        payload = json.load(response)
    if payload.get("status") != "ok":
        raise RuntimeError("API route readiness failed")


async def check_mcp() -> None:
    from fastmcp import Client

    async with Client("http://127.0.0.1:8001/mcp") as client:
        tools = await client.list_tools()
    expected = {"get_my_schedules", "save_study_plan", "search_course_docs"}
    if not expected.issubset({tool.name for tool in tools}):
        raise RuntimeError("MCP tool readiness failed")


def main() -> int:
    try:
        target = sys.argv[1] if len(sys.argv) == 2 else ""
        if target == "api":
            check_api()
        elif target == "mcp":
            asyncio.run(asyncio.wait_for(check_mcp(), timeout=7))
        else:
            raise ValueError("expected api or mcp")
    except Exception:
        # A Docker health result is public to operators; never print credentials.
        print("readiness failed", file=sys.stderr)
        return 1
    print("ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
