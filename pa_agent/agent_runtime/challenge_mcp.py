"""Child-only MCP proxy: one tool, no Job files or parent capability."""
import argparse
import os
from typing import Any

from pa_agent.agent_runtime.stage_challenge import challenge_rpc


def main() -> None:
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--batch", action="store_true")
    args = parser.parse_args()
    token = os.environ["PA_AGENT_CHILD_EVIDENCE_TOKEN"]
    mcp = MCPServer("pa-agent-scoped-child-evidence")

    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                  idempotentHint=True, openWorldHint=False)

    def read_challenge_resources() -> dict[str, Any]:
        """Read the complete finite Host-authorized scope once, preserving every original value."""
        try:
            result = challenge_rpc(args.endpoint, token, {"action": "read_all"})
            if any("error" in row for row in result["resources"]):
                raise ValueError("scoped evidence denied or budget exhausted")
            return result
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    def read_challenge_resource(resource_index: int) -> dict[str, Any]:
        """Read one resource from this task's finite, Host-authorized frozen index."""
        try:
            return challenge_rpc(args.endpoint, token, {"action": "read", "resource_index": resource_index})
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    mcp.add_tool(read_challenge_resources if args.batch else read_challenge_resource,
                 structured_output=True, annotations=annotations)

    mcp.run()


if __name__ == "__main__":
    main()
