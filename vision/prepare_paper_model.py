"""Prepare reusable paper text embeddings once; no camera or paid model calls.

Run: python -m vision.prepare_paper_model [--test-image path/to/frame.jpg]
First preparation downloads official YOLOE weights and its text encoder.
The output and downloaded assets stay in ignored vision/.local/.
"""
import argparse
import json
import os
from pathlib import Path

from vision.perception import PAPER_CLASSES, detection_classes, resolve_device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-image', type=Path)
    args = parser.parse_args()
    test_image = args.test_image.resolve() if args.test_image else None
    output = Path(__file__).resolve().parent / '.local'
    output.mkdir(exist_ok=True)
    from ultralytics import YOLOE
    os.chdir(output)
    classes = detection_classes(PAPER_CLASSES)
    target = output / 'yoloe-26s-paper.pt'
    if target.exists():
        model = YOLOE(str(target))
        if tuple(model.names.values()) != classes:
            raise ValueError('Existing paper model has different prompts; choose a fresh output before preparing')
    else:
        model = YOLOE('yoloe-26s-seg.pt')
        model.set_classes(list(classes))
        model.save(str(target))
    print('Prepared model:', target)
    if test_image:
        from PIL import Image
        with Image.open(test_image) as image:
            result = model.predict(image.convert('RGB'), device=resolve_device('auto'),
                                   conf=.1, imgsz=960, verbose=False)[0]
        print(json.dumps([{'label': result.names[int(b.cls[0])], 'score': round(float(b.conf[0]), 4),
                           'bboxPx': [round(float(v), 1) for v in b.xyxy[0]]} for b in result.boxes]))


if __name__ == '__main__':
    main()
