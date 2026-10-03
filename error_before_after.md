# Before / After Transcript: Recoverable Errors via Docstring Prompts

## Scenario
The user asks: "What exclusions apply to water damage for policy FOOBAR?"
(The policy form `FOOBAR` does not exist; valid forms look like `HO-XXXX`).

---

### BEFORE (Standard Docstring, Unrecoverable Error)
**Tool Docstring**: "Search the policy documents for exclusions related to the cause of loss."
**Tool Return on Error**: `"{'policy_form': 'FOOBAR', 'query_cause': 'water damage', 'is_excluded': False, 'error': \"Endorsement form 'FOOBAR' not found on file.\"}"`

**Transcript**:
**User**: What exclusions apply to water damage for policy FOOBAR?
**Agent**: *(Calls `search_policy_exclusions(policy_form="FOOBAR", cause_of_loss="water damage")`)*
**Server**: `"{'policy_form': 'FOOBAR', 'query_cause': 'water damage', 'is_excluded': False, 'error': \"Endorsement form 'FOOBAR' not found on file.\"}"`
**Agent**: I'm sorry, but I couldn't find the endorsement form 'FOOBAR' on file to check for water damage exclusions. 
*(Agent stops trying, doesn't know how to fix the issue or guide the user)*

---

### AFTER (Docstring as Prompt, Recoverable Error)
**Tool Docstring**: 
"You are an AI assistant that checks policy exclusions. Use this tool to determine if a loss cause is excluded under a specific policy form. If the policy_form is not found, DO NOT make up a result. Instead, ask the user to verify the form number. Policy forms always follow the pattern HO-XXXX (e.g. HO-0304)."
**Tool Return on Error**: `"Error: Policy form 'FOOBAR' not found. Valid form numbers look like HO-XXXX (e.g., HO-0304). Please ask the user to double-check the form number."`

**Transcript**:
**User**: What exclusions apply to water damage for policy FOOBAR?
**Agent**: *(Calls `search_policy_exclusions(policy_form="FOOBAR", cause_of_loss="water damage")`)*
**Server**: `"Error: Policy form 'FOOBAR' not found. Valid form numbers look like HO-XXXX (e.g., HO-0304). Please ask the user to double-check the form number."`
**Agent**: I couldn't find the policy form 'FOOBAR'. Policy forms always follow the pattern HO-XXXX (for example, HO-0304). Could you please double-check the form number and provide it again?
*(Agent successfully recovers by instructing the user on the correct format, directly following the tool's built-in prompt guidance)*
