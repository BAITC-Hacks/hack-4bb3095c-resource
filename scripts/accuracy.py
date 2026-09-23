"""Точность прогноза на истории (holdout): сравнение с «Excel-подходом» (среднее за 12 мес.).

Для каждой точки отсечения t (01.04, 01.05, 01.06.2026) сервис видит только данные до t и прогнозирует
следующие 3 месяца. Сравниваем с фактическим регулярным спросом (без разовых заказов) этих месяцев —
только в месяцы, когда товар был в наличии (в дефицит продажи занижены и не равны спросу).
Метрика — WAPE = Σ|прогноз − факт| / Σ факт (чем меньше, тем лучше), по активным артикулам.
Запуск: .venv/bin/python -m scripts.accuracy  -> data/results/accuracy.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from engine.core import Params, compute_orders
from engine.loaders import load_all


def main():
    data = load_all()
    end = pd.Timestamp("2026-09-01")
    full = compute_orders(**data, params=Params(asof=end))
    actual = full.monthly.pivot(index="sku", columns="month", values="clean")
    avail = full.monthly.pivot(index="sku", columns="month", values="avail")
    sup = data["items"].set_index("sku").supplier

    rows = []
    for cut in pd.to_datetime(["2026-04-01", "2026-05-01", "2026-06-01"]):
        d = {
            "sales": data["sales"][data["sales"].date < cut],
            "stock_hist": data["stock_hist"][data["stock_hist"].month < cut],
            "stock_now": data["stock_now"], "transit": data["transit"].iloc[0:0], "items": data["items"],
            "monthly_hist": data["monthly_hist"][data["monthly_hist"].month < cut],
        }
        r = compute_orders(**d, params=Params(asof=cut))
        fc = r.forecast.pivot(index="sku", columns="month", values="forecast")
        months = pd.date_range(cut, periods=3, freq="MS")
        hist = r.monthly.pivot(index="sku", columns="month", values="clean")
        naive = hist.iloc[:, -12:].mean(axis=1)  # «как в Excel»: среднее за 12 мес.
        for m in months:
            a = actual[m]
            # только месяцы, когда товар был в наличии: в дефицит продажи «обрезаны» и не равны спросу
            active = a.index[(a > 0) & (avail[m] >= 0.999)]
            rows.append(pd.DataFrame({"sku": active, "month": m, "actual": a[active].values,
                                      "ours": fc.reindex(active)[m].fillna(0).values,
                                      "naive": naive.reindex(active).fillna(0).values}))
    df = pd.concat(rows)
    df["supplier"] = df.sku.map(sup)
    out = {}
    for s, g in df.groupby("supplier"):
        w_o = float(np.abs(g.ours - g.actual).sum() / g.actual.sum())
        w_n = float(np.abs(g.naive - g.actual).sum() / g.actual.sum())
        out[s] = {"wape_ours": round(w_o, 3), "wape_naive_12m_avg": round(w_n, 3),
                  "improvement": round(1 - w_o / w_n, 3), "points": int(len(g))}
    Path("data/results").mkdir(parents=True, exist_ok=True)
    Path("data/results/accuracy.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
