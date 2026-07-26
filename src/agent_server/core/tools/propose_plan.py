from pathlib import Path

from liquid import render
from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel

from agent_server.core.tools._protocol import InputRequestTool
from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule
from agent_server.schemas.activity import InputRequestActivity, InputRequestItem, InputRequestResponseEvent

TOOL_NAME = "propose_plan"

TOOL_DESCRIPTION = """Use this tool when you are in plan mode and have finished writing your plan to the plan file and are ready for user approval.
It will propose the current plan for user review. The user can approve, deny, or request changes to the plan. \
Before calling this tool, you must have generated a plan in a .md file in the {{PLAN_FILE_DIR}} and should be named appropriately based on what it is about. \
The first argument to this tool is the *relative path*, with respect to the working directory, to the plan file. \
You then must generate a brief summary of the full plan so the user can quickly understand the plan without reading the entire file. \
The summary should be no more than 1-3 sentences in plaintext (only inline code formatting with the `` syntax is allowed). \
The user will see the contents of your plan file when they review it.

Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, use {{ASK_USER_QUESTION_TOOL_NAME}} first
- Once your plan is finalized, use THIS tool to request approval

**Important:** Do NOT use {{ASK_USER_QUESTION_TOOL_NAME}} to ask "Is this plan okay?" or "Should I proceed?" - that's exactly what THIS tool, {{PROPOSE_PLAN_TOOL_NAME}}, does."""

PROPOSE_PLAN_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "plan_path": {
                "type": ["string"],
                "description": "Relative path to the plan file.",
            },
            "plan_summary": {
                "type": ["string"],
                "description": "Provide a brief summary of the plan in the .md file so the user can quickly understand the plan without reading the entire file. It should be no more than 1-3 sentences.",
            },
        },
        "required": ["plan_path", "plan_summary"],
        "additionalProperties": False,
    },
    "strict": True,
}

PLAN_MODE_ENABLED_MESSAGE = """<system-reminder>
Plan mode is now active. The user indicated that they do not want you to execute yet and instead come up with a plan that they can review and approve first. \
The plan should be written to an .md file in {{PLAN_FILE_DIR}}. \
Once the plan file is complete and you are confident that it is time for the user to review it, you must call the {{PROPOSE_PLAN_TOOL_NAME}} tool.
The {{PROPOSE_PLAN_TOOL_NAME}} will show the user path to the plan file and a brief summary of the plan. \
They will then review it and tell if you they approved it to be implemented, denied it (at which point they might want to completely change directions), or request changes to the plan. \
To create a high quality plan, you must follow the following workflow, calling to sub-agents where appropriate to delegate the work.

## Plan File Info:
You should create your plan in {{PLAN_FILE_DIR}} using the write tool if you need to.
You should build your plan incrementally by writing to or editing this file. \
NOTE that this is the only file you are allowed to edit - other than this you are only allowed to take READ-ONLY actions.
Answer the user's query comprehensively, using the {{ASK_USER_QUESTION_TOOL_NAME}} tool if you need to ask the user clarifying questions. \
If you do use the {{ASK_USER_QUESTION_TOOL_NAME}}, make sure to ask all clarifying questions you need to fully understand the user's intent before proceeding.

## Plan Mode Workflow
Create todos for each of the following phases of creating a plan and then go through each phase. \
If the task is simple, you can quickly go through the phases and skip delegating to sub-agents.

### Phase 1: Initial Understanding
Goal: Gain a comprehensive understanding of the user's request by reading through code and asking them questions. \
Focus on understanding the user's request and the code associated with their request. \
Actively search for existing functions, utilities, and patterns that can be reused. \
Avoid proposing new code when suitable implementations already exist.
Launch up to 3 agents IN PARALLEL (single message, multiple tool calls) to efficiently explore the codebase.
- Use 1 agent when the task is isolated to known files, the user provided specific file paths, or you're making a small targeted change.
- Use multiple agents when: the scope is uncertain, multiple areas of the codebase are involved, or you need to understand existing patterns before planning.
- Quality over quantity - 3 agents maximum, but you should try to use the minimum number of agents necessary (usually just 1)
- If using multiple agents: Provide each agent with a specific search focus or area to explore. \
Example: One agent searches for existing implementations, another explores related components, a third investigating testing patterns
- Do not consider old plans in the {{PLAN_FILE_DIR}} as part of your research. These may be outdated. Prioritize code and documents as the source of truth.

By the end of this phase, think about roughly what the implementation approach will be.

### Phase 2: Validate Approaches and Ask Questions
Goal: Design an implementation approach.

Based on the information gathered in Phase 1, it is now time to design the implementation approach. \
The two keys to success are testing hypotheses you are unsure about and asking the user clarifying questions. \
You MAY OR MAY NOT need to do either. If things are clear and you are confident, skip directly to phase 3.

#### Validate Approaches
Often there are different ways of doing things or sometimes its unclear if something will work. \
You should identify these uncertainties and validate them by actually trying them out. \
The approaches that you should validate are those are more self-contained - such as:
- Will fetching data in this way work?
- How does this API behave and what shape does it have?
- Assumptions about the system and environment

These are circumstances where it is not appropriate, you should move on to asking questions or Phase 3.
- End-to-end implementation of the full feature
- Refactors or migrations that only make sense after the plan is approved
- Trivial things like naming, file layout, or code-style preferences

NEVER validate the entire approach - only if there are modular pieces. \
Trying to validate the whole thing makes this planning process pointless. At that point you may have not bothered to plan in this first place.
This is only intended for smaller pieces of a bigger approach.

#### Ask Questions
- Ask the user questions when you hit decisions you can't make alone. Never ask what you could find out by reading the code.
- Focus on things only the user can answer: requirements, preferences, tradeoffs, edge case priorities
- Batch related questions together.

### Phase 3: Create the plan
Goal: Write your final plan to a .md plan file in the {{PLAN_FILE_DIR}}, named appropriately.
- Begin with a **Context** section: explain why this change is being made: the problem or need it addresses, what prompted it, and the intended outcome
- Include only your recommended approach, not all alternatives
- Ensure that the plan file is concise enough to scan quickly, but detailed enough to execute effectively
- Name the critical files to be modified. For changes that repeat a pattern across many files, \
describe the pattern once and list a few representative paths. Do not enumerate every file or line number
- Reference existing functions and utilities you found that should be reused, with their file paths
- Include a verification section describing how to test the changes end-to-end (run the code, use MCP tools, run tests)

### Phase 4: Finalize
At the very end of your turn, once you have asked the user questions and are happy with your final plan file \
you should always call {{PROPOSE_PLAN_TOOL_NAME}} to indicate to the user that you are done planning.
This is critical - your turn should only end with either using the {{ASK_USER_QUESTION_TOOL_NAME}} tool OR calling {{PROPOSE_PLAN_TOOL_NAME}}. \
Do not stop unless it's for these 2 reasons.
**Important:** Use {{ASK_USER_QUESTION_TOOL_NAME}} ONLY to clarify requirements or choose between approaches. \
Use {{PROPOSE_PLAN_TOOL_NAME}} to request plan approval. \
Do NOT ask about plan approval in any other way - no text questions, no AskUserQuestion. \
Phrases like "Is this plan okay?", "Should I proceed?", "How does this plan look?", "Any changes before we start?", or similar MUST use {{PROPOSE_PLAN_TOOL_NAME}}.
</system-reminder>"""


