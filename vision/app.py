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

from vision.models import MappingEvidence, TargetRequest, CameraGrokControl
from vision.perception import _living
from vision.publisher import Publisher
from vision.service import VisionService
from vision.settings import Settings
from vision.cameras.adapters import LocalCapture, RoverHTTPAdapter


def camera_token(token, camera_id):
    return hmac.new(token.encode(), ("camera:" + camera_id).encode(), hashlib.sha256).hexdigest()


def _diagnostic_snapshot(service, objects):
    """Complete retained-object cut. sequence is the inclusive watermark after evaluation."""
    return {
        "schemaVersion": "vision.v1",
        "producerBootId": service.boot_id,
        "bootId": service.boot_id,
        "sequence": service.sequence,
        "scope": "ACTIVE_OBJECTS",
        "complete": True,
        "objects": objects,
    }


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
                "classificationTransport": getattr(service.classifier, "transport", "xai"),
                "detectorProfile": getattr(service.detector, "profile", "default"),
                "detectorModel": getattr(service.detector, "model_name", None),
                "liveClassificationConfigured": bool(getattr(service.classifier, "_key", None)),
                "automaticClassificationEnabled": any(service.grok_enabled.values()),
                "automaticClassificationByCamera": dict(service.grok_enabled),
                "pendingAutomaticClassifications": len(service.classification_tasks),
                "cameraRecovery": {"armed": bool(service.recovery.binding and service.recovery.binding.setup_confirmed),
                                   "activeObjectId": service.recovery.active_id,
                                   "threshold": service.recovery.binding.threshold if service.recovery.binding else .8},
                "delivery": {"delivered": publisher.delivered, "failed": publisher.failed,
                             "queued": publisher.queue.qsize(), "lastError": publisher.last_error}}

    web = Path(__file__).parent / "web"

    @app.get("/camera")
    async def phone_page():
        return FileResponse(web / "camera.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/camera.js")
    async def phone_script():
        return FileResponse(web / "camera.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})

    def grok_camera_state(camera_id, authorization):
        check_token(authorization, camera_id)
        if camera_id not in settings.cameras:
            raise HTTPException(404, "Camera is not configured")
        return {"cameraId": camera_id, "enabled": service.grok_enabled[camera_id],
                "includePickup": True, "pickupVerified": False}

    @app.get("/api/cameras/{camera_id}/grok")
    async def get_camera_grok(camera_id: str, authorization: str | None = Header(default=None)):
        return grok_camera_state(camera_id, authorization)

    @app.put("/api/cameras/{camera_id}/grok")
    async def set_camera_grok(camera_id: str, payload: CameraGrokControl,
                              authorization: str | None = Header(default=None)):
        grok_camera_state(camera_id, authorization)
        await service.set_grok_enabled(camera_id, payload.enabled)
        return grok_camera_state(camera_id, authorization)

    @app.get("/api/cameras", dependencies=[Depends(authorize)])
    async def cameras():
        actual = {item["camera_id"]: item for item in service.frames.list_cameras()}
        return {"cameras": [{"cameraId": key, "cameraKind": kind,
                             "grokEnabled": service.grok_enabled[key],
                             "active": key in actual, "stream": actual.get(key),
                             "processed": ({"sessionId": service._processed[key][0],
                                            "geometryVersion": service._processed[key][1],
                                            "frameId": service._processed[key][2]}
                                           if key in service._processed else None)}
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
        return _diagnostic_snapshot(service, service.snapshot())

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

    detect_lock = asyncio.Lock()
    active_assessments = 0

    @app.post("/api/classify-object")
    async def classify_object(request: Request, authorization: str | None = Header(default=None)):
        """Stateless peer assessment, never a track update or pickup authorization."""
        nonlocal active_assessments
        supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
        allowed = [t for t in (settings.peer_token, settings.token) if t]
        if not supplied or not any(secrets.compare_digest(supplied, token) for token in allowed):
            raise HTTPException(401, "Valid server-to-server bearer token required")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPException(415, "Use application/json")
        if active_assessments >= 2:
            raise HTTPException(503, "Classification capacity reached")
        active_assessments += 1
        try:
            raw = bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > 8 * 1024 * 1024:
                            raise HTTPException(413, "Assessment JSON exceeds 8 MiB")
                        raw.extend(chunk)
            except TimeoutError:
                raise HTTPException(408, "Request body timeout") from None
            import base64
            from vision.classification import _images
            try:
                body = json.loads(raw)
                if not isinstance(body, dict) or set(body) - {'cropImageBase64', 'contextImageBase64', 'requestId', 'includePickup'}:
                    raise ValueError('Invalid fields')
                crop_b64 = body['cropImageBase64']
                context_b64 = body.get('contextImageBase64') or crop_b64
                if not isinstance(crop_b64, str) or not isinstance(context_b64, str):
                    raise ValueError('Image strings required')
                crop = base64.b64decode(crop_b64, validate=True)
                context = base64.b64decode(context_b64, validate=True)
                request_id = body.get('requestId')
                include_pickup = body.get('includePickup', False)
                if type(include_pickup) is not bool:
                    raise ValueError('includePickup must be boolean')
                if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
                    raise ValueError('Invalid requestId')
                await asyncio.to_thread(_images, crop, context)
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise HTTPException(422, "Invalid JPEG/PNG images, fields or requestId") from None
            from vision.inspect_image import inspect_image
            if include_pickup:
                result = await inspect_image(crop, service.classifier, context)
            else:
                try:
                    classification = await service.classifier.classify(crop, context)
                    value = {'status': 'COMPLETED', 'result': classification.model_dump(mode='json')}
                except Exception:
                    value = {'status': 'UNAVAILABLE', 'result': None}
                result = {'provider': service.classifier.provider,
                          'transport': getattr(service.classifier, 'transport', 'xai'),
                          'model': getattr(service.classifier, '_model', None),
                          'classification': value,
                          'visualPickupAdvisory': {'status': 'NOT_REQUESTED', 'result': None},
                          'pickupVerified': False, 'metricSizeKnown': False,
                          'dispatchAuthorized': False}
            result.update(requestId=request_id, assessmentId=secrets.token_hex(16), advisory=True)
            success = (result['classification']['status'] == 'COMPLETED' and
                       (not include_pickup or result['visualPickupAdvisory']['status'] == 'COMPLETED'))
            return JSONResponse(result, status_code=200 if success else 503)
        finally:
            active_assessments -= 1

    @app.post("/api/detect-frame")
    async def detect_frame(request: Request, authorization: str | None = Header(default=None)):
        supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
        allowed = [t for t in (settings.token, settings.peer_token) if t]
        if not supplied or not any(secrets.compare_digest(supplied, token) for token in allowed):
            raise HTTPException(401, "Valid bearer token required")
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in {"image/jpeg", "application/json"}:
            raise HTTPException(415, "Use raw image/jpeg or application/json with imageBase64")
        if detect_lock.locked():
            raise HTTPException(503, "Detection capacity reached")
        async with detect_lock:
            raw = bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > 4 * 1024 * 1024:
                            raise HTTPException(413, "Request body exceeds 4 MiB")
                        raw.extend(chunk)
            except TimeoutError:
                raise HTTPException(408, "Request body timeout") from None
            if content_type == "application/json":
                import base64
                try:
                    payload = json.loads(bytes(raw))
                    b64 = payload.get("imageBase64") if isinstance(payload, dict) else None
                    if not isinstance(b64, str) or not b64.strip():
                        raise ValueError("Missing imageBase64")
                    jpeg_bytes = base64.b64decode(b64.encode("ascii"), validate=True)
                except Exception:
                    raise HTTPException(422, "Invalid imageBase64 JSON payload") from None
            else:
                jpeg_bytes = bytes(raw)
            if not jpeg_bytes:
                raise HTTPException(422, "Empty JPEG payload")
            try:
                detections = await asyncio.to_thread(service.detector.detect, jpeg_bytes)
            except Exception:
                raise HTTPException(422, "Unable to decode or run detection on image") from None
            filtered = [
                {
                    "detectorLabel": d.label,
                    "detectorScore": float(d.score),
                    "bboxPx": [float(v) for v in d.bbox],
                    "contactPointPx": [float(v) for v in d.contact_point] if d.contact_point is not None else None,
                }
                for d in detections
                if not _living(d.label) and (d.contact_point is None or tuple(d.contact_point) != (0.0, 0.0))
            ]
            return {"detections": filtered}

    @app.get("/api/events", dependencies=[Depends(authorize)])
    async def events(request: Request):
        if len(service.subscribers) >= 32:
            raise HTTPException(503, "Event subscriber capacity reached")
        # Snapshot and subscription occur without an await on the event loop.
        objects = service.snapshot()
        cut = service.sequence
        snapshot = _diagnostic_snapshot(service, objects)
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
