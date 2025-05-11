import argparse
import asyncio
import json
import os
import re
import shlex
import sys
from urllib.parse import urljoin

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import JSONRPCRequest, JSONRPCResponse
from pydantic import BaseModel
from pygments import highlight
from pygments.lexers import JsonLexer
from pygments.formatters import TerminalFormatter


METHODS = (
    "prompts/list",
    "prompts/get",
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "tools/list",
    "tools/call",
)


def print_json(result: BaseModel) -> None:
    """Print the given result object with syntax highlighting."""
    json_str = result.model_dump_json(indent=2, exclude_defaults=True)
    if not sys.stdout.isatty():
        print(json_str)
    else:
        highlighted = highlight(json_str, JsonLexer(), TerminalFormatter())
        print(highlighted)


async def invoke(
    cmd_or_url: str, method: str, params: dict, verbose: bool = False
) -> None:
    if verbose:
        print("Request:")
        print_json(
            JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method=method,
                params=params or None,
            )
        )

    if cmd_or_url.startswith(("http://", "https://")):
        # SSE transport
        url = urljoin(cmd_or_url, "/sse")
        client = sse_client(url=url)
    else:
        # STDIO transport
        elements = shlex.split(cmd_or_url)
        if not elements:
            raise ValueError("stdio command is empty")

        command, args = elements[0], elements[1:]
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=os.environ,  # pass all env vars to the server
        )
        client = stdio_client(server_params)

    async with client as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            match method:
                case "prompts/list":
                    result = await session.list_prompts()

                case "prompts/get":
                    result = await session.get_prompt(**params)

                case "resources/list":
                    result = await session.list_resources()

                case "resources/read":
                    result = await session.read_resource(**params)

                case "resources/templates/list":
                    result = await session.list_resource_templates()

                case "tools/list":
                    result = await session.list_tools()

                case "tools/call":
                    result = await session.call_tool(**params)

                case _:
                    raise ValueError(f"Unknown method: {method}")

            if verbose:
                print("Response:")
                print_json(
                    JSONRPCResponse(
                        jsonrpc="2.0",
                        id=1,
                        result=result.model_dump(exclude_defaults=True),
                    )
                )
            else:
                print_json(result)


def parse_params(params: list[str]):
    """Parse parameters in the form of `key=string_value` or `key:=json_value`."""

    # Regular expression pattern
    PATTERN = re.compile(r"^([^=:]+)(=|:=)(.+)$")

    def parse(param: str) -> tuple:
        match = PATTERN.match(param)
        if not match:
            raise ValueError(f"Invalid parameter: {param!r}")

        key, separator, value = match.groups()
        parsed_value = value  # String field
        if separator == ":=":  # Raw JSON field
            try:
                parsed_value = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON value: {value!r}")
        return key, parsed_value

    return dict(parse(param) for param in params)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A command-line utility for interacting with MCP servers."
    )
    parser.add_argument(
        "cmd_or_url",
        help="The command (stdio-transport) or URL (sse-transport) to connect to the MCP server",
    )
    parser.add_argument("method", help="The method to be invoked")
    parser.add_argument(
        "params",
        nargs="*",
        help="The parameter values, in the form of `key=string_value` or `key:=json_value`",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output showing request/response details",
    )
    args = parser.parse_args()

    if args.method not in METHODS:
        parser.error(
            f"Invalid method: {args.method} (choose from {', '.join(METHODS)})."
        )

    try:
        params = parse_params(args.params)
    except ValueError as exc:
        parser.error(str(exc))

    asyncio.run(invoke(args.cmd_or_url, args.method, params, args.verbose))


if __name__ == "__main__":
    main()
