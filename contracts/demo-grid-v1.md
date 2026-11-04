# Demo sector grid

Owner: `Tezqx` owns `mapping/grid_mapper.py`. Alan records the shared demo rule.
Status: draft on `task/AlanDD13/vision-pipeline`, not merged.

This is the follow-up to comment 6637306081. It is not a Grok measurement.
Grok still must not invent a sector. The label is an even split of one image.
`calibrated` is false. `mapId` is `demo-grid`. It is not `MappingEvidence`, it
does not enter the stationarity gate, and `POST /api/observations` continues
to reject a body that carries this grid.

## Geometry

The demo room is 2.0 m by 2.0 m, 4 columns by 4 rows, each cell 0.5 m.
Column 1 is the left image strip and column 4 is the right strip.
Row A is the bottom strip and row D is the top strip, because map +y is up.
Image y still increases downward.

For a contact pixel inside the frame:

```text
column = min(4, floor(x / width * 4) + 1)
rowFromTop = min(4, floor(y / height * 4) + 1)
rowFromBottom = 5 - rowFromTop
sector = "ABCD"[rowFromBottom - 1] + column
```

`pixel_to_sector(855, 423, 1920, 1080)` is `C2`. The centre of C2 is
`(0.75, 1.25)` m. The centre of A1 is `(0.25, 0.25)` m. The frame range is
half-open: `x == width` or `y == height` is outside and produces no label. `A5` and `E1` are not valid labels. The wire form is
`A1`, not `A-1`.

## Who publishes it

Mapping computes the label from `contactPointPx`. Vision may draw the same
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
    {"objectId": "overhead-1:session1:track6", "sector": "C2", "contactPointPx": [855, 423]}
  ]
}
```

That list is a display label. Backend must not turn it into an observation,
an eligibility change, or a mission. The vision overlay itself is not in this
commit: the lead checkout still has uncommitted preview edits, and those files
were left untouched.
