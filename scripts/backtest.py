"""Пересчитать «Машину времени» (≈1–2 мин) и сохранить итоги в data/results/backtest_<z>.json.
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


def main(service_z: float = 1.65):
    from engine.core import Params
    bt = run_backtest(load_all(), params=Params(service_z=service_z), unit_cost=unit_cost())
    bt["service_z"] = service_z
    import json
    from pathlib import Path
    Path("data/results").mkdir(parents=True, exist_ok=True)
    Path(f"data/results/backtest_{service_z}.json").write_text(
        json.dumps({"service_z": service_z, "made_to_order_excluded": bt["made_to_order_excluded"], "by_supplier": bt["by_supplier"]}, ensure_ascii=False, indent=1))
    print(pd.DataFrame(bt["by_supplier"]).T.to_string())


if __name__ == "__main__":
    import sys
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 1.65)
