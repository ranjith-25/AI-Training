import sys
from mcp.server.fastmcp import FastMCP
from w7_tools import search_policy_exclusions as _search_policy_exclusions

mcp = FastMCP("policy-server")

@mcp.tool()
def search_policy_exclusions(policy_form: str, cause_of_loss: str) -> str:
    """Search the policy documents for exclusions related to the cause of loss.
    
    Args:
        policy_form: The policy form code (e.g. HO-0304).
        cause_of_loss: A description of the damage cause.
    """
    return _search_policy_exclusions(policy_form, cause_of_loss)

if __name__ == "__main__":
    mcp.run(transport='stdio')
