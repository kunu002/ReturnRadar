"""Check Gemini credentials; optionally validate ReturnRadar's response schema."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from google import genai
from google.genai import errors
from backend.models import AnalysisResponse
from backend.gemini import request_json, DEFAULT_MODEL, GeminiUnavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Test a model without changing .env.")
    parser.add_argument("--structured", action="store_true", help="Also test ReturnRadar's classification schema instead of a basic text response.")
    args = parser.parse_args()
    config = dotenv_values(ROOT / ".env")
    key = os.getenv("GEMINI_API_KEY") or config.get("GEMINI_API_KEY")
    model = args.model or os.getenv("GEMINI_MODEL") or config.get("GEMINI_MODEL") or DEFAULT_MODEL
    if not key or not key.strip():
        print("Missing GEMINI_API_KEY. Set it in the project's .env file.")
        return 1
    print(f"Testing model: {model}")
    try:
        with genai.Client(api_key=key, http_options={"timeout": 20000}) as client:
            if not args.structured:
                response = client.models.generate_content(model=model, contents="Reply with only the word OK.")
                print("SUCCESS: Your API key and model accepted a generation request.")
                print("Response:", (response.text or "(empty response)").replace(key, "[REDACTED]")[:100])
                return 0
            result,receipt = request_json(client,model,'Classify this single complaint: record_id="key-test", complaint="My laptop is too wide for the bag compartment." Use the Laptop fit issue. No listing is supplied, so return an empty listing_observations array.',AnalysisResponse,"key_test",max_tokens=2048)
            if len(result.classifications) != 1 or result.classifications[0].record_id != "key-test" or result.classifications[0].issue != "Laptop fit":
                print("Gemini responded, but the classification did not match the test.")
                return 1
            print("SUCCESS: Your API key and configured model work.")
            print("ReturnRadar's structured response validated: Laptop fit.")
            return 0
    except GeminiUnavailable as exc:
        print("FAILED:",str(exc))
        return 1
    except errors.APIError as exc:
        # Provider error messages can contain request details; always redact the key.
        message = str(exc.message or "Request rejected.").replace(key, "[REDACTED]")
        print(f"FAILED: Gemini returned HTTP {exc.code}.")
        print(message[:500])
        payload = exc.details if isinstance(exc.details, dict) else {}
        details = payload.get("error", payload).get("details", [])
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, dict):
                continue
            if detail.get("reason"):
                print("Reason:", str(detail["reason"]).replace(key, "[REDACTED]")[:200])
            for violation in detail.get("fieldViolations", []):
                print("Invalid field:", str(violation.get("field", "")).replace(key, "[REDACTED]")[:150])
                print(str(violation.get("description", "")).replace(key, "[REDACTED]")[:300])
        if exc.code in (400, 401, 403):
            print("Check key validity, API restrictions, project access, and any billing/region requirement stated above.")
        elif exc.code == 404:
            print("The configured model may be unavailable or not support this API. Check GEMINI_MODEL.")
        elif exc.code == 429:
            print("Quota/rate limit reached. This does not by itself mean the key is invalid.")
        return 1
    except Exception as exc:
        # Never print raw transport exceptions or request URLs containing secrets.
        print(f"FAILED: {type(exc).__name__}. Check connectivity and model response compatibility.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
