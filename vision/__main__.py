"""Run from the repository root: python -m vision --help."""
import argparse
import os

from vision.app import camera_token, create_app
from vision.settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Vision source service (not the main dashboard)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--detector-profile", choices=["default", "paper"],
                        help="paper uses targeted text prompts and smaller floor-object proposals")
    parser.add_argument("--camera-token", metavar="CAMERA_ID", help="Print a scoped upload token for this configured camera")
    args = parser.parse_args()
    if args.detector_profile:
        os.environ["VISION_DETECTOR_PROFILE"] = args.detector_profile
    settings = Settings.from_env()
    if args.camera_token:
        if args.camera_token not in settings.cameras:
            parser.error("camera ID not configured")
        print(camera_token(settings.token, args.camera_token))
        return
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (args.cert and args.key):
        parser.error("LAN phone capture requires --cert and --key")
    import uvicorn
    uvicorn.run(create_app(settings), host=args.host, port=args.port,
                ssl_certfile=args.cert, ssl_keyfile=args.key, access_log=False)


if __name__ == "__main__":
    main()
