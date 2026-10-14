"""Helpful connection setup feedback never echoes submitted credentials."""
import pytest
from test_workflows import workspace
from test_connections import register


@pytest.mark.parametrize("endpoint",["/api/connections","/api/connections/test"])
def test_app_settings_url_gets_helpful_safe_store_address_feedback(workspace,endpoint):
    owner,_=register("connection-help@example.com")
    secret="private-sentinel-not-for-response"
    response=owner.post(endpoint,json={"platform":"shopify","url":"https://dev.shopify.com/dashboard/239070117/apps/433642668033/settings","access_token":secret})
    assert response.status_code==422
    assert "myshopify.com" in response.json()["detail"]
    assert secret not in response.text and "input" not in response.json()


@pytest.mark.parametrize("endpoint",["/api/connections","/api/connections/test"])
def test_missing_access_details_returns_actionable_message(workspace,endpoint):
    owner,_=register("connection-help@example.com")
    response=owner.post(endpoint,json={"platform":"shopify","url":"merchant.myshopify.com","access_token":""})
    assert response.status_code==422
    assert response.json()["detail"]=="Enter the store access token."
