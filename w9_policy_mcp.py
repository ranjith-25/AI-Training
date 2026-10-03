import sys
from mcp.server.mcpserver import MCPServer
from w7_tools import search_policy_exclusions as _search_policy_exclusions

mcp = MCPServer("policy-server")

@mcp.tool()
def search_policy_exclusions(policy_form: str, cause_of_loss: str) -> str:
    """You are an AI assistant that checks policy exclusions.
    Use this tool to determine if a loss cause is excluded under a specific policy form.
    If the policy_form is not found, DO NOT make up a result. Instead, ask the user to verify the form number.
    Policy forms always follow the pattern HO-XXXX (e.g. HO-0304).
    
    Args:
        policy_form: The policy form code (e.g. HO-0304).
        cause_of_loss: A description of the damage cause.
    """
    result = _search_policy_exclusions(policy_form, cause_of_loss)
    if "error" in result and "not found" in result["error"]:
        return f"Error: Policy form '{policy_form}' not found. Valid form numbers look like HO-XXXX (e.g., HO-0304). Please ask the user to double-check the form number."
    return str(result)

if __name__ == "__main__":
    mcp.run(transport='stdio')
