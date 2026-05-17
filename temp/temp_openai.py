import os

from openai import OpenAI
from openai.types.responses import FunctionToolParam

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

tools: list[FunctionToolParam] = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Get current temperature for a given location.",
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string", "description": "City and country e.g. Bogota, Colombia"}},
            "required": ["location"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]

stream = client.responses.create(
    model="gpt-5.4-mini",
    reasoning={"effort": "medium", "summary": "auto"},
    input=[{"role": "user", "content": "Can you think about the meaning of life?"}],
    tools=tools,
    stream=True,
)
events = []
for event in stream:
    events.append(event)
    print(event)

print(events)
