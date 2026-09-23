"""Регрессии по итогам код-ревью: пустые данные, пропуски остатков, малые выборки, сплошной дефицит."""
import pandas as pd

from engine.core import Params, compute_orders
from scripts.make_synthetic import ASOF, make_dataset


def _run(d, **kw):
    return compute_orders(d["sales"], d["stock_hist"], d["stock_now"], d["transit"], d["items"],
                          params=Params(asof=kw.get("asof", ASOF)))


def test_empty_sales_does_not_crash():
    d = make_dataset()
    d["sales"] = d["sales"].iloc[0:0]
    r = compute_orders(d["sales"], d["stock_hist"], d["stock_now"], d["transit"], d["items"])
    assert len(r.orders) == len(d["items"])
    assert (r.orders.rec_qty == 0).all()


def test_missing_stock_row_is_not_a_stockout():
    d = make_dataset()
    base = _run(d).orders.set_index("sku").loc["FLAT"]
    sh = d["stock_hist"]
    d["stock_hist"] = sh[~((sh.sku == "FLAT") & (sh.month == pd.Timestamp("2026-04-01")))]
    r = _run(d).orders.set_index("sku").loc["FLAT"]
    assert r.lost_demand_12m == base.lost_demand_12m == 0


def test_extreme_order_filtered_with_few_documents():
    d = make_dataset()
    rows = [(pd.Timestamp(f"2026-0{m}-10"), "RARE", 10.0, f"R{m}", "Склад", "C1") for m in (3, 4, 5, 6)]
    rows.append((pd.Timestamp("2026-07-10"), "RARE", 5000.0, "R-BIG", "Склад", "C99"))
    d["sales"] = pd.concat([d["sales"], pd.DataFrame(rows, columns=d["sales"].columns)], ignore_index=True)
    d["items"] = pd.concat([d["items"], pd.DataFrame([{"sku": "RARE", "name": "Редкий", "article": "", "supplier": "S1",
                                                       "category": "X", "moq": 1}])], ignore_index=True)
    r = _run(d).orders.set_index("sku").loc["RARE"]
    assert r.oneoff_excluded >= 4900


def test_permanent_stockout_still_estimates_lost_demand():
    d = make_dataset()
    d["stock_hist"].loc[d["stock_hist"].sku == "FLAT", "begin_stock"] = 0
    r = _run(d).orders.set_index("sku").loc["FLAT"]
    assert r.lost_demand_12m > 0
