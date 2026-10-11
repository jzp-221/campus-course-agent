"""Use the existing real MCP server only after verifying the isolated target."""

from deploy.demo.seed import require_demo_target

require_demo_target()

from app.config import settings
from mcp_server import mcp


if __name__ == "__main__":
    mcp.run(transport="http", host=settings.MCP_HOST, port=settings.MCP_PORT,
            path=settings.MCP_PATH, uvicorn_config={"ws": "websockets", "access_log": False})
