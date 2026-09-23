import pytest

from engine.backtest import run_backtest
from engine.core import Params
from scripts.make_synthetic import make_dataset


@pytest.fixture(scope="module")
def synthetic_data():
    return make_dataset()


@pytest.fixture(scope="module")
def baseline(synthetic_data):
    return run_backtest(synthetic_data, start="2026-05-01", months=3)


def test_backtest_reports_supplier_metrics_and_nonnegative_values(baseline):
    rows = baseline["by_supplier"]
    assert {row["supplier"] for row in rows} >= {"S1", "S2"}

    keys = {
        "actual_stockout_months",
        "ours_stockout_months",
        "actual_avg_stock_units",
        "ours_avg_stock_units",
    }
    for row in rows:
        assert keys <= row.keys()
        assert all(row[key] >= 0 for key in keys)


def test_backtest_average_stock_is_monotone_in_service_level(synthetic_data):
    low = run_backtest(synthetic_data, start="2026-05-01", months=3, params=Params(service_z=0.84))
    high = run_backtest(synthetic_data, start="2026-05-01", months=3, params=Params(service_z=2.33))

    low_stock = sum(row["ours_avg_stock_units"] for row in low["by_supplier"])
    high_stock = sum(row["ours_avg_stock_units"] for row in high["by_supplier"])
    assert high_stock >= low_stock


def test_backtest_reports_made_to_order_exclusion(baseline):
    count = baseline["made_to_order_excluded"]
    assert isinstance(count, int)
    assert count >= 0
