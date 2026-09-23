import pytest
import pandas as pd

import engine.loaders as loaders


@pytest.fixture(scope="module")
def loaded_data(tmp_path_factory):
    # load_all writes its pickle even with use_cache=False; isolate that side effect.
    original_cache = loaders.CACHE
    loaders.CACHE = tmp_path_factory.mktemp("loader-cache")
    try:
        return loaders.load_all(use_cache=False)
    finally:
        loaders.CACHE = original_cache


def test_load_all_returns_nonempty_tables(loaded_data):
    expected = {"sales", "stock_hist", "stock_now", "transit", "items", "monthly_hist"}
    assert expected <= loaded_data.keys()
    assert all(not loaded_data[key].empty for key in expected)


def test_items_have_expected_suppliers_and_valid_catalog_fields(loaded_data):
    items = loaded_data["items"]
    assert set(items["supplier"].unique()) == {"IEK", "Systeme Electric"}
    assert items["sku"].is_unique
    assert (items["moq"] >= 1).all()
    names = items["name"].astype("string").str.strip()
    assert names.notna().all() and names.ne("").all()
    assert names.ne(items["sku"].astype("string")).all()


def test_sales_stock_and_transit_ranges(loaded_data):
    sales = loaded_data["sales"]
    assert sales["date"].min() >= pd.Timestamp("2023-01-01")
    assert sales["date"].max() <= pd.Timestamp("2026-09-30")
    assert (sales["qty"] > 0).mean() > 0.99

    stock_hist = loaded_data["stock_hist"]
    assert stock_hist["month"].dt.day.eq(1).all()
    assert (loaded_data["stock_now"]["on_hand"] >= 0).all()

    transit = loaded_data["transit"]
    assert transit["eta"].min() >= pd.Timestamp("2026-09-01")


def test_systeme_electric_has_unit_cost_for_at_least_300_items(loaded_data):
    items = loaded_data["items"]
    covered = items.loc[
        items["supplier"].eq("Systeme Electric") & items["unit_cost"].gt(0), "sku"
    ].nunique()
    assert covered >= 300
