"""«Симулятор склада»: что будет с остатком по дням, если заказать N штук сегодня.

Дневной спрос = помесячный прогноз сервиса / число дней месяца. Приходы: товары в пути — в дату ETA,
новый заказ — через срок поставки. Если товара нет, спрос дня теряется (остаток не уходит в минус).
"""
from __future__ import annotations

import pandas as pd


def simulate_stock(on_hand: float, forecast: pd.DataFrame, transit: pd.DataFrame, asof: pd.Timestamp,
                   lead_days: int, order_qty: float, days: int = 120) -> dict:
    """forecast: month, forecast (шт/мес); transit: qty, eta. -> dict с рядом по дням и сводкой."""
    asof = pd.Timestamp(asof).normalize()
    dates = pd.date_range(asof, periods=days, freq="D")
    fc = forecast.assign(month=pd.to_datetime(forecast["month"]).dt.to_period("M")).set_index("month")["forecast"]
    per = dates.to_period("M")
    daily = pd.Series([float(fc.get(p, fc.iloc[-1] if len(fc) else 0.0)) / p.days_in_month for p in per], index=dates)

    arrivals = pd.Series(0.0, index=dates)
    for r in transit.itertuples():
        eta = pd.Timestamp(r.eta).normalize()
        if eta in arrivals.index:
            arrivals[eta] += float(r.qty)
        elif eta < asof:
            arrivals.iloc[0] += float(r.qty)
    order_eta = asof + pd.Timedelta(days=int(lead_days))
    if order_qty > 0 and order_eta in arrivals.index:
        arrivals[order_eta] += float(order_qty)

    stock, lost, level = [], 0.0, float(max(on_hand, 0))
    for d in dates:
        level += arrivals[d]
        sold = min(level, daily[d])
        lost += daily[d] - sold
        level -= sold
        stock.append(level)
    s = pd.Series(stock, index=dates)
    out_days = s[s <= 1e-9].index
    return {
        "series": pd.DataFrame({"date": dates, "stock": s.values, "demand": daily.values}),
        "order_eta": order_eta,
        "first_stockout": out_days[0] if len(out_days) else None,
        "stockout_days": int(len(out_days)),
        "lost_units": lost,
        "avg_stock": float(s.mean()),
        "end_stock": float(s.iloc[-1]),
    }
