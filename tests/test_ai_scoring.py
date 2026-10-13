import numpy as np
import pandas as pd

from src.analysis.build_dashboard import FEATURES, score_isolation_forest


def test_isolation_forest_prioritizes_extreme_deterioration():
    rng = np.random.default_rng(42)
    rows = 180
    normal = pd.DataFrame({
        "sales_drop_pct": rng.uniform(0, 8, rows),
        "transaction_drop_pct": rng.uniform(0, 8, rows),
        "sales_below_3m_pct": rng.uniform(0, 10, rows),
        "transactions_below_3m_pct": rng.uniform(0, 10, rows),
        "inactive_days": rng.integers(0, 5, rows).astype(float),
        "cancel_ratio": rng.uniform(0, 0.08, rows),
        "partial_cancel_ratio": rng.uniform(0, 0.05, rows),
        "consecutive_declines": rng.integers(0, 2, rows).astype(float),
    })
    current = pd.DataFrame([
        dict(zip(FEATURES, [1, 2, 3, 4, 1, 0.01, 0.01, 0])),
        dict(zip(FEATURES, [95, 90, 85, 88, 60, 0.8, 0.6, 3])),
    ])

    scores, model_info = score_isolation_forest(normal, current)

    assert model_info["status"] == "OK"
    assert model_info["algorithm"] == "IsolationForest"
    assert scores[1] > scores[0]


def test_small_data_returns_clear_insufficient_status():
    small = pd.DataFrame([{feature: 0.0 for feature in FEATURES} for _ in range(4)])
    scores, model_info = score_isolation_forest(small, small.iloc[:2])
    assert len(scores) == 2
    assert model_info["status"] == "INSUFFICIENT_DATA"
