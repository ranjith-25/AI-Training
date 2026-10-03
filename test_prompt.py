import asyncio
import os
import json
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from google import genai
from google.genai import types
from dotenv import load_dotenv

def map_mcp_tool_to_gemini(mcp_tool):
    schema = mcp_tool.input_schema
    properties = {}
    required = schema.get("required", [])
    for prop_name, prop_data in schema.get("properties", {}).items():
        properties[prop_name] = types.Schema(
            type=types.Type.STRING,
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

async def main():
    load_dotenv()
    with open("w9_config.json", "r") as f:
        config = json.load(f)
    server_configs = config.get("mcpServers", {})
    gemini_funcs = []
    
    server_params = StdioServerParameters(
        command=server_configs["policy-server"]["command"],
        args=server_configs["policy-server"]["args"],
        env=None
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            for tool in tools_response.tools:
                gemini_funcs.append(map_mcp_tool_to_gemini(tool))
                
            client = genai.Client(api_key=os.getenv("API_KEY"))
            agent_tool = types.Tool(function_declarations=gemini_funcs)
            
            prompt = "What exclusions apply to water damage for policy FOOBAR?"
            print(f"User: {prompt}")
            
            response = client.models.generate_content(
                model='gemini-3.5-flash-lite',
                contents=prompt,
                config=types.GenerateContentConfig(tools=[agent_tool])
            )
            
            if response.function_calls:
                for call in response.function_calls:
                    print(f"Agent calls tool: {call.name} with args {call.args}")
                    result = await session.call_tool(call.name, call.args)
                    print(f"Result: {result.content[0].text}")
                    
                    # second turn
                    messages = [
                        types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
                        types.Content(role="model", parts=[types.Part.from_function_call(name=call.name, args=call.args)]),
                        types.Content(role="user", parts=[types.Part.from_function_response(name=call.name, response={"result": result.content[0].text})])
                    ]
                    
                    response2 = client.models.generate_content(
                        model='gemini-3.5-flash-lite',
                        contents=messages,
                        config=types.GenerateContentConfig(tools=[agent_tool])
                    )
                    print(f"Agent: {response2.text}")
            else:
                print(f"Agent: {response.text}")

if __name__ == "__main__":
    asyncio.run(main())
