import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import re
import shlex
import sys
from typing import Any
from urllib.parse import urljoin

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import JSONRPCRequest, JSONRPCResponse, Result
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


@asynccontextmanager
async def simplified_streamablehttp_client(*args, **kwargs):
    """Simplified version of streamablehttp_client(), which only returns (read, write) tuple.

    Usage example:
        async with simplified_streamablehttp_client(...) as (read, write):
            ...
    """
    async with streamablehttp_client(*args, **kwargs) as (read, write, _):
        yield (read, write)

async def log_response_body(response: httpx.Response):
        """Logs the response body."""
        try:
            print(f"============== HTTP RESPONSE ================")
            print(f"URL: {response.url}")
            await response.aread()
            print(f"Status Code: {response.status_code}")
            print(f"Body: {response.text}")
        except Exception as e:
            print(f"Error logging response body: {e}")
        print(f"-------------- /HTTP RESPONSE ---------------")


async def log_request_body(request: httpx.Request):
        """Logs the request body."""
        try:
            print(f"============== HTTP REQUEST ================")
            print(f"URL: {request.url}")
            if request.content:
                req_body = request.content.decode()
                try:
                    parsed = json.loads(req_body)
                    pretty = json.dumps(parsed, indent=2)
                    print(f"Body: {pretty}")
                except json.JSONDecodeError:
                    print(f"Body: {req_body}")
            else:
                print("Body: None")
        except Exception as e:
            print(f"Error logging request body: {e}")
        print(f"-------------- /HTTP REQUEST ---------------")

def httpx_tracing_client_factory(*args, **kwargs):
    default_client: httpx.AsyncClient = create_mcp_http_client(*args, **kwargs)
    default_client.event_hooks = default_client.event_hooks if default_client.event_hooks else {}
    default_client.event_hooks.update({
        "request": [log_request_body],
        "response": [log_response_body]
    })
    return default_client


class Client(BaseModel):
    cmd_or_url: str
    method: str
    params: dict[str, Any]

    metadata: dict[str, str]
    """Additional metadata.

    STDIO transport:
    - The key/value pairs are passed as environment variables to the server.

    SSE transport:
    - The key/value pairs are passed as HTTP headers to the server.
    """

    async def invoke(self, verbose: bool) -> Result:
        if self.cmd_or_url.startswith(("http://", "https://")):
            url = self.cmd_or_url
            headers = self.metadata or None
            if url.endswith("/sse"):
                # Explicitly specified SSE transport.
                client = sse_client(url=url, headers=headers)
            else:
                # Default to Streamable HTTP transport.
                if not (url.endswith("/mcp") or url.endswith("/mcp/")):
                    url = url.removesuffix("/") + "/mcp/"
                if verbose:
                    client_factory = httpx_tracing_client_factory
                else:
                    client_factory = create_mcp_http_client
                client = simplified_streamablehttp_client(
                    httpx_client_factory=client_factory,
                    url=url,
                    headers=headers
                )
        else:
            # STDIO transport
            elements = shlex.split(self.cmd_or_url)
            if not elements:
                raise ValueError("stdio command is empty")

            command, args = elements[0], elements[1:]
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=self.metadata or None,
            )
            client = stdio_client(server_params)

        if verbose:
            self.show_jsonrpc_request()
            
        async with client as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()


                match self.method:
                    case "prompts/list":
                        result = await session.list_prompts()

                    case "prompts/get":
                        result = await session.get_prompt(**self.params)

                    case "resources/list":
                        result = await session.list_resources()

                    case "resources/read":
                        result = await session.read_resource(**self.params)

                    case "resources/templates/list":
                        result = await session.list_resource_templates()

                    case "tools/list":
                        result = await session.list_tools()

                    case "tools/call":
                        result = await session.call_tool(**self.params)

                    case _:
                        raise ValueError(f"Unsupported method: {self.method}")

                if verbose:
                    self.show_jsonrpc_response(result)
                else:
                    print_json(result)

                return result

    def show_jsonrpc_request(self) -> None:
        print("Request:")
        print_json(
            JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method=self.method,
                params=self.params or None,
            )
        )

    def show_jsonrpc_response(self, result: Result) -> None:
        print("Response:")
        print_json(
            JSONRPCResponse(
                jsonrpc="2.0",
                id=1,
                result=result.model_dump(exclude_defaults=True),
            )
        )


def print_json(result: BaseModel) -> None:
    """Print the given result object with syntax highlighting."""
    json_str = result.model_dump_json(indent=2, exclude_defaults=True)
    if not sys.stdout.isatty():
        print(json_str)
    else:
        highlighted = highlight(json_str, JsonLexer(), TerminalFormatter())
        print(highlighted)


def parse_items(items: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Parse items in the form of `key:value`, `key=string_value` or `key:=json_value`."""

    # Regular expression pattern
    PATTERN = re.compile(r"^([^:=]+)(=|:=|:)(.+)$", re.DOTALL)

    params: dict[str, Any] = {}
    metadata: dict[str, str] = {}

    def parse(item: str) -> None:
        match = PATTERN.match(item)
        if not match:
            raise ValueError(f"Invalid item: {item!r}")

        key, separator, value = match.groups()
        match separator:
            case "=":  # String field
                params[key] = value
            case ":=":  # Raw JSON field
                try:
                    parsed_value = json.loads(value)
                    params[key] = parsed_value
                except json.JSONDecodeError:
                    raise ValueError(f"Invalid JSON value: {value!r}")
            case ":":  # Metadata
                metadata[key] = value
            case _:
                raise ValueError(f"Unsupported separator: {separator!r}")

    for item in items:
        parse(item)

    return params, metadata


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
        "items",
        nargs="*",
        help="""\
The parameter values (in the form of `key=string_value` or `key:=json_value`),
or the metadata values (in the form of `key:value`)\
""",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output showing JSON-RPC request/response",
    )
    args = parser.parse_args()

    print(f"Verbose: {args.verbose}")

    # if args.verbose:
    #     logging.basicConfig(
    #         level=logging.DEBUG,
    #         format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    #         datefmt='%H:%M:%S',
    #         stream=sys.stderr
    #     )
    #     logging.getLogger("asyncio").setLevel(logging.DEBUG)
    #     logging.getLogger("mcp").setLevel(logging.DEBUG)
    #     logging.getLogger("streamable_http").setLevel(logging.INFO)

    if args.method not in METHODS:
        parser.error(
            f"Invalid method: {args.method} (choose from {', '.join(METHODS)})."
        )

    try:
        params, metadata = parse_items(args.items)
    except ValueError as exc:
        parser.error(str(exc))

    client = Client(
        cmd_or_url=args.cmd_or_url,
        method=args.method,
        params=params,
        metadata=metadata,
    )
    asyncio.run(client.invoke(args.verbose))


if __name__ == "__main__":
    main()
