
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Get Gemini API key
API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError("GEMINI_API_KEY is missing from .env")

# Configure Gemini client
client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(
        timeout=30000,
        retry_options=types.HttpRetryOptions(attempts=1)
    )
)

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL_NAME = os.getenv(
    "GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite"
)

_temperature_value = os.getenv("GEMINI_TEMPERATURE")
_max_output_tokens_value = os.getenv("GEMINI_MAX_OUTPUT_TOKENS")
GENERATION_OPTIONS = {}
if _temperature_value is not None:
    GENERATION_OPTIONS["temperature"] = float(_temperature_value)
if _max_output_tokens_value is not None:
    GENERATION_OPTIONS["max_output_tokens"] = int(_max_output_tokens_value)

SYSTEM_PROMPT = """
You are Nova AI, an intelligent conversational assistant.

IDENTITY:
- Your name is Nova AI.
- You are powered by Google's Gemini model.
- Never introduce yourself as Gemini.
- Be friendly, helpful, accurate and conversational.

PURPOSE:
- Help users learn, create and solve problems.
- Explain complex concepts in simple language.
- Give clear and useful answers.
- Maintain a natural conversational tone.
"""


def _status_code(error: Exception):
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        status_code = getattr(error, "code", None)
    if status_code is None:
        status_code = getattr(
            getattr(error, "response", None), "status_code", None
        )
    return status_code


def _generate_content(contents, config=None):
    request = {
        "model": MODEL_NAME,
        "contents": contents
    }
    if config is not None:
        request["config"] = config

    try:
        return client.models.generate_content(**request)
    except Exception as error:
        if _status_code(error) not in (429, 503, "429", "503"):
            raise

        request["model"] = FALLBACK_MODEL_NAME
        return client.models.generate_content(**request)


def generate_summary(prompt: str) -> str:
    """Generate a conversation summary using the configured Gemini models."""
    try:
        config = (
            types.GenerateContentConfig(**GENERATION_OPTIONS)
            if GENERATION_OPTIONS else None
        )
        response = _generate_content(prompt, config)
        if not response.text:
            raise RuntimeError("Gemini returned an empty summary.")
        return response.text
    except Exception as error:
        error_message = str(error).replace(API_KEY, "[REDACTED]")
        print(f"Gemini API error: {type(error).__name__}: {error_message}")
        raise RuntimeError(
            "Nova AI could not update conversation memory."
        ) from None



def generate_response(
    prompt: str,
    history: list = None,
    summary: str = ""
) -> str:
    """Generate a response using previous conversation history."""

    try:
        conversation = []

        # Add previous messages to the conversation
        if history:
            for message in history:
                conversation.append({
                    "role": "user" if message["role"] == "user" else "model",
                    "parts": [{"text": message["content"]}]
                })

        # Add the latest user message
        conversation.append({
            "role": "user",
            "parts": [{"text": prompt}]
        })

        system_instruction = SYSTEM_PROMPT
        if summary:
            system_instruction = f"{SYSTEM_PROMPT}\n\nConversation summary:\n{summary}"

        request_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            **GENERATION_OPTIONS
        )
        response = _generate_content(conversation, request_config)

        if not response.text:
            raise RuntimeError("Gemini returned an empty response.")

        return response.text

    except Exception as error:
        error_message = str(error).replace(API_KEY, "[REDACTED]")
        print(f"Gemini API error: {type(error).__name__}: {error_message}")
        raise RuntimeError(
            "Nova AI could not generate a response. Please try again."
        ) from None


if __name__ == "__main__":
    print("\n================================")
    print("             NOVA AI")
    print("    Intelligence that remembers.")
    print("================================\n")

    print("Connecting to Gemini...")

    try:
        answer = generate_response(
            "Who are you? Introduce yourself in two sentences."
        )

        print("\nNOVA AI RESPONSE:")
        print(answer)

    except RuntimeError as error:
        print(f"\nERROR: {error}")
