"""One-shot, explicitly requested Grok image check. Never authorizes pickup.

python -m vision.inspect_image image.png [--env-file vision/.env]
Makes bounded classification and crop-bound robot-estimate calls.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shlex
import time

from vision.classification import build_classifier_from_env


def load_env_file(path):
    """Read selected runtime values as data, without evaluating a shell script."""
    allowed = {'XAI_API_KEY', 'GROK_VISION_PROVIDER', 'GROK_VISION_MODEL'}
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, separator, raw = line.strip().removeprefix('export ').partition('=')
        if separator and key.strip() in allowed:
            parts = shlex.split(raw, comments=True)
            if len(parts) <= 1:
                os.environ.setdefault(key.strip(), parts[0] if parts else '')


async def inspect_image(data, classifier, context=None, prefer_joint=False):
    start = time.monotonic()
    async def call(method, scene):
        try:
            return {'status': 'COMPLETED', 'result': (await method(data, scene)).model_dump(mode='json')}
        except Exception:
            return {'status': 'UNAVAILABLE', 'result': None}
    if prefer_joint and hasattr(classifier, 'assess_object'):
        try:
            joint = await classifier.assess_object(data, context if context is not None else data)
            classification = {'status': 'COMPLETED', 'result': joint.classification.model_dump(mode='json')}
            advisory = {'status': 'COMPLETED', 'result': joint.pickup.model_dump(mode='json')}
        except Exception:
            classification = advisory = {'status': 'UNAVAILABLE', 'result': None}
    else:
        classification, advisory = await asyncio.gather(
            call(classifier.classify, context if context is not None else data),
            call(classifier.assess_pickup, data))
    return {'provider': classifier.provider, 'transport': getattr(classifier, 'transport', 'xai'),
            'model': getattr(classifier, '_model', None), 'classification': classification,
            'visualPickupAdvisory': advisory, 'pickupVerified': False,
            'metricSizeKnown': False, 'dispatchAuthorized': False,
            'elapsedSeconds': round(time.monotonic() - start, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--env-file', type=Path, default=Path('vision/.env'))
    args = parser.parse_args()
    if not 0 < args.image.stat().st_size <= 4 * 1024 * 1024:
        parser.error('Image must be between 1 byte and 4 MiB')
    load_env_file(args.env_file)
    classifier = build_classifier_from_env()
    if not getattr(classifier, '_key', None):
        parser.error('Configure XAI_API_KEY in the runtime environment')
    result = asyncio.run(inspect_image(args.image.read_bytes(), classifier))
    print(json.dumps(result, indent=2))
    if any(result[key]['status'] != 'COMPLETED' for key in ('classification', 'visualPickupAdvisory')):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
