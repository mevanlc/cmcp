# cMCP

`cmcp` is a command-line utility that helps you interact with [MCP][1] servers. It's basically `curl` for MCP servers.


## Installation

```bash
pip install cmcp
```


## Quick Start

Given the following MCP Server (taken from [here][2]):

```python
# server.py
from mcp.server.fastmcp import FastMCP

# Create an MCP server
mcp = FastMCP("Demo")


# Add an addition tool
@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


# Add a dynamic greeting resource
@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello, {name}!"
```

### STDIO transport

List resources:

```bash
cmcp 'mcp run server.py' resources/list
```

Read a resource:

```bash
cmcp 'mcp run server.py' resources/read
```

List tools:

```bash
cmcp 'mcp run server.py' tools/list
```

Call a tool:

```bash
cmcp 'mcp run server.py' tools/call -d '{"name": "add", "arguments": {"a": 1, "b": 2}}'
```

### SSE transport

Run the above MCP server with SSE transport:

```bash
mcp run server.py -t sse
```

List resources:

```bash
cmcp http://localhost:8000 resources/list
```

Read a resource:

```bash
cmcp http://localhost:8000 resources/read
```

List tools:

```bash
cmcp http://localhost:8000 tools/list
```

Call a tool:

```bash
cmcp http://localhost:8000 tools/call -d '{"name": "add", "arguments": {"a": 1, "b": 2}}'
```


[1]: https://modelcontextprotocol.io
[2]: https://github.com/modelcontextprotocol/python-sdk#quickstart
