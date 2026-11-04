import numpy as np
from mapping.grid_mapper import pixel_to_sector
from vision.demo_grid import demo_cells, draw_demo_grid


def test_live_grid_uses_amirs_top_row_and_box_centre_convention():
    cells = demo_cells(1920, 1080)
    assert len(cells) == 16
    assert cells[0][0] == 'A1'
    assert cells[-1][0] == 'D4'
    assert all(pixel_to_sector(x, y, 1920, 1080) == label for label, x, y in cells)
    assert pixel_to_sector(855, 423, 1920, 1080) == 'B2'


def test_grid_is_only_a_display_overlay_and_draws_all_internal_dividers():
    original = np.zeros((400, 800, 3), dtype=np.uint8)
    display = original.copy()
    draw_demo_grid(display)
    assert not original.any()
    for x in (200, 400, 600): assert display[199, x].any()
    for y in (100, 200, 300): assert display[y, 399].any()
