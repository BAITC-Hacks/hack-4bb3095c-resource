"""Пересчитать «Машину времени» (≈1–2 мин) и сохранить в data/cache/backtest.pkl.
Запуск: .venv/bin/python -m scripts.backtest"""
import pandas as pd

from engine.backtest import run_backtest
from engine.loaders import RAW, load_all


def unit_cost() -> pd.Series:
    """Себестоимость «СС реал» есть только в рабочей таблице Systeme Electric."""
    raw = pd.read_excel(RAW / "se_in_transit.xlsx", header=1)
    raw = raw.dropna(subset=["Код 1с"])
    raw["k"] = raw["Код 1с"].astype(str).str.strip()
    return raw.drop_duplicates("k").set_index("k")["СС реал"]


def main():
    bt = run_backtest(load_all(), unit_cost=unit_cost())
    pd.to_pickle(bt, "data/cache/backtest.pkl")
    print(pd.DataFrame(bt["by_supplier"]).T.to_string())


if __name__ == "__main__":
    main()
