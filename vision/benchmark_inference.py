"""Benchmark local detection only: no camera, Grok, backend or robot calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from vision.perception import YoloDetector, resolve_device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--image-size', type=int, default=960)
    parser.add_argument('--runs', type=int, default=12)
    parser.add_argument('--warmup', type=int, default=3)
    args = parser.parse_args()
    if args.runs < 2 or args.warmup < 1:
        parser.error('Use at least two measured runs and one warmup')
    if not args.model.is_file():
        parser.error('--model must be a local weights file (no downloads)')
    jpeg = args.image.read_bytes()
    device = resolve_device(args.device)
    detector = YoloDetector(str(args.model), device=device, image_size=args.image_size)
    samples, outputs = [], []
    for index in range(args.warmup + args.runs):
        started = time.perf_counter()
        detections = detector.detect(jpeg)
        if device == 'mps':
            import torch
            torch.mps.synchronize()
        elif device.startswith('cuda') or device.isdigit():
            import torch
            torch.cuda.synchronize(int(device) if device.isdigit() else device)
        elapsed = (time.perf_counter() - started) * 1000
        if index >= args.warmup:
            samples.append(elapsed)
            outputs.append([(d.label, d.score, d.bbox) for d in detections])
    print(json.dumps(dict(
        image_sha256=hashlib.sha256(jpeg).hexdigest(), device=device,
        model=args.model.name, image_size=detector.image_size,
        tiles=detector.paper_tiles, runs=args.runs, warmup=args.warmup,
        median_ms=statistics.median(samples), min_ms=min(samples), max_ms=max(samples),
        samples_ms=samples, detections=outputs[-1], stable_outputs=all(o == outputs[0] for o in outputs),
    )))


if __name__ == '__main__':
    main()
