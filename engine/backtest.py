"""«Машина времени»: что было бы, если бы заказы последние N месяцев считал наш сервис.

Честная постановка (без выдуманных данных):
  * старт — реальный начальный остаток каждого артикула на 1-е число стартового месяца (из 1С);
  * каждое 1-е число: сервис считает заказ, видя ТОЛЬКО данные до этой даты;
  * заказ приходит через срок поставки поставщика;
  * спрос месяца = реальные продажи + оценка упущенного спроса (месяцы, когда товара не было);
  * «Как было» — реальные начальные остатки из 1С за те же месяцы.
Сравниваем: число «месяцев без товара» по активным артикулам, неудовлетворённый спрос, средний запас (шт и ₸).
Упрощения (см. README): месячный шаг, приход в месяце прибытия доступен для продаж этого месяца.
"""
from __future__ import annotations

import pandas as pd

from engine.core import Params, compute_orders


def run_backtest(data: dict, start: str = "2026-03-01", months: int = 6, params: Params | None = None,
                 unit_cost: pd.Series | None = None) -> dict:
    base = params or Params()
    start = pd.Timestamp(start)
    steps = pd.date_range(start, periods=months, freq="MS")
    end = steps[-1] + pd.offsets.MonthBegin(1)

    # «истинный» спрос за период теста: считаем по полным данным (факт + упущенный, без разовых — разовые
    # тоже списываем со склада, т.к. их реально отгружали)
    full = compute_orders(**{k: v for k, v in data.items()}, params=Params(asof=end, lead_time_days=base.lead_time_days))
    mon = full.monthly[full.monthly.month.isin(steps)]
    demand = mon.pivot(index="sku", columns="month", values="raw") + (
        mon.pivot(index="sku", columns="month", values="corrected") - mon.pivot(index="sku", columns="month", values="clean"))

    sh = data["stock_hist"].pivot_table(index="sku", columns="month", values="begin_stock", aggfunc="sum")
    active = demand.index[(demand.sum(axis=1) > 0)]
    demand = demand.reindex(active).fillna(0)
    actual_stock = sh.reindex(index=active, columns=steps).fillna(0)

    inv = actual_stock[steps[0]].copy()
    pipeline: list[tuple[pd.Timestamp, pd.Series]] = []
    sim_begin, unmet, ordered = {}, {}, {}
    items = data["items"].set_index("sku")
    lead = items.supplier.map(lambda s: base.lead_time_days.get(s, base.default_lead_time)).reindex(active).fillna(30)

    for t in steps:
        # приход заказов, прибывающих в этом месяце
        arrive = pd.Series(0.0, index=active)
        rest = []
        for eta, q in pipeline:
            if eta < t + pd.offsets.MonthBegin(1):
                arrive = arrive.add(q.reindex(active).fillna(0))
            else:
                rest.append((eta, q))
        pipeline = rest
        sim_begin[t] = inv.copy()

        # расчёт заказа сервисом на дату t — только на данных до t
        d_t = {
            "sales": data["sales"][data["sales"].date < t],
            "stock_hist": data["stock_hist"][data["stock_hist"].month < t],
            "stock_now": pd.DataFrame({"sku": active, "on_hand": inv.values}),
            "transit": pd.DataFrame([(s, q, eta) for eta, ser in pipeline for s, q in ser[ser > 0].items()],
                                    columns=["sku", "qty", "eta"]),
            "items": data["items"],
        }
        if "monthly_hist" in data:
            d_t["monthly_hist"] = data["monthly_hist"][data["monthly_hist"].month < t]
        r = compute_orders(**d_t, params=Params(asof=t, lead_time_days=base.lead_time_days,
                                                review_days=30, service_z=base.service_z))
        q = r.orders.set_index("sku").rec_qty.reindex(active).fillna(0)
        ordered[t] = q
        for sup_lead in lead.unique():
            mask = lead == sup_lead
            eta = t + pd.Timedelta(days=int(sup_lead))
            pipeline.append((eta, q.where(mask, 0)))
        # заказы с ETA в этом же месяце (срок < 1 мес.) доступны сразу
        now_arr = [p for p in pipeline if p[0] < t + pd.offsets.MonthBegin(1)]
        pipeline = [p for p in pipeline if p[0] >= t + pd.offsets.MonthBegin(1)]
        for _, qq in now_arr:
            arrive = arrive.add(qq.reindex(active).fillna(0))

        avail = inv + arrive
        dem = demand[t]
        unmet[t] = (dem - avail).clip(lower=0)
        inv = (avail - dem).clip(lower=0)

    sim = pd.DataFrame(sim_begin)
    act_unmet = pd.DataFrame({t: demand[t].where(actual_stock[t] <= 0, 0) * 0 for t in steps})
    # «как было»: месяц без товара = начальный остаток 0 при наличии спроса; неудовл. спрос = упущенный
    lost = (mon.pivot(index="sku", columns="month", values="corrected")
            - mon.pivot(index="sku", columns="month", values="clean")).reindex(active).fillna(0)
    act_unmet = lost[steps]
    sim_unmet = pd.DataFrame(unmet)

    def value(df):
        if unit_cost is None:
            return None
        c = unit_cost.reindex(df.index).fillna(0)
        return float(df.mul(c, axis=0).mean(axis=1).sum())

    sup = items.supplier.reindex(active)
    out = {"steps": steps, "by_supplier": []}
    for s in sorted(sup.dropna().unique()):
        m = sup == s
        a_so = int(((actual_stock[m] <= 0) & (demand[m] > 0)).to_numpy().sum())
        s_so = int(((sim[m] <= 0) & (demand[m] > 0) & (sim_unmet[m] > 0)).to_numpy().sum())
        row = {
            "supplier": s, "skus": int(m.sum()),
            "actual_stockout_months": a_so, "ours_stockout_months": s_so,
            "actual_unmet_units": float(act_unmet[m].to_numpy().sum()),
            "ours_unmet_units": float(sim_unmet[m].to_numpy().sum()),
            "actual_avg_stock_units": float(actual_stock[m].mean(axis=1).sum()),
            "ours_avg_stock_units": float(sim[m].mean(axis=1).sum()),
        }
        if unit_cost is not None:
            cm = unit_cost.reindex(active[m]).fillna(0)
            has = cm > 0
            row["cost_coverage_skus"] = int(has.sum())
            row["actual_avg_stock_kzt"] = value(actual_stock[m][has.values])
            row["ours_avg_stock_kzt"] = value(sim[m][has.values])
        out["by_supplier"].append(row)
    out["sim_stock"] = sim
    out["actual_stock"] = actual_stock
    out["demand"] = demand
    out["ordered"] = pd.DataFrame(ordered)
    return out
