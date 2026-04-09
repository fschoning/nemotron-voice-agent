import asyncio
import json
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.sse import sse_client

MCP_SERVER_URL = "http://192.168.1.121:16080/mcp/sse"

async def test_execution():
    print(f"🔌 Connecting to MCP server at {MCP_SERVER_URL}...")
    
    try:
        async with AsyncExitStack() as stack:
            sse_ctx = sse_client(url=MCP_SERVER_URL)
            read_stream, write_stream = await stack.enter_async_context(sse_ctx)
            
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            
            # 1. Find the resolveLocation tool
            tools_response = await session.list_tools()
            tool_name = "resolveLocation"
            tool = next((t for t in tools_response.tools if t.name == tool_name), None)
            
            if not tool:
                print(f"❌ Tool '{tool_name}' not found!")
                return
                
            print(f"\n🛠️ Found '{tool_name}'! Here is the input schema it expects from the LLM:")
            print(json.dumps(tool.inputSchema, indent=2))
            print("-" * 40)
            
            # 2. Setup test arguments (You may need to change "query" to match your schema)
            test_args = {
                "query": "London, UK" 
            }
            
            print(f"🚀 Executing {tool_name} with args: {test_args}")
            
            # 3. Call the tool and wait for the Java backend to process it
            result = await session.call_tool(tool_name, arguments=test_args)
            
            print("\n✅ Execution Successful! Data returned from Java:")
            for content in result.content:
                print(content.text)
                
    except Exception as e:
        print(f"\n❌ Execution Failed: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_execution())
