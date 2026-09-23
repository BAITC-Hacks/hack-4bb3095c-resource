from __future__ import annotations

import pandas as pd

from engine.core import Params, compute_orders
from scripts.make_synthetic import ASOF, make_dataset


def _orders(data, *, asof=ASOF, params=None):
    return compute_orders(
        data["sales"], data["stock_hist"], data["stock_now"], data["transit"],
        data["items"], params=params or Params(asof=asof),
    )


def _order(result, sku):
    return result.orders.set_index("sku").loc[sku]


def test_each_input_source_affects_calculation_or_is_supported():
    base = make_dataset()
    baseline = _orders(base)
    base_flat_qty = _order(baseline, "FLAT").rec_qty

    with_transit = {key: value.copy() for key, value in base.items()}
    with_transit["transit"] = pd.DataFrame({
        "sku": ["FLAT"], "qty": [300.0], "eta": [ASOF + pd.Timedelta(days=5)],
    })
    assert _order(_orders(with_transit), "FLAT").rec_qty < base_flat_qty

    with_more_stock = {key: value.copy() for key, value in base.items()}
    with_more_stock["stock_now"].loc[with_more_stock["stock_now"].sku == "FLAT", "on_hand"] += 25
    assert _order(_orders(with_more_stock), "FLAT").rec_qty != base_flat_qty

    manual_growth = _orders(base, params=Params(asof=ASOF, growth_pct={"S1": 30}))
    assert _order(manual_growth, "FLAT").rec_qty > base_flat_qty

    changed_category = {key: value.copy() for key, value in base.items()}
    changed_category["items"].loc[changed_category["items"].sku == "SEASON", "category"] = "Another category"
    result = _orders(changed_category)
    assert result.forecast.loc[result.forecast.sku == "SEASON", "forecast"].notna().all()


def test_seasonal_forecast_has_summer_peak():
    data = make_dataset()
    result = _orders(data, asof=pd.Timestamp("2026-04-01"))
    forecast = result.forecast[result.forecast.sku == "SEASON"].set_index("month").forecast
    assert forecast.loc[pd.Timestamp("2026-07-01")] >= 1.8 * forecast.loc[pd.Timestamp("2026-05-01")]


def test_stockout_recovers_lost_demand_and_raises_level():
    data = make_dataset()
    with_stockout = _orders(data)
    no_stockout = {key: value.copy() for key, value in data.items()}
    no_stockout["stock_hist"].loc[no_stockout["stock_hist"].sku == "STOCKOUT", "begin_stock"] = 500.0
    without = _orders(no_stockout)

    observed = _order(with_stockout, "STOCKOUT")
    baseline = _order(without, "STOCKOUT")
    assert observed.lost_demand_12m > 0
    assert observed.level_month >= baseline.level_month * 1.10


def test_big_oneoff_is_excluded_from_regular_level():
    data = make_dataset()
    with_oneoff = _orders(data)
    without_data = {key: value.copy() for key, value in data.items()}
    without_data["sales"] = without_data["sales"][without_data["sales"].doc != "BIG-ONEOFF-20260615"].copy()
    without = _orders(without_data)

    observed = _order(with_oneoff, "BIG")
    baseline = _order(without, "BIG")
    assert observed.level_month <= baseline.level_month * 1.10
    assert observed.oneoff_excluded >= 4000


def test_order_output_has_reason_supplier_groups_and_moq_multiples():
    data = make_dataset()
    orders = _orders(data).orders
    positive = orders[orders.rec_qty > 0]
    assert positive.reason.notna().all() and positive.reason.str.strip().ne("").all()
    assert set(orders.groupby("supplier").groups) == {"S1", "S2"}
    assert ((positive.rec_qty % positive.moq) == 0).all()
