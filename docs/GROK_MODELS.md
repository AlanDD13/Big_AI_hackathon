# Grok models and native xAI integration

All active Grok requests go directly to `https://api.x.ai/v1` using
`Authorization: Bearer $XAI_API_KEY`. OpenRouter is no longer a supported
transport. Keys stay in the server environment and never reach the browser.

| Role | Default / override | Native endpoint | Owning code |
| --- | --- | --- | --- |
| Camera classification, grabbability estimate and identity confirmation | `grok-4.7` / `GROK_VISION_MODEL` | `POST /responses` | `vision/classification/` |
| Rover visual alignment and pickup tool loop | `grok-4.7` / `GROK_ROVER_MODEL`, CLI argument or recovery config `model` in `grok_tool_loop.py`; the separate `live_grok.py` diagnostic uses fixed `grok-4.7` | `POST /chat/completions` | `robot/grok_tool_loop.py`, `robot/live_grok.py` |
| Optional recovery policy proposals | `grok-4.7` / `GROK_RECOVERY_MODEL` | `POST /chat/completions` | `integration/recovery/policy.py` |
| Spoken operator messages | Grok Voice API, voice `eve` / `GROK_TTS_VOICE`, language `en` / `GROK_TTS_LANGUAGE` | `POST /tts` | `backend/speech.py` |

The TTS endpoint uses `text`, `voice_id` and `language`. It does **not** take an
invented model ID. Local YOLOE detection/tracking is separate from Grok.
`GROK_REASONING_EFFORT=low` configures the rover tool loop; vision and the
optional recovery policy use fixed low reasoning effort. Responses uses
`reasoning.effort`; Chat Completions uses `reasoning_effort`.

## What “used” means

Configured model names are not evidence that an API call ran. Authenticated
`GET /api/models` exposes configured roles, models actually reported by completed
vision classifications (`observedVisionModels`) and whether this backend has
successfully generated speech (`speech.used`). Each completed native vision
classification carries the response model in `classificationModel`. Vision
health also exposes the last classification model. Rover route reports record
`provider: "xai"` and the selected model, alongside the actual route outcome.
CLI/config overrides may differ from the backend's environment defaults.

Without `XAI_API_KEY`, classification and speech remain unavailable. They do
not fall back to another provider or manufacture completed results. Offline
tests mock the native API and therefore do not count as live model usage.
SDK command completion does not independently verify holding or placement.
Grabbability is a model estimate from 0 to 1, not a calibrated probability.

## Local setup

Copy the relevant entries from `.env.example` into private runtime configuration.
Load that environment separately for backend, vision and robot processes; the
backend does not automatically load a root `.env` file. Never use `VITE_` for
credentials. The frontend only requests authenticated backend notice audio.

The operator console setup is documented in
[`contracts/operator-console-v1.md`](../contracts/operator-console-v1.md).
Measured camera recovery still requires a locally confirmed physical setup in
`VISION_RECOVERY_CONFIG`; changing cameras does not recalibrate a physical route.

Official references: [models](https://docs.x.ai/developers/models),
[Responses](https://docs.x.ai/developers/rest-api-reference/inference/responses),
[Chat Completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions),
[reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning), and
[text to speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech).
