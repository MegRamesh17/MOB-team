import os
from dotenv import load_dotenv

load_dotenv()

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

# --- Tool definitions: what the agent is ALLOWED to do ---
# The model decides WHICH of these to call, if any, based on the conversation.

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_reading_context",
            "description": "Get the text of the reading section the trainee is currently viewing",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_quiz_context",
            "description": "Get the current quiz question, the trainee's answer, the correct answer, and the related reading",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the internet for additional context not covered in the reading",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The search query"}},
                "required": ["query"]
            }
        }
    }
]


# --- Tool implementations: what actually happens when a tool gets called ---
# Stubbed for now, will be filled in once frontend/session-state is decided

def get_current_reading_context():
    """Placeholder — will eventually pull whatever reading the trainee is on."""
    pass

def get_quiz_context():
    """Placeholder — will eventually pull the current quiz question/answers."""
    pass

def search_web(query: str):
    """Placeholder — will eventually hit a real web search API."""
    pass


# Maps tool names (as GPT will refer to them) to actual Python functions
tool_map = {
    "get_current_reading_context": get_current_reading_context,
    "get_quiz_context": get_quiz_context,
    "search_web": search_web,
}


def chat(user_message: str, conversation_history: list = None):
    """
    Main agent loop. Sends the trainee's message + available tools to GPT.
    GPT decides whether to call a tool, and the loop keeps going until
    GPT produces a final answer instead of another tool call.
    """
    pass