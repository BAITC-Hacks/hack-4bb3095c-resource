import pandas as pd

from engine.simulate import simulate_stock

ASOF = pd.Timestamp("2026-09-23")
FC = pd.DataFrame({"month": pd.date_range("2026-09-01", periods=6, freq="MS"), "forecast": [300.0] * 6})
NO_TRANSIT = pd.DataFrame(columns=["qty", "eta"])


def test_bigger_order_means_fewer_stockout_days_and_more_stock():
    small = simulate_stock(100, FC, NO_TRANSIT, ASOF, 40, 0)
    big = simulate_stock(100, FC, NO_TRANSIT, ASOF, 40, 1500)
    assert small["stockout_days"] > big["stockout_days"]
    assert big["avg_stock"] > small["avg_stock"]
    assert small["first_stockout"] is not None


def test_transit_arrival_delays_stockout():
    base = simulate_stock(50, FC, NO_TRANSIT, ASOF, 40, 0)
    tr = pd.DataFrame({"qty": [500.0], "eta": [ASOF + pd.Timedelta(days=3)]})
    with_tr = simulate_stock(50, FC, tr, ASOF, 40, 0)
    assert with_tr["first_stockout"] > base["first_stockout"]


def test_stock_never_negative():
    r = simulate_stock(0, FC, NO_TRANSIT, ASOF, 40, 0)
    assert (r["series"].stock >= 0).all() and r["lost_units"] > 0
