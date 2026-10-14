"""Validate deployment configuration before creating storage or seeding records."""
import os
import re
from urllib.parse import urlsplit

TASK_DEADLINE_SECONDS = 1800
EVENT_LEASE_SECONDS = TASK_DEADLINE_SECONDS + 100


def hosted(environment=None):
    values = os.environ if environment is None else environment
    return bool(values.get("K_SERVICE"))


def _https_origin(value):
    try:
        address = urlsplit(value)
        return bool(address.scheme == "https" and address.hostname and "." in address.hostname
                    and address.hostname not in {"localhost", "127.0.0.1"}
                    and not address.username and not address.password and address.port in (None, 443)
                    and not address.path and not address.query and not address.fragment
                    and "*" not in value)
    except ValueError:
        return False


def validate_configuration(environment=None):
    """Only report setting names; configuration values may contain credentials."""
    values = os.environ if environment is None else environment
    errors = []
    role = values.get("SERVICE_ROLE", "api")
    if role not in {"api", "worker"}:
        errors.append("SERVICE_ROLE must be api or worker")
    if values.get("STORAGE_BACKEND", "sqlite") not in {"sqlite", "firestore"}:
        errors.append("STORAGE_BACKEND must be sqlite or firestore")
    if values.get("AUTH_PROVIDER", "firebase" if hosted(values) else "local") not in {"local", "firebase"}:
        errors.append("AUTH_PROVIDER must be local or firebase")
    if hosted(values):
        for name, expected in {"STORAGE_BACKEND": "firestore", "AUTH_PROVIDER": "firebase", "DEMO_MODE": "false"}.items():
            if values.get(name, "").strip().lower() != expected:
                errors.append(f"{name} must be {expected} on Cloud Run")
        required = ["GOOGLE_CLOUD_PROJECT", "CONNECTOR_ENCRYPTION_KEY", "GEMINI_API_KEY"]
        if role == "api":
            required += ["FIREBASE_WEB_API_KEY", "FIREBASE_AUTH_DOMAIN", "CORS_ORIGINS",
                         "CLOUD_TASKS_QUEUE", "CLOUD_TASKS_LOCATION", "WORKER_URL", "TASKS_SERVICE_ACCOUNT"]
        for name in required:
            if not values.get(name, "").strip():
                errors.append(f"{name} is required on Cloud Run")
        if values.get("CONNECTOR_ENCRYPTION_KEY"):
            from cryptography.fernet import Fernet
            try:
                Fernet(values["CONNECTOR_ENCRYPTION_KEY"].encode())
            except (ValueError, TypeError):
                errors.append("CONNECTOR_ENCRYPTION_KEY must be a valid Fernet key")
        if values.get("FIRESTORE_EMULATOR_HOST") or values.get("FIREBASE_AUTH_EMULATOR_HOST"):
            errors.append("Firebase emulators must not be configured on Cloud Run")
        if role == "api":
            if values.get("WORKER_URL") and not _https_origin(values["WORKER_URL"].rstrip("/")):
                errors.append("WORKER_URL must be an HTTPS service root")
            if values.get("CORS_ORIGINS") and not all(_https_origin(value.strip()) for value in values["CORS_ORIGINS"].split(",")):
                errors.append("CORS_ORIGINS must contain exact HTTPS origins")
            if values.get("FIREBASE_AUTH_DOMAIN") and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", values["FIREBASE_AUTH_DOMAIN"]):
                errors.append("FIREBASE_AUTH_DOMAIN must be a hostname")
            if values.get("TASKS_SERVICE_ACCOUNT") and not re.fullmatch(r"[^\s@]+@[^\s@]+\.iam\.gserviceaccount\.com", values["TASKS_SERVICE_ACCOUNT"]):
                errors.append("TASKS_SERVICE_ACCOUNT must be a service account email")
    if errors:
        raise RuntimeError("ReturnRadar configuration is incomplete: " + "; ".join(errors))
