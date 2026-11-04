"""Authenticated HTTP boundary; the operator dashboard remains frontend's work."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from vision.models import MappingEvidence, TargetRequest
from vision.publisher import Publisher
from vision.service import VisionService
from vision.settings import Settings
from vision.cameras.adapters import LocalCapture, RoverHTTPAdapter


def camera_token(token, camera_id):
    return hmac.new(token.encode(), ("camera:" + camera_id).encode(), hashlib.sha256).hexdigest()


def create_app(settings=None, detector=None, classifier=None, run_workers=True):
    settings = settings or Settings.from_env()
    service = VisionService(settings, detector=detector, classifier=classifier)
    publisher = Publisher(settings)
    service.publisher = publisher

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if run_workers:
            tasks = [asyncio.create_task(service.worker()), asyncio.create_task(service.sweeper()),
                     asyncio.create_task(publisher.run())]
            for camera_id, source in settings.local_sources.items():
                tasks.append(asyncio.create_task(source_loop(service, lambda s=source, c=camera_id:
                    LocalCapture(s, service.frames, c, secrets.token_hex(16)))))
            if settings.rover_bridge_url:
                tasks.append(asyncio.create_task(source_loop(service, lambda:
                    RoverHTTPAdapter(settings.rover_bridge_url, service.frames, settings.rover_camera_id,
                                     None, token=settings.peer_token))))
        yield
        await service.close()
        publisher.closed = True
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Rover vision source service", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    # No queued waiters: admission counts include body reads and decoding.
    active_uploads = 0
    active_json = 0
    camera_locks = {camera_id: asyncio.Lock() for camera_id in settings.cameras}

    @app.middleware("http")
    async def response_headers(request, call_next):
        nonlocal active_json
        if request.method == "POST" and request.url.path in {"/api/mapping-evidence", "/api/targets/confirm"}:
            if active_json >= 16:
                return JSONResponse({"detail": "Request capacity reached"}, status_code=503)
            active_json += 1
            try:
                body = bytearray()
                try:
                    async with asyncio.timeout(5):
                        async for chunk in request.stream():
                            if len(body) + len(chunk) > 65536:
                                return JSONResponse({"detail": "JSON body exceeds 64 KiB"}, status_code=413)
                            body.extend(chunk)
                except TimeoutError:
                    return JSONResponse({"detail": "Request body timeout"}, status_code=408)
                delivered = False
                original_receive = request._receive
                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await original_receive()
                request._receive = replay
                response = await call_next(request)
            finally:
                active_json -= 1
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def check_token(value, camera_id=None):
        supplied = value.removeprefix("Bearer ") if value and value.startswith("Bearer ") else ""
        allowed = [settings.token]
        if camera_id is not None:
            allowed.append(camera_token(settings.token, camera_id))
        if not supplied or not any(secrets.compare_digest(supplied, token) for token in allowed):
            raise HTTPException(401, "Valid bearer token required")

    def authorize(authorization: str | None = Header(default=None)):
        check_token(authorization)

    @app.get("/health")
    async def health():
        return {"status": "degraded" if service.detector_error or publisher.last_error else "running",
                "detectorError": service.detector_error,
                "classificationProvider": service.classifier.provider,
                "liveClassificationConfigured": bool(getattr(service.classifier, "_key", None)),
                "delivery": {"delivered": publisher.delivered, "failed": publisher.failed,
                             "queued": publisher.queue.qsize(), "lastError": publisher.last_error}}

    web = Path(__file__).parent / "web"

    @app.get("/camera")
    async def phone_page():
        return FileResponse(web / "camera.html", media_type="text/html")

    @app.get("/camera.js")
    async def phone_script():
        return FileResponse(web / "camera.js", media_type="application/javascript")

    @app.get("/api/cameras", dependencies=[Depends(authorize)])
    async def cameras():
        actual = {item["camera_id"]: item for item in service.frames.list_cameras()}
        return {"cameras": [{"cameraId": key, "cameraKind": kind,
                             "active": key in actual, "stream": actual.get(key)}
                            for key, kind in settings.cameras.items()]}

    @app.post("/api/cameras/{camera_id}/frames", status_code=202)
    async def upload(camera_id: str, request: Request,
                     authorization: str | None = Header(default=None)):
        check_token(authorization, camera_id)
        if camera_id not in settings.cameras:
            raise HTTPException(404, "Camera is not configured")
        nonlocal active_uploads
        if request.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise HTTPException(415, "Use raw image/jpeg")
        if active_uploads >= 4 or camera_locks[camera_id].locked():
            raise HTTPException(503, "Upload capacity reached")
        active_uploads += 1
        try:
            async with camera_locks[camera_id]:
                data = bytearray()
                try:
                    async with asyncio.timeout(5):
                        async for chunk in request.stream():
                            if len(data) + len(chunk) > 4 * 1024 * 1024:
                                raise HTTPException(413, "JPEG exceeds 4 MiB")
                            data.extend(chunk)
                except TimeoutError:
                    raise HTTPException(408, "Upload body timeout") from None
                try:
                    kind = settings.cameras[camera_id]
                    if request.headers.get("x-camera-kind", kind) != kind:
                        raise ValueError("Camera kind differs from configuration")
                    frame_id = int(request.headers["x-frame-id"])
                    # Cancellation cannot release the per-camera lock while a decode
                    # thread is still capable of committing to the frame store.
                    task = asyncio.create_task(asyncio.to_thread(service.frames.put,
                        camera_id, request.headers["x-session-id"],
                        request.headers["x-geometry-version"], frame_id,
                        float(request.headers["x-captured-at"]), bytes(data), kind=kind,
                        timestamp_basis=request.headers.get("x-timestamp-basis", "SOURCE")))
                    try:
                        frame = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        try:
                            await task
                        except Exception:
                            pass
                        raise
                except (KeyError, ValueError) as exc:
                    reason = str(exc)
                    code = 409 if "sequence" in reason.lower() or "session" in reason.lower() or "geometry" in reason.lower() else 422
                    raise HTTPException(code, "Invalid frame, metadata or sequence") from None
                service.enqueue_frame(frame)
        finally:
            active_uploads -= 1
        return {"cameraId": camera_id, "sessionId": frame.session_id,
                "frameId": frame.frame_id, "receivedAt": frame.received_at,
                "width": frame.width, "height": frame.height}

    @app.get("/api/frames/{camera_id}/{session_id}/{frame_id}", dependencies=[Depends(authorize)])
    async def frame_image(camera_id: str, session_id: str, frame_id: int):
        frame = service.frames.get(camera_id, session_id, frame_id)
        if not frame:
            raise HTTPException(404, "Frame unknown or evicted")
        return Response(frame.jpeg, media_type="image/jpeg", headers={
            "X-Camera-Id": frame.camera_id, "X-Session-Id": frame.session_id,
            "X-Geometry-Version": frame.geometry_version, "X-Frame-Id": str(frame.frame_id),
            "X-Captured-At": str(frame.captured_at), "X-Received-At": str(frame.received_at),
            "X-Timestamp-Basis": frame.timestamp_basis})

    @app.get("/api/objects", dependencies=[Depends(authorize)])
    async def objects():
        snapshot = service.snapshot()
        return {"schemaVersion": "vision.v1", "sequence": service.sequence,
                "bootId": service.boot_id, "objects": snapshot}

    @app.post("/api/mapping-evidence", dependencies=[Depends(authorize)])
    async def mapping(evidence: MappingEvidence):
        try:
            return await service.apply_mapping(evidence)
        except ValueError:
            raise HTTPException(409, "Observation is stale, unknown or incompatible") from None

    @app.post("/api/objects/{object_id}/reclassify", dependencies=[Depends(authorize)])
    async def reclassify(object_id: str):
        state = service.objects.get(object_id)
        if not state or state.status == "LOST":
            raise HTTPException(404, "Current object not found")
        service.invalidate(state, "OPERATOR_RECLASSIFICATION")
        state.classification_attempt = -1
        service._evaluate(state)
        service.emit("object.updated", service.object_payload(state))
        return {"status": "PENDING", "objectId": object_id}

    @app.post("/api/targets/confirm", dependencies=[Depends(authorize)])
    async def confirm(payload: TargetRequest):
        try:
            return await service.confirm_target(payload)
        except ValueError:
            raise HTTPException(409, "Current target or rover evidence unavailable") from None
        except RuntimeError:
            raise HTTPException(429, "Confirmation capacity reached") from None

    @app.get("/api/events", dependencies=[Depends(authorize)])
    async def events(request: Request):
        if len(service.subscribers) >= 32:
            raise HTTPException(503, "Event subscriber capacity reached")
        # Snapshot and subscription occur without an await on the event loop.
        objects = service.snapshot()
        cut = service.sequence
        snapshot = {"schemaVersion": "vision.v1", "bootId": service.boot_id,
                    "sequence": cut, "objects": objects}
        queue = asyncio.Queue(maxsize=128)
        service.subscribers.add(queue)
        async def stream():
            try:
                # A snapshot is mandatory on every reconnect; missed transitions are not silently skipped.
                yield "event: snapshot\ndata: " + json.dumps(snapshot) + "\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=10)
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if event is None:
                        yield "event: resync_required\ndata: {}\n\n"
                        break
                    if event.sequence <= cut:
                        continue
                    yield f"id: {event.event_id}\nevent: {event.type}\ndata: {event.model_dump_json(by_alias=True)}\n\n"
            finally:
                service.subscribers.discard(queue)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    return app


async def source_loop(service, factory):
    source = None
    try:
        source = await asyncio.to_thread(factory)
        while not service.closed:
            try:
                frame = await asyncio.to_thread(source.read)
                if frame is not None:
                    service.enqueue_frame(frame)
            except Exception:
                # No synthetic image or timestamp: liveness expiry revokes existing observations.
                await asyncio.sleep(.5)
            await asyncio.sleep(1 / 3)
    except Exception:
        service.emit("alert.camera_unavailable", {"reason": "SOURCE_UNAVAILABLE"})
    finally:
        if source and hasattr(source, "close"):
            source.close()
