"""FastAPI surface for the dashboard stream and mapping observations.

Dashboard routes use the operator cookie. Mapping uses a separate bearer token.
Neither credential is accepted in place of the other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
import os
import secrets
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from backend.evidence import EvidenceConflict, InvalidEvidence, parse_evidence
from backend.postgres_projection import PostgresObservationLog, StorageUnavailable
from backend.state import BackendState, StreamItem


def _match_secret(presented: str, expected: str) -> bool:
    if len(expected) < 16 or not isinstance(presented, str):
        return False
    return secrets.compare_digest(presented.encode(), expected.encode())


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def _sse(item: StreamItem) -> str:
    lines = []
    if item.event_id is not None:
        lines.append(f"id: {item.event_id}")
    lines.append(f"event: {item.event}")
    lines.append("data: " + json.dumps(item.data, separators=(",", ":"), allow_nan=False))
    return "\n".join(lines) + "\n\n"


def create_app(
    *,
    operator_token: str,
    peer_token: str,
    cookie_secure: bool = True,
    now: Callable[[], float] | None = None,
    boot_id: str | None = None,
    idle_timeout: float | None = None,
    state: BackendState | None = None,
    observation_log: PostgresObservationLog | None = None,
) -> FastAPI:
    app = FastAPI(title="Rover backend", version="0.1.0")
    app.state.operator_token = operator_token
    app.state.peer_token = peer_token
    app.state.cookie_secure = cookie_secure
    app.state.idle_timeout = idle_timeout
    if state is None:
        state = BackendState(boot_id=boot_id, now=now, observation_log=observation_log)
        if observation_log is not None:
            for evidence_body, event_id in observation_log.load():
                state.record_observation(evidence_body, vision_event_id=event_id, persist=False)
    app.state.backend = state

    def cookie_session(request: Request) -> str | None:
        session_id = request.cookies.get("session")
        if not app.state.backend.session_open(session_id):
            return None
        return session_id

    def peer_authorized(request: Request) -> bool:
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            return False
        return _match_secret(presented, app.state.peer_token)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/session", status_code=204)
    async def open_session(request: Request) -> Response:
        if len(app.state.operator_token) < 16:
            return _error(503, "AUTH_UNCONFIGURED", "Operator sign-in is not configured")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "INVALID_BODY", "Session body must be JSON")
        if not isinstance(payload, dict) or set(payload) != {"token"} or not isinstance(payload["token"], str):
            return _error(422, "INVALID_BODY", "Session body must be {token}")
        if not _match_secret(payload["token"], app.state.operator_token):
            return _error(401, "UNAUTHORIZED", "Operator token was rejected")
        session_id = app.state.backend.open_session()
        response = Response(status_code=204)
        response.set_cookie(
            "session",
            session_id,
            httponly=True,
            secure=app.state.cookie_secure,
            samesite="lax",
            path="/",
            max_age=28800,
        )
        return response

    @app.delete("/api/session", status_code=204)
    def close_session(request: Request) -> Response:
        app.state.backend.close_session(request.cookies.get("session"))
        response = Response(status_code=204)
        response.delete_cookie(
            "session",
            path="/",
            httponly=True,
            secure=app.state.cookie_secure,
            samesite="lax",
        )
        return response

    @app.get("/api/snapshot")
    def snapshot(request: Request) -> Response:
        session_id = cookie_session(request)
        if session_id is None:
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        return JSONResponse(app.state.backend.snapshot(session_id))

    @app.get("/api/events")
    def events(request: Request, after: str | None = None) -> Response:
        session_id = cookie_session(request)
        if session_id is None:
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        cursor = request.headers.get("last-event-id") or after

        def generate() -> Iterator[str]:
            for item in app.state.backend.follow(
                session_id, cursor, idle_timeout=app.state.idle_timeout
            ):
                yield _sse(item)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/api/evidence/{evidence_id}")
    def evidence(evidence_id: str, request: Request) -> Response:
        session_id = cookie_session(request)
        if session_id is None:
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        return _error(404, "EVIDENCE_UNAVAILABLE", "The exact frame is gone")

    @app.post("/api/observations")
    async def observations(request: Request) -> Response:
        if len(app.state.peer_token) < 16:
            return _error(503, "AUTH_UNCONFIGURED", "Peer authentication is not configured")
        if not peer_authorized(request):
            return _error(401, "UNAUTHORIZED", "Peer bearer token was rejected")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "INVALID_BODY", "Observation body must be JSON")
        try:
            evidence_body = parse_evidence(payload)
            result = app.state.backend.record_observation(
                evidence_body,
                vision_event_id=_optional_header(request.headers, "x-vision-event-id"),
                producer_boot_id=_optional_header(request.headers, "x-producer-boot-id"),
                entity_version=_optional_int_header(request.headers, "x-entity-version"),
                vision_revision=_optional_int_header(request.headers, "x-vision-revision"),
            )
        except InvalidEvidence as exc:
            return _error(422, "INVALID_EVIDENCE", str(exc))
        except EvidenceConflict as exc:
            return _error(409, "CONFLICT", str(exc))
        except StorageUnavailable:
            return _error(503, "STORAGE_UNAVAILABLE", "Observation storage is unavailable")
        except ValueError as exc:
            return _error(422, "INVALID_HEADER", str(exc))
        return JSONResponse(
            {
                "objectId": result.object_id,
                "backendRevision": result.backend_revision,
                "replayed": result.replayed,
            }
        )

    return app


def _optional_header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None or value == "":
        return None
    return value


def _optional_int_header(headers: Mapping[str, str], name: str) -> int | None:
    value = _optional_header(headers, name)
    if value is None:
        return None
    if not value.isdigit():
        raise ValueError(f"{name} must be a nonnegative integer")
    return int(value)


def app_from_env() -> FastAPI:
    operator_token = os.environ.get("OPERATOR_TOKEN", "")
    peer_token = os.environ.get("PEER_TOKEN", "")
    cookie_secure = os.environ.get("BACKEND_COOKIE_SECURE", "1") != "0"
    database_url = os.environ.get("BACKEND_DATABASE_URL", "")
    observation_log = None
    if database_url:
        if "sslmode=" not in database_url:
            database_url += ("&" if "?" in database_url else "?") + "sslmode=require"

        def connect():
            import psycopg

            return psycopg.connect(database_url)

        observation_log = PostgresObservationLog(connect)
    return create_app(
        operator_token=operator_token,
        peer_token=peer_token,
        cookie_secure=cookie_secure,
        observation_log=observation_log,
    )


app = app_from_env()
