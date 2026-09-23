"""Deterministic synthetic dataset for order recommendation must-have checks."""
from __future__ import annotations

import numpy as np
import pandas as pd


ASOF = pd.Timestamp("2026-09-01")
START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-08-31")


def make_dataset(seed: int = 0) -> dict[str, pd.DataFrame]:
    """Return sales, stock history, current stock, transit, and item catalog."""
    rng = np.random.default_rng(seed)
    skus = ["FLAT", "SEASON", "STOCKOUT", "GROWTH", "BIG"]
    suppliers = {"FLAT": "S1", "SEASON": "S1", "STOCKOUT": "S1", "GROWTH": "S2", "BIG": "S2"}
    months = pd.date_range(START, ASOF - pd.offsets.MonthBegin(1), freq="MS")
    sales_rows: list[dict] = []

    for sku in skus:
        for month in months:
            year_index = month.year - 2024
            if sku in ("FLAT", "BIG", "STOCKOUT"):
                target = 100.0
            elif sku == "SEASON":
                target = 100.0 * (2.5 if month.month in (6, 7, 8) else 0.4 if month.month in (12, 1, 2) else 1.0)
            else:
                target = 100.0 * 1.4**year_index

            if sku == "STOCKOUT" and month in pd.date_range("2026-03-01", "2026-05-01", freq="MS"):
                target /= 3.0

            # Daily small orders create many ordinary documents and realistic client variety.
            days = pd.date_range(month, month + pd.offsets.MonthEnd(0), freq="D")
            n_orders = max(1, int(round(target / 5.0)))
            chosen_days = rng.choice(days.to_numpy(), size=n_orders, replace=True)
            weights = rng.uniform(2.0, 8.0, size=n_orders)
            weights *= target / weights.sum()
            for i, (day, qty) in enumerate(zip(chosen_days, weights)):
                sales_rows.append({
                    "date": pd.Timestamp(day),
                    "sku": sku,
                    "qty": float(qty),
                    "doc": f"{sku}-{month:%Y%m}-{i:03d}",
                    "warehouse": "WH1",
                    "client": f"C{int(rng.integers(1, 31))}",
                })

    # A single extreme document exercises the one-off filter without changing the
    # ordinary daily order pattern for BIG.
    sales_rows.append({
        "date": pd.Timestamp("2026-06-15"), "sku": "BIG", "qty": 5000.0,
        "doc": "BIG-ONEOFF-20260615", "warehouse": "WH1", "client": "C99",
    })
    sales = pd.DataFrame(sales_rows).sort_values(["date", "sku", "doc"]).reset_index(drop=True)

    stock_rows = []
    for sku in skus:
        for month in months:
            begin_stock = 0.0 if sku == "STOCKOUT" and month in pd.date_range("2026-03-01", "2026-05-01", freq="MS") else 500.0
            stock_rows.append({"sku": sku, "month": month, "begin_stock": begin_stock})
    stock_hist = pd.DataFrame(stock_rows)
    stock_now = pd.DataFrame({"sku": skus, "on_hand": [50.0] * len(skus)})
    transit = pd.DataFrame(columns=["sku", "qty", "eta"])
    items = pd.DataFrame({
        "sku": skus,
        "name": [f"Synthetic {sku}" for sku in skus],
        "article": skus,
        "supplier": [suppliers[sku] for sku in skus],
        "category": ["Test category"] * len(skus),
        "moq": [10 if sku == "BIG" else 1 for sku in skus],
    })
    return {
        "sales": sales,
        "stock_hist": stock_hist,
        "stock_now": stock_now,
        "transit": transit,
        "items": items,
    }
