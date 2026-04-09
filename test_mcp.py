import asyncio
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.sse import sse_client

# Note: Many MCP servers expose the SSE endpoint at /sse instead of /mcp
MCP_SERVER_URL = "http://192.168.1.121:16080/mcp/sse"

async def test_mcp_discovery():
    print(f"🔌 Connecting to MCP server at {MCP_SERVER_URL}...")
    
    try:
        async with AsyncExitStack() as stack:
            # 1. Connect to the SSE stream
            sse_ctx = sse_client(url=MCP_SERVER_URL)
            read_stream, write_stream = await stack.enter_async_context(sse_ctx)
            
            # 2. Establish the client session over those streams
            session_ctx = ClientSession(read_stream, write_stream)
            session = await stack.enter_async_context(session_ctx)
            
            # 3. Initialize the handshake with the server
            await session.initialize()
            print("✅ Successfully initialized session!\n")
            
            # 4. Request the list of available tools
            tools_response = await session.list_tools()
            
            print("="*40)
            print(f"🛠️  DISCOVERED {len(tools_response.tools)} TOOLS")
            print("="*40)
            
            for tool in tools_response.tools:
                print(f"Name:        {tool.name}")
                print(f"Description: {tool.description}")
                print("-" * 40)
                
    except ExceptionGroup as eg:
        print("\n❌ Connection Failed. The TaskGroup hid these exact errors:")
        for exc in eg.exceptions:
            print(f"  -> {type(exc).__name__}: {exc}")
    except Exception as e:
        print(f"\n❌ Connection Failed: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_mcp_discovery())
