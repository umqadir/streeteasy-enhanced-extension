import numpy as np

from cv_pipeline.sfm.scale import _robust_mad


def test_robust_mad_zero_on_constant():
    x = np.ones(10)
    assert _robust_mad(x) == 0.0

