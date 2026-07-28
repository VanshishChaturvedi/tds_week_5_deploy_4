import hashlib
import contextvars
import os
from fastapi import FastAPI, Request
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
import uvicorn

# Context variable to hold the HTTP header across the async request lifecycle
challenge_var = contextvars.ContextVar("challenge", default="")

# Initialize FastAPI and the MCP Server
app = FastAPI(title="Exam MCP Server")
mcp_server = Server("exam-server")

# The EXACT registered exam email required by the grader
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
    
    # Read the fresh challenge from the HTTP headers via the context variable
    challenge = challenge_var.get()
    
    # Format: ${challenge}:${normalizedEmail}
    text_to_hash = f"{challenge}:{EMAIL}"
    
    # Generate SHA-256 hash and extract the first 16 lowercase hex characters
    hash_hex = hashlib.sha256(text_to_hash.encode("utf-8")).hexdigest()[:16]
    
    # Return as a single MCP text content block
    return [TextContent(type="text", text=hash_hex)]

# Setup the standard MCP SSE Transport
# We bind the messages endpoint to /sse to match the grader's URL validation
sse = SseServerTransport("/sse")

@app.get("/")
async def root():
    """Returns a 200 OK if the grader pings the root domain."""
    return {"status": "Server is live"}

@app.get("/sse")
async def endpoint_sse_get(request: Request):
    """Handles the initial MCP SSE connection (GET)."""
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp_server.run(
            streams[0],
            streams[1],
            mcp_server.create_initialization_options()
        )

@app.post("/sse")
async def endpoint_sse_post(request: Request):
    """Handles the JSON-RPC tool calls OR validation pings from the grader (POST)."""
    # Extract the X-Exam-Challenge header
    challenge = request.headers.get("X-Exam-Challenge", "")
    
    # Inject it into the async context so handle_call_tool can read it
    challenge_var.set(challenge)
    
    # The grader sends a POST request to validate the URL before initiating SSE.
    # If there is no sessionId in the query parameters, it's just a validation ping.
    if "sessionId" not in request.query_params:
        return {"status": "URL valid"}
        
    # Hand off the request to the MCP transport to process the JSON-RPC body
    await sse.handle_post_message(request.scope, request.receive, request._send)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
