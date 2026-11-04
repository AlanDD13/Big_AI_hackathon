# Robot simulator contract

Owner: `platonyesin-dev` implements the simulator under `robot/**`.
Alan records the wire contract. `zekihxh` is the only caller for mission commands.

Status: draft on `task/AlanDD13/vision-pipeline`, not merged.
This document does not create a bridge, does not persist through a real rover
reboot, and does not validate DJI hardware, cameras, speeds, or firmware.
Simulator agreement is not physical acceptance.

Slice 1 is this file. Manual control is `manual-control-v1.md` and is not slice 1.

## Memory and identity

Slice 1 keeps command state in process memory only. `bridgeBootId` is 32
lowercase hex characters generated at process start. `statusRevision` starts at
1 for a new command and increases only when the stage or latch changes. An
identical retry returns the current body and the same `statusRevision`.

After a bridge restart, `GET /commands/{commandId}` for a previously acknowledged
command returns `404` with `{"error":{"code":"UNKNOWN_COMMAND","message":"..."}}`.
The backend keeps the rover slot and must not send that `commandId` again.
A new attempt is a new `commandId` after fresh validation. `404` is not success
and it is not permission to repeat a pickup.

## Authorization

`POST /commands/pickup`

```json
{
  "commandId": "cmd-1",
  "missionId": "mission-1",
  "objectId": "phone-1:session1:track1",
  "targetRevision": 3,
  "confirmationEvidenceId": "evidence-1",
  "expiresAt": 1793797203.0
}
```

IDs match `^[A-Za-z0-9_-]{1,128}$`, except `objectId`, which is
`cameraId:sessionId:trackId`. `expiresAt` is UTC UNIX seconds. The backend sets
it at most 3.0 seconds after issue. The bridge rejects the call when its own
clock is past `expiresAt`. Clock sync is not measured.

The bridge serializes pickup, hold, and stop per `commandId`. Pickup is legal
only from `NAVIGATING` or `ALIGNING`, while the command is not held and not
stopped. Those are the pre-pickup stages.

| Result | HTTP | code | Effect |
| --- | --- | --- | --- |
| Accepted | 200 |  | stage `PICKING` |
| Same binding repeated | 200 |  | current state, no second action |
| Different binding, same commandId | 409 | `CONFLICT` | no action |
| `expiresAt` passed | 409 | `EXPIRED` | no action |
| Hold already won | 409 | `HELD` | stage stays `HELD` |
| Already stopped | 409 | `STOPPED` | stage stays `STOPPED` |
| Still navigating is allowed | 200 |  | enters `PICKING` |
| Unknown command after restart | 404 | `UNKNOWN_COMMAND` | no new command is created |

Expected motion during `PICKING` is not a pre-pickup revocation. The backend
must not turn that motion into another pickup.

## Hold, stop, rearm, status

- `POST /commands/{commandId}/hold` with `commandId`, `missionId`, `reason`.
  Before pickup, stage becomes `HELD` and a later pickup returns `409 HELD`.
  During `PICKING`, hold returns `409 ALREADY_PICKING` and does not stop motion.
  Hold is idempotent while already `HELD`.
- `POST /commands/{commandId}/stop` has priority in every stage. Stage becomes
  `STOPPED`. Stop is idempotent. `POST /stop` remains the rover-wide emergency
  stop and also latches every active command `STOPPED`.
- `POST /commands/{commandId}/rearm` is legal only when that command is
  `STOPPED`. The response stays `stage: STOPPED` with `rearmed: true` and
  `motion: false`. Rearm does not resume movement and does not make the old
  command executable. `409 NOT_STOPPED` otherwise. A later attempt needs a new
  `commandId`.
- `GET /commands/{commandId}` returns `commandId`, `missionId`, `objectId`,
  `stage`, `statusRevision`, `held`, `stopped`, `rearmed`, `bridgeBootId`,
  and the accepted binding or null. Stages are `NAVIGATING`, `ALIGNING`,
  `HELD`, `PICKING`, `STOPPED`.

Lost acknowledgements are resolved by `GET`. Until that GET shows `STOPPED` or
`404 UNKNOWN_COMMAND`, the backend retains the slot and issues no new motion
command for that rover.

## Deadlines

These are simulator contract numbers, not measured rover limits.

- Pose used for a new authorization must have backend receipt age `<= 0.5 s`.
  Otherwise the backend does not send pickup.
- The backend reads `GET /commands/{commandId}` or receives a status report at
  least every `1.0 s`. Three missed intervals (`3.0 s`) mark the link unknown,
  retain the slot, and block a new authorization.
- A command deadline expiry without a terminal GET result is unknown. Do not
  repeat the side effect.

## Camera

`GET /camera/frame` is unchanged: JPEG plus `X-Camera-Id`, `X-Session-Id`,
`X-Geometry-Version`, `X-Frame-Id`, `X-Captured-At`, `X-Timestamp-Basis`.
`Authorization: Bearer PEER_TOKEN` is required. `PEER_TOKEN` is not `VISION_TOKEN`.

The same frame id always returns the same bytes while the bridge still has that
frame. Vision already ignores a repeated id and must not mint a new observation.
A lower id on the same session is an error. If the bridge knows the newest
frame is older than `1.0 s`, it returns `503` with `{"error":{"code":"STALE_FRAME","message":"..."}}`
and no image. No endpoint may answer `200` with a fabricated or stand-in image.
No camera or bridge is implemented by this contract.

Errors use `{"error":{"code":"<CODE>","message":"<text>"}}`.
Normative sequences: `contracts/fixtures/robot-simulator.json`.
