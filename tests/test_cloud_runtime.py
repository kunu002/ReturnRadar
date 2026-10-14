"""Deployment guardrails without live Google credentials or cloud mutations."""
import json
import os
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi import BackgroundTasks, HTTPException

from backend import gemini, integrations, main
from backend.runtime import EVENT_LEASE_SECONDS, TASK_DEADLINE_SECONDS, validate_configuration
from tests.test_workflows import workspace


def cloud_config(role="api"):
    values = {"K_SERVICE":"returnradar-"+role, "SERVICE_ROLE":role,
              "DEMO_MODE":"false", "AUTH_PROVIDER":"firebase", "STORAGE_BACKEND":"firestore",
              "GOOGLE_CLOUD_PROJECT":"test-project", "GEMINI_API_KEY":"private-test-ai-key",
              "CONNECTOR_ENCRYPTION_KEY":Fernet.generate_key().decode()}
    if role == "api":
        values.update(FIREBASE_WEB_API_KEY="public-web-config", FIREBASE_AUTH_DOMAIN="test-project.firebaseapp.com",
                      CORS_ORIGINS="https://test-project.web.app,https://test-project.firebaseapp.com",
                      CLOUD_TASKS_QUEUE="returnradar", CLOUD_TASKS_LOCATION="asia-south1",
                      WORKER_URL="https://returnradar-worker.example.run.app",
                      TASKS_SERVICE_ACCOUNT="tasks@test-project.iam.gserviceaccount.com")
    return values


def test_local_defaults_and_each_cloud_role_are_valid():
    validate_configuration({})
    validate_configuration(cloud_config("api"))
    validate_configuration(cloud_config("worker"))


def test_cloud_import_rejects_sqlite_before_creating_database(tmp_path):
    values = {**os.environ, **cloud_config(), "STORAGE_BACKEND":"sqlite", "DATABASE_PATH":str(tmp_path / "ephemeral.db")}
    result = subprocess.run([sys.executable,"-c","import backend.store"],env=values,capture_output=True,text=True)
    assert result.returncode != 0
    assert "STORAGE_BACKEND must be firestore on Cloud Run" in result.stderr
    assert not (tmp_path / "ephemeral.db").exists()


@pytest.mark.parametrize("name,value", [
    ("STORAGE_BACKEND","sqlite"), ("AUTH_PROVIDER","local"), ("DEMO_MODE","true"),
    ("SERVICE_ROLE","unknown"), ("GEMINI_API_KEY",""), ("GOOGLE_CLOUD_PROJECT",""),
    ("WORKER_URL","http://localhost:8000"), ("WORKER_URL","https://worker.run.app/api"),
    ("CORS_ORIGINS","*"), ("CORS_ORIGINS","https://good.web.app,http://localhost:5173"),
    ("CONNECTOR_ENCRYPTION_KEY","private-invalid-key"), ("FIREBASE_WEB_API_KEY",""),
    ("FIREBASE_AUTH_DOMAIN","https://test-project.firebaseapp.com"),
    ("TASKS_SERVICE_ACCOUNT","owner@example.com"), ("CLOUD_TASKS_QUEUE",""),
    ("FIRESTORE_EMULATOR_HOST","localhost:8085"), ("FIREBASE_AUTH_EMULATOR_HOST","localhost:9099"),
])
def test_incomplete_cloud_config_fails_without_exposing_secrets(name,value):
    values = cloud_config()
    values[name] = value
    with pytest.raises(RuntimeError) as result:
        validate_configuration(values)
    assert "private-test-ai-key" not in str(result.value)
    assert "private-invalid-key" not in str(result.value)
    assert values["CONNECTOR_ENCRYPTION_KEY"] not in str(result.value)


def test_tasks_use_workspace_oidc_and_long_running_deadline(monkeypatch):
    from google.cloud import tasks_v2
    fake = Mock()
    fake.queue_path.return_value = "projects/test-project/locations/asia-south1/queues/returnradar"
    monkeypatch.setattr(tasks_v2,"CloudTasksClient",lambda:fake)
    for name,value in cloud_config().items():
        monkeypatch.setenv(name,value)
    integrations.enqueue("event-one","merchant-one")
    task = fake.create_task.call_args.kwargs["task"]
    assert fake.create_task.call_args.kwargs["timeout"] == 15
    assert fake.create_task.call_args.kwargs["retry"] is None
    assert task["dispatch_deadline"]["seconds"] == TASK_DEADLINE_SECONDS == 1800
    request = task["http_request"]
    assert json.loads(request["body"]) == {"event_id":"event-one","workspace_id":"merchant-one"}
    assert request["url"] == "https://returnradar-worker.example.run.app/api/internal/process"
    assert request["oidc_token"] == {"service_account_email":"tasks@test-project.iam.gserviceaccount.com", "audience":"https://returnradar-worker.example.run.app"}


def test_hosted_dispatch_never_falls_back_to_local_background(monkeypatch):
    monkeypatch.setenv("K_SERVICE","returnradar-api")
    monkeypatch.delenv("CLOUD_TASKS_QUEUE",raising=False)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as result:
        main.dispatch("event",background,"merchant")
    assert result.value.status_code == 503
    assert background.tasks == []


def test_chat_budget_fits_hosting_and_analysis_keeps_worker_budget(monkeypatch):
    calls = []
    monkeypatch.setenv("GEMINI_API_KEY","private-test-key")
    monkeypatch.setattr(gemini.genai,"Client",lambda **kwargs:calls.append(kwargs))
    gemini.client(purpose="chat")
    gemini.client()
    chat,analysis = [call["http_options"] for call in calls]
    assert chat["timeout"] == 15000 and chat["retry_options"]["attempts"] == 2
    assert chat["timeout"]/1000 * chat["retry_options"]["attempts"] + chat["retry_options"]["max_delay"] < 50
    assert analysis["timeout"] == 90000 and analysis["retry_options"]["attempts"] == 3


def test_worker_exposes_only_probe_and_task_routes(workspace,monkeypatch):
    _,client = workspace
    monkeypatch.setattr(main,"SERVICE_ROLE","worker")
    for path in ["/", "/docs", "/openapi.json", "/api/config", "/api/auth/session", "/api/dashboard", "/api/public/products/p1"]:
        assert client.get(path).status_code == 404
    assert client.post("/api/auth/logout").status_code == 404
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ready").json() == {"status":"ready"}


def test_private_responses_are_not_cached_and_readiness_is_safe(workspace,monkeypatch):
    db,client = workspace
    response = client.get("/api/dashboard")
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get("/api/config").headers["cache-control"] == "private, no-store"
    monkeypatch.setattr(db,"get",lambda *args:(_ for _ in ()).throw(RuntimeError("private-storage-failure")))
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json() == {"detail":"Service is temporarily unavailable."}


def test_processing_lease_lasts_beyond_tasks_deadline(workspace,monkeypatch):
    db,_ = workspace
    db.create("events","still-running",{"id":"still-running","kind":"analysis","status":"processing", "lease_at":time.time()-TASK_DEADLINE_SECONDS-1})
    with pytest.raises(RuntimeError,match="already processing"):
        main.process_event("still-running")
    assert db.get("events","still-running")["status"] == "processing"
    db.put("events","expired",{"id":"expired","kind":"analysis","status":"processing", "lease_at":time.time()-EVENT_LEASE_SECONDS-1})
    monkeypatch.setattr(main,"analyze",lambda *args:{"count":1})
    main.process_event("expired")
    assert db.get("events","expired")["status"] == "complete"
