"""Bounded merchant context remains useful and never offers invalid citations."""
import json
import pytest

from backend.analysis import dashboard
from backend.chat import CONTEXT_BYTES, build_context
from backend.models import ChatRequest
from tests.test_workflows import workspace, fake_gemini, chat_context


def test_large_saved_reports_keep_totals_and_only_selected_citations(workspace, monkeypatch):
    db, client = workspace
    selected = dashboard(db, 28, "synthetic")
    original_ids = [record["id"] for record in selected["returns"]]
    excluded_ids = ["historical-excluded-" + str(index) + "x" * 150 for index in range(200)]
    for product in selected["products"]:
        product_ids = [record["id"] for record in selected["returns"] if record["product_id"] == product["id"]]
        db.put("analysis_reports", product["id"], {
            "id": product["id"], "mode": "Gemini", "at": "2026-10-13T00:00:00+00:00",
            "observations": [{"issue": "Laptop fit", "basis": "listing_text", "listing_quote": "laptop",
                              "observation": "Check the claimed fit against verified dimensions.",
                              "evidence_ids": (product_ids + excluded_ids)[:200]}] * 5,
        })
    db.put("review_cycles", "review-large", {
        "id": "review-large", "product_id": selected["products"][0]["id"], "issue": "Laptop fit", "status": "planned",
        "baseline": {"feedback_count": 16, "issue_count": 8, "issue_share": 50,
                     "evidence_ids": excluded_ids}, "checkpoints": [{"note": "Private old note", "by": "private-owner"}] * 100,
    })
    assert len(json.dumps(db.all("analysis_reports"))) > 100_000

    def respond(call):
        context = chat_context(call)
        assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()) <= CONTEXT_BYTES
        assert context["counts"] == selected["counts"]
        assert context["issue_counts"] == selected["reasons"]
        sample_ids = {record["id"] for record in context["sample_evidence"]}
        assert sample_ids == set(context["allowed_evidence_ids"])
        assert sample_ids == {f"e{index}" for index in range(1, len(sample_ids) + 1)}
        schema = json.loads(call["contents"][-1].split("matching this response schema: ", 1)[1])
        assert set(schema["properties"]["evidence_ids"]["items"]["enum"]) == sample_ids
        assert len(sample_ids) <= 32
        for report in context["product_analysis"]:
            for observation in report["listing_observations"]:
                assert set(observation["evidence_ids"]) <= sample_ids
        assert "historical-excluded-" not in json.dumps(context)
        assert "private-owner" not in json.dumps(context)
        assert context["review_cycles"][0]["baseline"]["issue_share"] == 50
        assert context["coverage"]["sample_evidence"]["available"] == 160
        return {"points": ["The synthetic sample needs verified product dimensions."], "evidence_ids": [next(iter(sample_ids))]}

    fake_gemini(monkeypatch, respond)
    response = client.post("/api/chat", json={"question": "What should I improve?", "source": "synthetic"})
    assert response.status_code == 200, response.text
    assert set(response.json()["evidence_ids"]) <= set(original_ids)
    assert [record["id"] for record in response.json()["evidence"]] == response.json()["evidence_ids"]
    assert len(db.all("ai_calls")) == 1


def test_source_filter_excludes_other_product_review_cycles(workspace):
    db, _ = workspace
    db.put("products", "shop-product", {"id": "shop-product", "name": "Shopify shoes", "source": "shopify", "description": "A shoe."})
    cycles = [
        {"id": "sample-review", "product_id": "p1", "issue": "Laptop fit", "status": "active"},
        {"id": "shop-review", "product_id": "shop-product", "issue": "Size & fit", "status": "planned"},
    ]
    body = ChatRequest(question="What reviews should I plan?", source="shopify")
    context, evidence, _ = build_context(dashboard(db, body.days, body.source), cycles, body)
    assert [cycle["id"] for cycle in context["review_cycles"]] == ["shop-review"]
    assert context["counts"]["returns"] == 0
    assert evidence == {}
    assert context["allowed_evidence_ids"] == []
    assert context["coverage"]["review_cycles"] == {"available": 1, "included": 1}


def test_question_prioritises_product_in_large_catalog_and_budget_preserves_totals(workspace):
    db, _ = workspace
    data = dashboard(db)
    for index in range(100):
        data["products"].append({"id": f"new-product-{index}", "name": f"Ceramic kettle {index}",
                                 "source": "synthetic", "description": "💚" * 1200})
    data["products"].append({"id": "target-shoe", "name": "Marathon runner shoe", "source": "synthetic", "description": "Check the supplied size guide."})
    body = ChatRequest(question="What should I check about Marathon runner shoe?")
    first, _, _ = build_context(data, [], body)
    second, _, _ = build_context(data, [], body)
    assert first == second
    assert first["products"][0]["id"] == "target-shoe"
    assert first["counts"] == data["counts"]
    assert first["source_counts"] == data["source_counts"]
    assert first["coverage"]["products"]["available"] == len(data["products"])
    assert first["coverage"]["products"]["included"] < len(data["products"])
    assert len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode()) <= CONTEXT_BYTES


@pytest.mark.parametrize("identifier_kind", ["product", "pattern", "record", "past_alias"])
def test_non_selected_citation_aliases_fail_without_saved_receipts(workspace, monkeypatch, identifier_kind):
    db, client = workspace
    data = dashboard(db)
    invalid = {"product": data["products"][0]["id"], "pattern": data["patterns"][0]["id"],
               "record": data["returns"][0]["id"], "past_alias": "e999"}[identifier_kind]

    def respond(call):
        context = chat_context(call)
        assert invalid not in context["allowed_evidence_ids"]
        return {"points": ["Check the product details."], "evidence_ids": [invalid]}

    fake_gemini(monkeypatch, respond)
    response = client.post("/api/chat", json={"question": "What should I check?"})
    assert response.status_code == 503
    assert "invalid answer" in response.json()["detail"]
    assert db.all("ai_calls") == []
    assert db.get("meta", "gemini_last_call") is None


def test_empty_scope_schema_only_accepts_no_citations(workspace, monkeypatch):
    db, client = workspace

    def respond(call):
        context = chat_context(call)
        assert context["allowed_evidence_ids"] == []
        schema = json.loads(call["contents"][-1].split("matching this response schema: ", 1)[1])
        assert schema["properties"]["evidence_ids"]["maxItems"] == 0
        return {"points": ["No CSV feedback is available."], "evidence_ids": ["e1"]}

    fake_gemini(monkeypatch, respond)
    response = client.post("/api/chat", json={"question": "What should I check?", "source": "csv"})
    assert response.status_code == 503
    assert db.all("ai_calls") == []
