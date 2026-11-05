"""FastAPI surface for the dashboard stream and mapping observations.

Dashboard routes use the operator cookie. Mapping uses a separate bearer token.
Neither credential is accepted in place of the other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict
import json
import os
import secrets
import time
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse
from psycopg import Error as PostgresError

from backend.camera_proxy import CameraConfig, CameraProxy, CameraStreamingResponse, CameraUnavailable
from backend.collection import CollectionStore, MappingClient, admit_collection
from backend.evidence import EvidenceConflict, InvalidEvidence, parse_evidence
from backend.mission_http import (
    IDEMPOTENCY_KEY_RE, MISSION_ERRORS, FixtureMapping, UnavailableMapping, parse_mission_request,
)
from backend.manual_proxy import ManualFailure, ManualProxy
from backend.manual_requests import InvalidManualRequest, StaleManualIntent
from backend.evidence import ID_RE
from backend.postgres_collection import PostgresCollectionStore
from backend.postgres_evidence import PostgresEvidenceStore
from backend.postgres_projection import PostgresObservationLog, StorageUnavailable
from backend.state import BackendState, StreamItem
from backend.vision_ingest import (
    ACCEPTED, CLASSIFICATIONS, DETECTIONS, VISION_EVENTS, InvalidVision, VisionConflict, parse_vision_event,
)
from backend.vision_store import PostgresVisionLog


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
    data = item.data["body"] if item.event == "mission.updated" else item.data
    lines.append("data: " + json.dumps(data, separators=(",", ":"), allow_nan=False))
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
    vision_log: PostgresVisionLog | None = None,
    collection_store: CollectionStore | None = None,
    mapping_client: MappingClient | None = None,
    manual_proxy: ManualProxy | None = None,
    camera_proxy: CameraProxy | None = None,
) -> FastAPI:
    app = FastAPI(title="Rover backend", version="0.1.0")
    app.state.operator_token = operator_token
    app.state.peer_token = peer_token
    app.state.cookie_secure = cookie_secure
    app.state.idle_timeout = idle_timeout
    if state is None:
        state = BackendState(
            boot_id=boot_id, now=now, observation_log=observation_log, vision_log=vision_log,
        )
        if observation_log is not None:
            for evidence_body, source in observation_log.load():
                state.record_observation(evidence_body, persist=False, **source)
        if isinstance(observation_log, PostgresObservationLog):
            for object_id, classification_status, classification in observation_log.load_classifications():
                state.record_classification(object_id, classification_status, classification)
        if isinstance(collection_store, PostgresCollectionStore):
            for mission in collection_store.load_missions():
                state.record_mission(**mission)
        if vision_log is not None:
            for canonical in vision_log.load():
                state.ingest_vision(parse_vision_event(canonical, ACCEPTED), persist=False)
    app.state.backend = state
    collection_now = now if now is not None else time.time
    manual = manual_proxy if manual_proxy is not None else ManualProxy(
        url="", peer_token="", now=collection_now,
    )
    mapping = mapping_client if mapping_client is not None else UnavailableMapping()
    camera = camera_proxy if camera_proxy is not None else CameraProxy(CameraConfig())

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
    async def close_session(request: Request) -> Response:
        session_id = request.cookies.get("session")
        app.state.backend.close_session(session_id)
        await manual.end_session(session_id)
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

    @app.get("/api/manual/status")
    async def manual_status(request: Request) -> Response:
        session_id = cookie_session(request)
        if session_id is None:
            await manual.end_session(request.cookies.get("session"))
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        rover_id = request.query_params.get("roverId", "")
        if ID_RE.fullmatch(rover_id) is None:
            return _error(422, "VALIDATION_ERROR", "A valid roverId is required")
        if rover_id != manual.rover_id:
            return _error(404, "UNKNOWN_ROVER", "Rover is not configured")
        try:
            result = await manual.status()
        except ManualFailure as exc:
            return _error(exc.status, exc.code, "Manual control request was not accepted")
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post("/api/manual/{action}")
    async def manual_action(action: str, request: Request) -> Response:
        session_id = cookie_session(request)
        if session_id is None:
            await manual.end_session(request.cookies.get("session"))
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        if action not in {"lease", "drive", "release", "stop", "rearm"}:
            return _error(404, "NOT_FOUND", "Manual action was not found")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                return _error(422, "VALIDATION_ERROR", "Manual request exceeds 64 KiB")
        try:
            payload = json.loads(body)
            result = await manual.execute(
                action, session_id, payload, lambda: app.state.backend.session_open(session_id),
            )
        except StaleManualIntent:
            await manual.end_session(session_id)
            return _error(409, "STALE_INTENT", "Manual intent is stale")
        except (ValueError, InvalidManualRequest):
            await manual.end_session(session_id)
            return _error(422, "VALIDATION_ERROR", "Manual request does not match the contract")
        except ManualFailure as exc:
            if exc.code in {"STALE_INTENT", "LEASE_EXPIRED", "BRIDGE_BOOT_MISMATCH"}:
                await manual.end_session(session_id)
            return _error(exc.status, exc.code, "Manual control request was not accepted")
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/camera/status")
    def camera_status(request: Request) -> Response:
        if cookie_session(request) is None:
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        return JSONResponse(
            {"configured": camera.config.configured, "captureFreshness": "unverified"},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/camera/feed")
    async def camera_feed(request: Request) -> Response:
        session_id = cookie_session(request)
        if session_id is None:
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        try:
            feed = await camera.open()
        except CameraUnavailable:
            response = _error(503, "CAMERA_UNAVAILABLE", "Camera feed is unavailable")
            response.headers["Cache-Control"] = "no-store"
            return response
        if not app.state.backend.session_open(session_id):
            await feed.close()
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if feed.media_type == "image/jpeg":
            await feed.close()
            return Response(feed.prefix, media_type="image/jpeg", headers=headers)
        return CameraStreamingResponse(
            feed, lambda: app.state.backend.session_open(session_id), headers=headers,
        )

    @app.post("/api/missions", status_code=202)
    async def create_mission(request: Request) -> Response:
        session_id = cookie_session(request)
        if session_id is None:
            return _error(401, "UNAUTHORIZED", "Operator session is missing or expired")
        key = request.headers.get("idempotency-key", "")
        if IDEMPOTENCY_KEY_RE.fullmatch(key) is None:
            return _error(422, "VALIDATION_ERROR", "A valid Idempotency-Key is required")
        try:
            payload = await request.json()
        except ValueError:
            return _error(422, "VALIDATION_ERROR", "Mission body must be JSON")
        try:
            mission_request = parse_mission_request(payload)
        except ValueError as exc:
            return _error(422, "VALIDATION_ERROR", str(exc))
        if collection_store is None:
            return _error(503, "STORAGE_UNAVAILABLE", "Mission storage is unavailable")
        try:
            admission = await run_in_threadpool(
                admit_collection,
                mission_request,
                scope=session_id,
                key=key,
                store=collection_store,
                mapping=mapping,
                now=collection_now,
            )
        except (PostgresError, StorageUnavailable):
            return _error(503, "STORAGE_UNAVAILABLE", "Mission storage is unavailable")
        if admission.outcome not in {"created", "replayed"}:
            return _error(*MISSION_ERRORS[admission.outcome])
        if admission.mission_id is None or admission.mission_state is None:
            raise RuntimeError("Accepted mission has no stored identity or state")
        classification = admission.classification
        if classification is not None and classification.provider in ("fixture", "grok"):
            app.state.backend.record_classification(
                mission_request.object_id,
                classification.status,
                {
                    field: value
                    for field, value in asdict(classification).items()
                    if field != "status" and value is not None
                },
            )
        mission_state = admission.mission_state.upper()
        mission = app.state.backend.record_mission(
            admission.mission_id,
            mission_request.object_id,
            mission_request.destination_zone_id,
            mission_request.source,
            mission_state,
            status_revision=admission.status_revision,
        )
        return JSONResponse(mission, status_code=202 if admission.outcome == "created" else 200)

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
            result = await run_in_threadpool(
                app.state.backend.record_observation,
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

    @app.post("/api/detections")
    async def detections(request: Request) -> Response:
        return await _ingest_vision(request, DETECTIONS)

    @app.post("/api/classifications")
    async def classifications(request: Request) -> Response:
        return await _ingest_vision(request, CLASSIFICATIONS)

    @app.post("/api/vision-events")
    async def vision_events(request: Request) -> Response:
        return await _ingest_vision(request, VISION_EVENTS)

    async def _ingest_vision(request: Request, allowed: frozenset[str]) -> Response:
        if len(app.state.peer_token) < 16:
            return _error(503, "AUTH_UNCONFIGURED", "Peer authentication is not configured")
        if not peer_authorized(request):
            return _error(401, "UNAUTHORIZED", "Peer bearer token was rejected")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "INVALID_BODY", "Vision body must be JSON")
        try:
            event = parse_vision_event(payload, allowed)
            result = app.state.backend.ingest_vision(event)
        except InvalidVision as exc:
            return _error(422, "VALIDATION_ERROR", str(exc))
        except VisionConflict as exc:
            return _error(409, "CONFLICT", str(exc))
        except StorageUnavailable:
            return _error(503, "STORAGE_UNAVAILABLE", "Observation storage is unavailable")
        return JSONResponse(
            {
                "objectId": result.object_id,
                "backendRevision": result.backend_revision,
                "replayed": result.replayed,
                "discarded": result.discarded,
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
    vision_log = None
    collection_store = None
    manual_active = None
    mapping_client = (
        FixtureMapping(time.time) if os.environ.get("BACKEND_MISSION_FIXTURE_MAPPING") == "1" else None
    )
    if database_url:
        if "sslmode=" not in database_url:
            database_url += ("&" if "?" in database_url else "?") + "sslmode=require"

        def connect():
            import psycopg

            return psycopg.connect(database_url)

        def manual_active(rover_id):
            with connect() as connection:
                return connection.execute(
                    """SELECT EXISTS(SELECT 1 FROM rover_backend.missions
                       WHERE rover_id = %s AND state IN ('dispatching','running','holding','unknown'))""",
                    (rover_id,),
                ).fetchone()[0]

        observation_log = PostgresObservationLog(connect)
        vision_log = PostgresVisionLog(connect)
        evidence_store = PostgresEvidenceStore(connect)

        def load_mission_evidence(connection, object_id):
            return evidence_store.load(connection, object_id, require_classification_provider=True)

        collection_store = PostgresCollectionStore(connect, load_mission_evidence, time.time)
    return create_app(
        operator_token=operator_token,
        peer_token=peer_token,
        cookie_secure=cookie_secure,
        observation_log=observation_log,
        vision_log=vision_log,
        collection_store=collection_store,
        mapping_client=mapping_client,
        manual_proxy=ManualProxy(
            url=os.environ.get("ROBOT_BRIDGE_URL", "") or os.environ.get("ROVER_BRIDGE_URL", ""),
            peer_token=peer_token,
            rover_id=os.environ.get("BACKEND_MANUAL_ROVER_ID", "rover-1"),
            autonomous_active=manual_active,
            now=time.time,
        ),
        camera_proxy=CameraProxy(CameraConfig(
            url=os.environ.get("BACKEND_CAMERA_URL", ""),
            authorization=os.environ.get("BACKEND_CAMERA_AUTHORIZATION", ""),
            ca_cert=os.environ.get("BACKEND_CAMERA_CA_CERT", ""),
        )),
    )


app = app_from_env()
