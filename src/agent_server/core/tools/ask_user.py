from pathlib import Path

from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel

from agent_server.core.tools._protocol import InputRequestTool
from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule
from agent_server.schemas.activity import InputRequestActivity, InputRequestItem, InputRequestResponseEvent

TOOL_NAME = "ask_user"

TOOL_DESCRIPTION = """Use this tool only when you are blocked on a decision that is genuinely the user's to make: one you cannot resolve from the request, the code, or sensible defaults.

Use this tool when you need to ask the user questions during execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- The user will always be presented with a "type your own answer" option so don't include "Other" or catch-all options
- Set `multiple: true` to allow selecting more than one
- If you recommend a specific option, make that the first option in the list and add "[Recommended] at the start of the choice."""

ASK_USER_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "The list of questions to present to the user.",
                "items": {
                    "type": "object",
                    "properties": {
                        "multiple": {
                            "type": "boolean",
                            "description": "When true, allow selecting more than one choice.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Very short label for the question (max 30 chars).",
                        },
                        "question": {
                            "type": "string",
                            "description": "The complete question to ask the user.",
                        },
                        "choices": {
                            "type": "array",
                            "description": "Available choices for the user to select from.",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["multiple", "title", "question", "choices"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
    "strict": True,
}


class AskUserConstraintRule(ConstraintRule):
    pass


class AskUserToolConfig(BaseModel):
    working_dir: Path
    rules: list[AskUserConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK


class AskUserTool(InputRequestTool):
    def __init__(self) -> None:
        pass

    def get_tool_defs(self) -> dict[str, FunctionToolParam]:
        return {TOOL_NAME: ASK_USER_TOOL_DEFINITION}

    def create_request(self, call_id: str, **arguments: object) -> InputRequestActivity:
        questions = arguments.get("questions")
        if not isinstance(questions, list) or not questions:
            raise ValueError("questions must be a non-empty list.")

        items: list[InputRequestItem] = []
        for index, question in enumerate(questions, start=1):
            if not isinstance(question, dict):
                raise TypeError("Each question must be an object.")

            title = question.get("title")
            content = question.get("question")
            allow_multiple = question.get("multiple")
            choices = question.get("choices")
            if not isinstance(title, str) or not isinstance(content, str):
                raise TypeError("Each question must include string title and question values.")
            if not isinstance(allow_multiple, bool):
                raise TypeError("Each question must include a boolean multiple value.")
            if not isinstance(choices, list):
                raise TypeError("Each question must include a list of string choices.")

            options: dict[str, str] = {}
            seen_choices: set[str] = set()
            for option_index, choice in enumerate(choices, start=1):
                if not isinstance(choice, str):
                    raise TypeError("Each question must include a list of string choices.")
                if choice in seen_choices:
                    raise ValueError("Question choices must be unique.")
                seen_choices.add(choice)
                options[f"option_{option_index}"] = choice

            items.append(
                InputRequestItem(
                    id=f"question_{index}",
                    header=title,
                    content=content,
                    allow_multiple=allow_multiple,
                    options=options,
                )
            )

        return InputRequestActivity(
            id=call_id,
            state="in_progress",
            title="Questions",
            items=items,
        )

    def resolve_request(self, request: InputRequestActivity, event: InputRequestResponseEvent) -> str:
        response_items = {item.id: item for item in event.items}
        lines = ["The user answered:"]

        for request_item in request.items:
            selections = response_items[request_item.id].selections
            answers = [request_item.options.get(selection, selection) for selection in selections]

            if len(lines) > 1:
                lines.append("")
            lines.append(f"Question: {request_item.content}")
            if len(answers) == 1:
                lines.append(f"Answer: {answers[0]}")
            else:
                lines.append("Answers:")
                lines.extend(f"- {answer}" for answer in answers)

        return "\n".join(lines)

    def check_constraint(self, **arguments: object) -> ConstraintPolicy:
        return ConstraintPolicy.ALLOW