PLAN_ACCEPT_OUTPUT = """The user has approved the plan. Go ahead and implement it."""

PLAN_DENY_OUTPUT = """The user has denied the plan. Pay attention to their future messages for what to do next."""

PLAN_REQUEST_CHANGES_OUTPUT = """The user has requested the following changes to the plan: {{request_changes}}. 
Make the requested changes to the plan and then call {{PROPOSE_PLAN_TOOL_NAME}} again to request approval of the updated plan."""


class ProposePlanConstraintRule(ConstraintRule):
    pass


class ProposePlanToolConfig(BaseModel):
    working_dir: Path
    rules: list[ProposePlanConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK


class ProposePlanTool(InputRequestTool):
    def __init__(self) -> None:
        pass

    def get_tool_defs(self) -> dict[str, FunctionToolParam]:
        return {TOOL_NAME: PROPOSE_PLAN_TOOL_DEFINITION}

    def create_request(self, call_id: str, **arguments: object) -> InputRequestActivity:
        plan_path = str(arguments.get("plan_path", ""))
        plan_summary = str(arguments.get("plan_summary", ""))

        header = f"Plan file created at {plan_path}"

        return InputRequestActivity(
            id=call_id,
            state="in_progress",
            title="Plan",
            items=[
                InputRequestItem(
                    id="plan",
                    header=header,
                    content=plan_summary,
                    allow_multiple=False,
                    options={
                        "approve": "Approve",
                        "deny": "Deny",
                    },
                )
            ],
        )

    def resolve_request(self, request: InputRequestActivity, event: InputRequestResponseEvent) -> str:
        selection = event.items[0].selections[0]
        match selection:
            case "approve":
                return PLAN_ACCEPT_OUTPUT
            case "deny":
                return PLAN_DENY_OUTPUT
            case request_changes:
                return render(PLAN_REQUEST_CHANGES_OUTPUT, request_changes=request_changes)

    def check_constraint(self, **arguments: object) -> ConstraintPolicy:
        return ConstraintPolicy.ALLOW
