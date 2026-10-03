import sys
from mcp.server.mcpserver import MCPServer
from w7_tools import get_claim as _get_claim, get_adjuster_notes as _get_adjuster_notes

mcp = MCPServer("claims-system")

@mcp.tool()
def get_claim(claim_id: str) -> str:
    """Retrieve the details of an insurance claim.
    
    Args:
        claim_id: The ID of the claim to retrieve.
    """
    return str(_get_claim(claim_id))

@mcp.tool()
def get_adjuster_notes(claim_id: str) -> str:
    """Retrieve the adjuster notes for a claim.
    
    Args:
        claim_id: The ID of the claim.
    """
    return str(_get_adjuster_notes(claim_id))

if __name__ == "__main__":
    mcp.run(transport='stdio')
