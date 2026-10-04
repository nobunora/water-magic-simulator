import base64

import numpy as np

from floodsim.domain.geometry import AnalysisArea, GeoBounds, LonLat
from floodsim.results.flow_field import native_flow_field


def test_native_half_metre_field_preserves_independent_cell_velocities():
    area = AnalysisArea(mode="rectangle", bounds=GeoBounds(west_deg=138.999,
        south_deg=34.999, east_deg=139.001, north_deg=35.001),
        center=LonLat(lon_deg=139, lat_deg=35), width_m=2, height_m=2, area_m2=4)
    u = np.arange(16, dtype=np.float32).reshape(4, 4)
    v = -u
    wet = np.ones((4, 4), dtype=bool)
    wet[1, 1] = False
    field = native_flow_field(u, v, wet, area=area, cell_size_m=0.5, window_origin=(0, 0))
    assert field["width"] == field["height"] == 4
    assert field["cell_size_m"] == 0.5
    cells = np.frombuffer(base64.b64decode(field["data"]), dtype="<f4").reshape(4, 4, 2)
    np.testing.assert_array_equal(cells[wet, 0], u[wet])
    np.testing.assert_array_equal(cells[wet, 1], v[wet])
    assert np.isnan(cells[1, 1]).all()
    assert len(set(cells[:2, :2, 0].ravel()[[0, 1, 2]])) == 3
    cropped = native_flow_field(u[2:, 2:], v[2:, 2:], wet[2:, 2:], area=area,
        cell_size_m=0.5, window_origin=(2, 2))
    assert cropped["width"] == cropped["height"] == 2
    assert cropped["corners"][0] == [139, 35]
    assert native_flow_field(u, v, np.zeros_like(wet), area=area,
        cell_size_m=0.5, window_origin=(0, 0)) is None
