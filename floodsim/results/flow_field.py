"""Compact native-cell velocities, independent of arrow display sampling."""

import base64
from typing import Any

import numpy as np
from pyproj import Transformer

from floodsim.domain.geometry import AnalysisArea
from floodsim.providers.common import local_crs


def native_flow_field(
    u: np.ndarray, v: np.ndarray, wet: np.ndarray, *, area: AnalysisArea,
    cell_size_m: float, window_origin: tuple[int, int],
) -> dict[str, Any] | None:
    valid = wet & np.isfinite(u) & np.isfinite(v)
    rows, columns = np.nonzero(valid)
    if not len(rows):
        return None
    r0, r1 = int(rows.min()), int(rows.max()) + 1
    c0, c1 = int(columns.min()), int(columns.max()) + 1
    velocities = np.stack((u[r0:r1, c0:c1], v[r0:r1, c0:c1]), axis=-1).astype("<f4")
    velocities[~valid[r0:r1, c0:c1]] = np.nan
    x = -area.width_m / 2 + (window_origin[1] + c0) * cell_size_m
    y = -area.height_m / 2 + (window_origin[0] + r0) * cell_size_m
    height, width = velocities.shape[:2]
    transform = Transformer.from_crs(local_crs(area), "EPSG:4326", always_xy=True)
    return {
        "encoding": "float32-le-uv-base64", "width": width, "height": height,
        "cell_size_m": cell_size_m, "row_order": "south-to-north",
        "corners": [list(transform.transform(x, y)),
                    list(transform.transform(x + width * cell_size_m, y)),
                    list(transform.transform(x, y + height * cell_size_m))],
        "data": base64.b64encode(velocities.tobytes()).decode("ascii"),
    }
