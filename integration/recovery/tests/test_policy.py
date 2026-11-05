import json

import httpx
import pytest

from integration.recovery.policy import GrokPolicy, MODEL, PolicyError
from integration.recovery.types import Mission, Observation, Target


def inputs():
    mission = Mission(mission_id="m", target=Target(object_id="o", revision=1,
        classification={"label": "box"}, dispatch_eligible=True), destination_id="d")
    observation = Observation(camera_id="c", session_id="s", frame_id=1, captured_at=1.0,
        telemetry_at=1.0, object_id="o", target_revision=1, identity_evidence_id="e",
        identity_confirmed=True, stationary=True, living_obstacle=False, safety="READY")
    return mission, "APPROACH", observation


def reply(arguments='{"action":"approach","object_id":"o","destination_id":"d"}'):
    return {"model": MODEL, "choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"type": "function", "function": {
            "name": "recovery_action", "arguments": arguments}}]}}]}


def policy(handler):
    return GrokPolicy("secret-key", httpx.Client(transport=httpx.MockTransport(handler)))


def test_request_and_valid_proposal():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.host == "openrouter.ai"
        assert body["model"] == MODEL and body["reasoning"] == {"effort": "low"}
        assert body["parallel_tool_calls"] is False
        assert body["tools"][0]["function"]["parameters"]["additionalProperties"] is False
        assert request.extensions["timeout"]["read"] <= 20
        return httpx.Response(200, json=reply())
    p = policy(handler)
    assert "secret-key" not in repr(p)
    assert p.choose(*inputs()).action == "approach"


@pytest.mark.parametrize("change", [
    lambda r: r.update(model="wrong"),
    lambda r: r["choices"][0].update(finish_reason="length"),
    lambda r: r["choices"][0]["message"].update(refusal="no"),
    lambda r: r["choices"][0]["message"]["tool_calls"].append(r["choices"][0]["message"]["tool_calls"][0]),
    lambda r: r["choices"][0]["message"]["tool_calls"][0]["function"].update(name="other"),
    lambda r: r["choices"][0]["message"]["tool_calls"][0]["function"].update(arguments='{"action":"stop","object_id":"o","destination_id":"d","speed":1}'),
    lambda r: r["choices"][0]["message"]["tool_calls"][0]["function"].update(arguments="not json"),
])
def test_rejects_invalid_proposals(change):
    data = reply()
    change(data)
    with pytest.raises(PolicyError):
        policy(lambda request: httpx.Response(200, json=data)).choose(*inputs())


def test_transport_failures_are_redacted():
    def fail(request):
        raise httpx.ReadTimeout("secret-key provider details", request=request)
    with pytest.raises(PolicyError) as exc:
        policy(fail).choose(*inputs())
    assert "secret" not in str(exc.value)
    assert exc.value.__cause__ is None


def test_oversized_response_and_http_error_are_rejected():
    for response in (httpx.Response(200, content=b"x" * (128 * 1024 + 1)),
                     httpx.Response(500, text="secret-key")):
        with pytest.raises(PolicyError) as exc:
            policy(lambda request: response).choose(*inputs())
        assert "secret" not in str(exc.value)
