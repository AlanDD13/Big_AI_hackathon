# Demo sector grid

Owner: `Tezqx` owns `mapping/grid_mapper.py`. Alan records the shared demo rule.
Status: live-demo integration proposal on `task/AlanDD13/live-demo-grid`, not merged.

This is the follow-up to comment 6637306081. It is not a Grok measurement.
Grok still must not invent a sector. The label is an even split of one image.
`calibrated` is false. `mapId` is `demo-grid`. It is not `MappingEvidence`, it
does not enter the stationarity gate, and `POST /api/observations` continues
to reject a body that carries this grid.

## Geometry

The demo room is 2.0 m by 2.0 m, 4 columns by 4 rows, each cell 0.5 m.
Column 1 is the left image strip and column 4 is the right strip.
Row A is the top strip and row D is the bottom strip. This adopts Tezqx's
3954c4f convention for Alan's requested live integration, replacing the earlier
A-bottom rule. The demo origin is bottom centre: x grows rightward and y grows
forward from the bottom. Image y grows downward.

For a bounding-box centre pixel inside the frame:

```text
column = floor(x / width * 4) + 1
rowFromTop = floor(y / height * 4)
sector = "ABCD"[rowFromTop] + column
```

`pixel_to_sector(855, 423, 1920, 1080)` is `B2`. The centre of B2 is
`(-0.25, 1.25)` m. The centre of A1 is `(-0.75, 1.75)` m. The frame range is
half-open: `x == width` or `y == height` is outside and produces no label. `A5` and `E1` are not valid labels. The wire form is
`A1`, not `A-1`.

## Who publishes it

Mapping computes this display label from the bbox centre; measured floor
projection still requires floor-contact evidence. Vision draws the same
grid and the same label on its own preview, using this formula, and may send
a separate demo list:

```json
{
  "cameraId": "overhead-1",
  "frameId": 6,
  "width": 1920,
  "height": 1080,
  "mapId": "demo-grid",
  "calibrated": false,
  "sectors": [
    {"objectId": "overhead-1:session1:track6", "sector": "B2", "bboxCenterPx": [855, 423]}
  ]
}
```

That list is a display label. Backend must not turn it into an observation,
an eligibility change, or a mission. The iPad page draws this 4x4 grid on a
separate canvas, keeping raw uploads unchanged. The PC viewer uses mapping's
canonical helper and labels object box centres with demo sectors.

No saved floor polygon is automatically reused with a changed camera, lens,
crop or aspect ratio. The old 1920x1080 phone outline does not calibrate the
current 3840x2760 iPad view. These cells are not measured 0.5m squares. A future
adaptive floor grid needs current image-to-measured-floor correspondences;
an arbitrary floor mask alone cannot establish metric geometry.
