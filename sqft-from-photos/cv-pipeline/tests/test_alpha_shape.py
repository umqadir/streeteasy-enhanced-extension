import numpy as np

from cv_pipeline.geometry.footprint import alpha_shape


def test_alpha_shape_square_area_reasonable():
    rng = np.random.default_rng(0)
    pts = rng.uniform(0, 1, size=(2000, 2))
    poly = alpha_shape(pts, alpha=10.0)
    # Should be close to the unit square for a large alpha.
    assert 0.9 < poly.area < 1.1

