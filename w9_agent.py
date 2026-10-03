import json
import asyncio
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from google import genai
from google.genai import types

def map_mcp_tool_to_gemini(mcp_tool):
    """Convert an MCP tool definition to Gemini's format."""
    schema = mcp_tool.input_schema
    properties = {}
    required = schema.get("required", [])
    
    for prop_name, prop_data in schema.get("properties", {}).items():
        prop_type = prop_data.get("type", "string").upper()
        if prop_type == "STRING":
            prop_type_enum = types.Type.STRING
        elif prop_type == "NUMBER":
            prop_type_enum = types.Type.NUMBER
        elif prop_type == "INTEGER":
            prop_type_enum = types.Type.INTEGER
        elif prop_type == "BOOLEAN":
            prop_type_enum = types.Type.BOOLEAN
        elif prop_type == "ARRAY":
            prop_type_enum = types.Type.ARRAY
        elif prop_type == "OBJECT":
            prop_type_enum = types.Type.OBJECT
        else:
            prop_type_enum = types.Type.STRING
            
        properties[prop_name] = types.Schema(
            type=prop_type_enum,
            description=prop_data.get("description", "")
        )
        
    return types.FunctionDeclaration(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties=properties,
            required=required
        )
    )

from dotenv import load_dotenv

async def main():
    load_dotenv()
    # Load config
    with open("w9_config.json", "r") as f:
        config = json.load(f)
        
    server_configs = config.get("mcpServers", {})
    
    # We will hold all open sessions here
    sessions = {}
    tool_map = {}  # tool_name -> session
    gemini_funcs = []
    
    async with AsyncExitStack() as stack:
        # Initialize all servers from config
        for server_name, s_config in server_configs.items():
            server_params = StdioServerParameters(
                command=s_config["command"],
                args=s_config["args"],
                env=None
            )
            # Connect over stdio
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
            
            # Start session
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            
            sessions[server_name] = session
            
            # Discover tools
            tools_response = await session.list_tools()
            for tool in tools_response.tools:
                tool_map[tool.name] = session
                gemini_funcs.append(map_mcp_tool_to_gemini(tool))
                
        # Report discovered tools
        print(f"DISCOVERED TOOLS ({len(gemini_funcs)}): {[f.name for f in gemini_funcs]}")
        
        # Now run a simple loop with the LLM
        import os
        api_key = os.getenv("API_KEY")
        client = genai.Client(api_key=api_key)
        agent_tool = types.Tool(function_declarations=gemini_funcs)
        
        prompt = "What are the details of claim CLM-2024-7001?"
        print(f"User: {prompt}")
        
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[agent_tool]
            )
        )
        
        if response.function_calls:
            for call in response.function_calls:
                print(f"Agent calls tool: {call.name} with args {call.args}")
                session = tool_map.get(call.name)
                if session:
                    try:
                        result = await session.call_tool(call.name, call.args)
                        print(f"Result: {result.content[0].text}")
                    except Exception as e:
                        print(f"Tool error: {e}")
                else:
                    print(f"Tool {call.name} not found in any session.")
        else:
            print(f"Agent: {response.text}")

if __name__ == "__main__":
    asyncio.run(main())
