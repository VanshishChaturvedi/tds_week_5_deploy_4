import hashlib
import json
import os
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
import uvicorn

app = FastAPI(title="Exam MCP Server")

# 1. Enable CORS for the grader (required if the grader uses a web UI)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

mcp_server = Server("exam-server")
EMAIL = "24f2003215@ds.study.iitm.ac.in"

@mcp_server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="solve_challenge",
            description="Solves the exam challenge using SHA-256 hashing.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]

@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "solve_challenge":
        raise ValueError(f"Unknown tool: {name}")
    
    # Extract the challenge we safely injected from the HTTP headers
    challenge = arguments.get("_challenge_header", "")
    
    # Format: ${challenge}:${normalizedEmail}
    text_to_hash = f"{challenge}:{EMAIL}"
    hash_hex = hashlib.sha256(text_to_hash.encode("utf-8")).hexdigest()[:16]
    
    return [TextContent(type="text", text=hash_hex)]

# 2. Standard MCP transport setup routing JSON-RPC POSTs to /messages
sse = SseServerTransport("/messages")

@app.get("/sse")
async def endpoint_sse_get(request: Request):
    """Handles the initial MCP SSE connection."""
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp_server.run(
            streams[0],
            streams[1],
            mcp_server.create_initialization_options()
        )

@app.route("/sse", methods=["POST", "HEAD"])
async def endpoint_sse_ping(request: Request):
    """Handles the initial validation pings from the grader directly on the /sse URL."""
    return Response(status_code=200)

@app.post("/messages")
async def endpoint_messages(request: Request):
    """Handles JSON-RPC messages and safely extracts HTTP headers without hanging ASGI."""
    # Read the challenge from the header
    challenge = request.headers.get("X-Exam-Challenge", "")
    
    # Consume the body safely
    body_bytes = await request.body()
    try:
        data = json.loads(body_bytes)
        # If it is a tool call, inject the header directly into the JSON arguments
        # so the isolated tool execution context can access it cleanly.
        if isinstance(data, dict) and data.get("method") == "tools/call":
            if "params" not in data:
                data["params"] = {}
            if "arguments" not in data["params"]:
                data["params"]["arguments"] = {}
            data["params"]["arguments"]["_challenge_header"] = challenge
        
        modified_body = json.dumps(data).encode("utf-8")
    except Exception:
        # Fallback if parsing fails for any reason
        modified_body = body_bytes

    # Reconstruct the ASGI stream state to prevent the transport from timing out
    received = False
    async def mock_receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": modified_body, "more_body": False}
        return {"type": "http.disconnect"}
        
    await sse.handle_post_message(request.scope, mock_receive, request._send)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
