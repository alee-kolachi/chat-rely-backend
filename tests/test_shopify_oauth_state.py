from uuid import uuid4

import pytest

from app.domains.integrations.shopify.oauth_state import (
    sign_oauth_state,
    validate_return_to,
    verify_oauth_state,
)


def test_sign_verify_round_trip() -> None:
    secret = "test-secret-for-hmac-should-be-long"
    user_id = uuid4()
    agent_id = uuid4()
    state = sign_oauth_state(secret=secret, user_id=user_id, agent_id=agent_id, nonce="n1")
    payload = verify_oauth_state(secret=secret, state=state)
    assert payload["u"] == str(user_id)
    assert payload["a"] == str(agent_id)
    assert payload["n"] == "n1"


def test_verify_rejects_tamper() -> None:
    secret = "test-secret-for-hmac-should-be-long"
    state = sign_oauth_state(
        secret=secret, user_id=uuid4(), agent_id=uuid4(), nonce="n1"
    )
    with pytest.raises(ValueError):
        verify_oauth_state(secret="wrong", state=state)


def test_validate_return_to() -> None:
    assert validate_return_to("/onboarding/connection?agentId=abc") == "/onboarding/connection?agentId=abc"
    assert validate_return_to("//evil.com") is None
    assert validate_return_to("https://x") is None
    assert validate_return_to("") is None


def test_return_to_round_trip() -> None:
    secret = "test-secret-for-hmac-should-be-long"
    user_id = uuid4()
    agent_id = uuid4()
    state = sign_oauth_state(
        secret=secret,
        user_id=user_id,
        agent_id=agent_id,
        nonce="n1",
        return_to="/onboarding/connection?agentId=abc",
    )
    payload = verify_oauth_state(secret=secret, state=state)
    assert payload["r"] == "/onboarding/connection?agentId=abc"
