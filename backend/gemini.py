"""Shared, validated Gemini requests and receipts for actual API calls."""
import json
import os
import uuid
from google import genai
from google.genai import errors
from .store import now

DEFAULT_MODEL = "gemini-3.1-flash-lite"


class GeminiUnavailable(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code=code


def configured_model():
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def client(purpose="analysis"):
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise GeminiUnavailable("Set GEMINI_API_KEY in .env and restart the server to enable Ask Radar.")
    # Firebase Hosting allows 60 seconds for proxied HTTP requests. Keep chat's
    # provider budget below that limit; analysis runs on the private task worker.
    chat = purpose == "chat"
    return genai.Client(api_key=key, http_options={"timeout": 15000 if chat else 90000, "retry_options": {"attempts": 2 if chat else 3,"initial_delay":1,"max_delay":2 if chat else 8,"http_status_codes":[408,429,500,502,503,504]}})


def request_json(api_client, model, contents, schema, purpose, max_tokens=4096):
    """Prompt for JSON and validate locally; unsupported provider schema options
    are avoided. Invalid model output fails rather than becoming a local answer.
    """
    parts = list(contents) if isinstance(contents, list) else [contents]
    parts.append("Return only one JSON object, without markdown fences or surrounding text, matching this response schema: " + json.dumps(schema.model_json_schema()))
    try:
        response = api_client.models.generate_content(model=model, contents=parts, config={"max_output_tokens": max_tokens})
    except errors.APIError as exc:
        hints = {400:"Analysis could not be prepared. Please contact your workspace administrator.",401:"The analysis connection needs attention. Please contact your workspace administrator.",403:"The analysis connection needs attention. Please contact your workspace administrator.",404:"Analysis is temporarily unavailable. Please try again later.",408:"Analysis is taking longer than expected. Please try again.",429:"The analysis allowance has been reached. Please try again later.",500:"Analysis is temporarily unavailable. Please try again later.",502:"Analysis is temporarily unavailable. Please try again later.",503:"Analysis is temporarily unavailable. Please try again later.",504:"Analysis is taking longer than expected. Please try again."}
        raise GeminiUnavailable(hints.get(exc.code,'Analysis could not be completed. Please try again.'),code=exc.code) from None
    except Exception:
        raise GeminiUnavailable("Gemini could not be reached. Check connectivity and try again.") from None
    text = (response.text or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = schema.model_validate_json(text)
    except ValueError:
        raise GeminiUnavailable("Gemini returned an incomplete or invalid answer. No result was saved. Try again.") from None
    usage = getattr(response, "usage_metadata", None)
    receipt = {"id": uuid.uuid4().hex, "provider": "Gemini", "model": model, "purpose": purpose, "at": now(), "response_id": getattr(response, "response_id", None), "input_tokens": getattr(usage, "prompt_token_count", None), "output_tokens": getattr(usage, "candidates_token_count", None), "total_tokens": getattr(usage, "total_token_count", None)}
    return parsed, receipt


def save_receipt(store, receipt):
    store.put("ai_calls", receipt["id"], receipt)
    store.put("meta", "gemini_last_call", {**receipt, "id": "gemini_last_call", "call_id": receipt["id"]})
