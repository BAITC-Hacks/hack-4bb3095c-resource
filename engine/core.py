"""Ядро расчёта рекомендованных заказов поставщикам. Детерминированно и объяснимо.

Конвейер для каждого артикула:
 1. Разовые крупные заказы: документ (или клиент-месяц) с количеством выше робастного порога
    max(Q3 + 3·IQR, 5·медиана, 2·P90) урезается до медианного «обычного» заказа — излишек исключается
    из регулярного спроса.
 2. Помесячный «чистый» спрос за полные месяцы.
 3. Сезонность: индекс группы (поставщик × категория) + собственный индекс артикула, если его
    сезонный профиль повторяется из года в год (корреляция профилей) — взвешенная смесь.
 4. Упущенный спрос: месяцы с нулевым остатком (stockout) досчитываются до ожидаемого уровня.
 5. Уровень и устойчивый рост: десезонализированный спрос, рост год-к-году учитывается только если
    он устойчив (совпадает по знаку в обоих полугодиях), с затуханием; + ручной прогноз прироста %.
 6. Прогноз на горизонт = срок поставки + период пересмотра, страховой запас z·σ·√(H/30).
 7. Потребность = прогноз + страховой запас − остаток − товары в пути (прибывающие до конца горизонта),
    округление вверх до кратности/MOQ. Срочность — по покрытию остатком срока поставки.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


@dataclass
class Params:
    asof: pd.Timestamp | None = None               # дата расчёта (по умолчанию — день после последней продажи)
    lead_time_days: dict = field(default_factory=lambda: {"IEK": 40, "Systeme Electric": 30})
    default_lead_time: int = 30
    review_days: int = 30                          # как часто делается заказ (период пересмотра)
    service_z: float = 1.65                        # 95% уровень сервиса
    growth_pct: dict = field(default_factory=dict)  # ручной прогноз прироста: {"IEK": 10, "IEK|Узо": 5}
    history_months: int = 36
    oneoff_iqr_k: float = 3.0
    oneoff_median_k: float = 5.0
    min_docs_for_oneoff: int = 6
    recurring_months: int = 4
    group_season_strength: float = 0.25   # подобрано по holdout-точности (scripts/accuracy.py)
    own_season_min_corr: float = 0.7
    own_season_max_w: float = 0.9


@dataclass
class Result:
    orders: pd.DataFrame          # все артикулы с расчётом (rec_qty может быть 0)
    monthly: pd.DataFrame         # sku, month, raw, clean, corrected, avail, season
    forecast: pd.DataFrame        # sku, month, forecast (будущие месяцы)
    oneoffs: pd.DataFrame         # исключённые разовые заказы
    params: Params

    @property
    def to_order(self) -> pd.DataFrame:
        return self.orders[self.orders.rec_qty > 0]


def _month(ts) -> pd.Timestamp:
    return pd.Timestamp(ts).to_period("M").to_timestamp()


# ---------------------------------------------------------------- 1. разовые заказы
def detect_oneoffs(sales: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (sales с колонкой qty_clean, таблица исключённых заказов)."""
    s = sales.copy()
    key = ["sku", "doc"]
    has_client = "client" in s.columns and s["client"].notna().any()
    docs = s.groupby(key, as_index=False).agg(date=("date", "min"), qty=("qty", "sum"),
                                               **({"client": ("client", "first")} if has_client else {}))
    pos = docs[docs.qty > 0]

    def thresholds(g: pd.Series) -> float:
        if len(g) < 3:
            return np.inf
        if len(g) < p.min_docs_for_oneoff:  # мало документов — только явный экстремальный выброс
            return 10 * g.median()
        q1, q3, med, p90 = g.quantile([0.25, 0.75, 0.5, 0.9])
        return max(q3 + p.oneoff_iqr_k * (q3 - q1), p.oneoff_median_k * med, 2 * p90)

    stats = pos.groupby("sku")["qty"].agg(thr=thresholds, typical="median")
    docs = docs.merge(stats, on="sku", how="left")
    docs["oneoff"] = docs.qty > docs.thr
    # Крупные заказы, которые повторяются (≥ recurring_months разных месяцев за последний год) —
    # это регулярный оптовый спрос, а не разовый всплеск: их не исключаем.
    docs["m"] = docs.date.map(_month)
    recent = docs.oneoff & (docs.date >= docs.date.max() - pd.DateOffset(months=12))
    rec_m = docs[recent].groupby("sku")["m"].nunique()
    regular = rec_m[rec_m >= p.recurring_months].index
    docs.loc[docs.sku.isin(regular), "oneoff"] = False
    reason = np.where(docs.oneoff, "разовый крупный заказ (документ)", "")

    if has_client:  # крупная продажа одному клиенту, разбитая на несколько документов в месяце
        cm = docs[~docs.oneoff].groupby(["sku", "client", "m"], as_index=False)["qty"].sum()
        cstats = cm[cm.qty > 0].groupby("sku")["qty"].agg(cthr=thresholds)
        cm = cm.merge(cstats, on="sku", how="left")
        big = cm[cm.qty > cm.cthr][["sku", "client", "m"]].assign(client_big=True)
        docs = docs.merge(big, on=["sku", "client", "m"], how="left")
        flag = docs.client_big.fillna(False).astype(bool) & ~docs.oneoff
        docs.loc[flag, "oneoff"] = True
        reason = np.where(flag, "крупная продажа одному клиенту", reason)
    docs["reason"] = reason

    docs["excluded"] = np.where(docs.oneoff, docs.qty - docs.typical.fillna(0), 0.0).clip(min=0)
    # распределяем исключение по строкам документа пропорционально
    s = s.merge(docs[key + ["excluded", "qty"]].rename(columns={"qty": "doc_qty"}), on=key, how="left")
    share = np.where(s.doc_qty > 0, s.qty / s.doc_qty, 0)
    s["qty_clean"] = s.qty - s.excluded.fillna(0) * share
    oneoffs = docs[docs.oneoff][["sku", "doc", "date", "qty", "typical", "excluded", "reason"]
                                + (["client"] if has_client else [])]
    return s.drop(columns=["excluded", "doc_qty"]), oneoffs.sort_values("excluded", ascending=False)


# ---------------------------------------------------------------- 3. сезонность
def _year_profiles(series: pd.Series) -> list[np.ndarray]:
    """Сезонные профили по «годам» через отношение к центрированной 12-мес. скользящей средней
    (классическая декомпозиция: тренд/рост не маскируется под сезонность).
    Возвращает до 2 профилей: по чётным/нечётным окнам, чтобы проверить повторяемость по годам."""
    s = series.astype(float)
    if len(s) < 24 or s.sum() <= 0:
        return []
    cma = s.rolling(12, center=True).mean().rolling(2).mean().shift(-1)
    ratio = (s / cma.replace(0, np.nan)).dropna()
    if len(ratio) < 12:
        return []
    # первые 12 и последние 12 точек отношения = два независимых «года» (перекрываются при короткой истории)
    out = []
    for chunk in (ratio.iloc[:12], ratio.iloc[-12:]):
        prof = np.ones(12)
        for ts, v in chunk.items():
            prof[ts.month - 1] = v
        if np.isfinite(prof).all():
            out.append(prof)
    return out


def _norm(idx: np.ndarray) -> np.ndarray:
    idx = np.clip(idx, 0.3, 3.0)
    return idx / idx.mean()


def seasonal_indices(pivot: pd.DataFrame, items: pd.DataFrame, group_strength: float = 0.25,
                     min_corr: float = 0.5, own_max_w: float = 0.85) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """pivot: sku x month (clean). -> (sku x 12 индекс, вес собственного профиля, источник)."""
    grp = items.set_index("sku").reindex(pivot.index)
    group_key = grp.supplier.fillna("?") + "|" + grp.category.fillna("?")
    sup_idx, grp_idx = {}, {}
    for sup, rows in pivot.groupby(grp.supplier.fillna("?")):
        prof = _year_profiles(rows.sum())
        sup_idx[sup] = _norm(np.mean(prof, axis=0)) if prof else np.ones(12)
    for gk, rows in pivot.groupby(group_key):
        prof = _year_profiles(rows.sum())
        base = sup_idx.get(gk.split("|")[0], np.ones(12))
        if prof:
            w = len(rows) / (len(rows) + 10)
            grp_idx[gk] = _norm(w * np.mean(prof, axis=0) + (1 - w) * base)
        else:
            grp_idx[gk] = base

    out, weights, source = {}, {}, {}
    for sku, row in pivot.iterrows():
        g = grp_idx[group_key[sku]]
        prof = _year_profiles(row) if row.mean() >= 3 else []
        w = 0.0
        if len(prof) >= 2:
            c = np.corrcoef(prof[-2], prof[-1])[0, 1]
            amp = np.mean([pr.max() - pr.min() for pr in prof])
            if np.isfinite(c) and c > min_corr and amp > 0.4:
                # вес растёт с повторяемостью профиля: c=min_corr → 0, c=1 → own_max_w (подобрано по holdout)
                w = float(own_max_w * (c - min_corr) / (1 - min_corr))
        own = _norm(np.mean(prof, axis=0)) if prof else np.ones(12)
        # групповой профиль — «мягко» (по бэктесту точности полная сила группового профиля добавляет шум на
        # уровне артикула); собственный профиль — в полную силу, только если он подтверждён повторением по годам
        g_soft = 1 + group_strength * (g - 1)
        out[sku] = _norm(w * own + (1 - w) * g_soft)
        weights[sku] = w
        source[sku] = "собственная (повторяется по годам)" if w > 0 else "по группе товаров"
    return pd.DataFrame.from_dict(out, orient="index"), pd.Series(weights), pd.Series(source)


# ---------------------------------------------------------------- 4. доступность / stockout
def availability(stock_hist: pd.DataFrame, months: pd.DatetimeIndex, skus: pd.Index,
                 raw: pd.DataFrame, explicit: pd.DataFrame | None = None) -> pd.DataFrame:
    """Доля месяца, когда товар был в наличии (1 = всегда). По начальным остаткам месяца."""
    avail = pd.DataFrame(1.0, index=skus, columns=months)
    if stock_hist is not None and not stock_hist.empty:
        sh = stock_hist.pivot_table(index="sku", columns="month", values="begin_stock", aggfunc="sum")
        sh = sh.reindex(index=skus)
        known = sh.notna().any(axis=1)
        all_m = months.append(pd.DatetimeIndex([months[-1] + pd.offsets.MonthBegin(1)]))
        sh = sh.reindex(columns=all_m)
        unknown = (sh[months].isna().to_numpy() | sh[all_m[1:]].isna().to_numpy())  # нет записи ≠ нулевой остаток
        sh = sh.fillna(0)
        b0 = sh[months].to_numpy() > 0
        b1 = sh[all_m[1:]].to_numpy() > 0
        sold = raw.reindex(index=skus, columns=months).fillna(0).to_numpy() > 0
        a = np.ones_like(b0, dtype=float)
        a[~b0 & ~b1] = np.where(sold[~b0 & ~b1], 0.5, 0.0)
        a[~b0 & b1] = 0.5
        a[b0 & ~b1] = 0.75
        a[~known.to_numpy()] = 1.0
        a[unknown] = 1.0
        # товар считается «в дефиците», только если он вообще продавался (иначе это не упущенный спрос)
        active = raw.reindex(index=skus, columns=months).fillna(0).sum(axis=1).to_numpy() > 0
        a[~active] = 1.0
        avail = pd.DataFrame(a, index=skus, columns=months)
    if explicit is not None and not explicit.empty:
        for r in explicit.itertuples():
            m = _month(r.month)
            if r.sku in avail.index and m in avail.columns:
                avail.loc[r.sku, m] = min(avail.loc[r.sku, m], float(getattr(r, "avail", 0.0)))
    return avail


# ---------------------------------------------------------------- главный расчёт
def compute_orders(sales: pd.DataFrame, stock_hist: pd.DataFrame, stock_now: pd.DataFrame,
                   transit: pd.DataFrame, items: pd.DataFrame, params: Params | None = None,
                   stockouts: pd.DataFrame | None = None, monthly_hist: pd.DataFrame | None = None) -> Result:
    """monthly_hist (sku, month, qty) — помесячная история для периода ДО начала построчных продаж
    (в данных ЕКТ документы есть с 2025-01, помесячные итоги — с 2024-01)."""
    p = params or Params()
    if p.asof is not None:
        asof = pd.Timestamp(p.asof)
    elif len(sales) and pd.notna(sales.date.max()):
        asof = sales.date.max().normalize() + pd.Timedelta(days=1)
    else:
        asof = pd.Timestamp.today().normalize()
    p.asof = asof
    cur_m = _month(asof)
    sales = sales[sales.date < asof]

    s_clean, oneoffs = detect_oneoffs(sales, p)
    s_clean["month"] = s_clean.date.map(_month)
    s_clean = s_clean[s_clean.month < cur_m]
    first = s_clean.month.min() if len(s_clean) else pd.NaT
    start = cur_m - pd.DateOffset(months=12) if pd.isna(first) else max(first, cur_m - pd.DateOffset(months=p.history_months))
    months = pd.date_range(start, cur_m - pd.DateOffset(months=1), freq="MS")

    skus = pd.Index(sorted(set(items.sku)))
    raw = s_clean.pivot_table(index="sku", columns="month", values="qty", aggfunc="sum").reindex(
        index=skus, columns=months).fillna(0).clip(lower=0)
    clean = s_clean.pivot_table(index="sku", columns="month", values="qty_clean", aggfunc="sum").reindex(
        index=skus, columns=months).fillna(0).clip(lower=0)
    if monthly_hist is not None and not monthly_hist.empty:
        # построчная история начинается с первого месяца с реальными отгрузками; раньше — помесячные итоги 1С
        tot = s_clean[s_clean.qty > 0].groupby("month")["qty"].sum()
        doc_start = tot[tot > 0.2 * tot.median()].index.min() if len(tot) else cur_m
        mh = monthly_hist.assign(month=monthly_hist.month.map(_month))
        start = mh.month.min()
        months = pd.date_range(max(start, cur_m - pd.DateOffset(months=p.history_months)),
                               cur_m - pd.DateOffset(months=1), freq="MS")
        mh_p = mh.pivot_table(index="sku", columns="month", values="qty", aggfunc="sum").reindex(
            index=skus, columns=months).fillna(0).clip(lower=0)
        raw = raw.reindex(columns=months).fillna(0)
        clean = clean.reindex(columns=months).fillna(0)
        early = months[months < doc_start]
        raw[early] = mh_p[early]
        # до 2025 г. документов нет — разовые всплески гасим на уровне месяца (≤ 3× медианы месяцев артикула)
        med = mh_p[early].where(mh_p[early] > 0).median(axis=1).fillna(0)
        if len(early):
            clean[early] = mh_p[early].clip(upper=3 * med, axis=0)

    season, season_w, season_src = seasonal_indices(clean, items, p.group_season_strength, p.own_season_min_corr,
                                                         p.own_season_max_w)
    sidx = season.to_numpy()[:, [m.month - 1 for m in months]]  # sku x months

    avail = availability(stock_hist, months, skus, raw, stockouts)
    A = avail.to_numpy()
    C = clean.to_numpy()
    deseas = C / sidx

    # уровень по месяцам с полной доступностью (последние 12, иначе вся история)
    full = A >= 0.999
    last12 = np.zeros(len(months), dtype=bool)
    last12[-12:] = True
    def masked_mean(mask):
        m = full & mask
        cnt = m.sum(axis=1)
        return np.where(cnt > 0, (deseas * m).sum(axis=1) / np.maximum(cnt, 1), np.nan)
    lvl_ok = masked_mean(last12)
    lvl_ok = np.where(np.isnan(lvl_ok), masked_mean(np.ones(len(months), bool)), lvl_ok)
    # Нет ни одного месяца с полной доступностью, но продажи есть — типичный товар «под заказ» (пришёл и сразу
    # отгружен клиенту). Упущенный спрос для него НЕ досчитываем (иначе удвоим спрос), а явно помечаем в обосновании.
    never_stocked = np.isnan(lvl_ok) & (C.sum(axis=1) > 0)
    lvl_ok = np.nan_to_num(lvl_ok)

    expected = lvl_ok[:, None] * sidx
    corrected = np.where(A < 0.999, np.maximum(C, C + (1 - A) * expected), C)
    corrected[never_stocked] = C[never_stocked]
    lost = corrected - C

    # уровень, рост, волатильность — по скорректированному спросу
    D = corrected / sidx
    n = len(months)
    l6 = D[:, -6:].mean(axis=1) if n >= 6 else D.mean(axis=1)
    l12 = D[:, -12:].mean(axis=1)
    level = 0.6 * l6 + 0.4 * l12
    p12 = D[:, -24:-12].mean(axis=1) if n >= 24 else np.full(len(skus), np.nan)
    growth = np.where((p12 > 0) & ~np.isnan(p12), l12 / np.where(p12 > 0, p12, 1) - 1, 0.0)
    h1 = D[:, -12:-6].mean(axis=1) if n >= 12 else l12
    p_h1 = D[:, -24:-18].mean(axis=1) if n >= 24 else h1
    g_h1 = np.where(p_h1 > 0, h1 / np.where(p_h1 > 0, p_h1, 1) - 1, 0)
    g_h2 = np.where((D[:, -18:-12].mean(axis=1) if n >= 24 else l6) > 0,
                    l6 / np.maximum(D[:, -18:-12].mean(axis=1) if n >= 24 else l6, 1e-9) - 1, 0)
    steady = (np.sign(g_h1) == np.sign(g_h2)) & (np.abs(growth) > 0.05)
    trend = np.where(steady, np.clip(growth, -0.3, 0.5) * 0.5, 0.0)  # затухающий годовой тренд
    resid = D[:, -12:] - level[:, None]
    # робастная волатильность (MAD) — разовые всплески и аномальные месяцы не раздувают страховой запас
    sigma = 1.4826 * np.median(np.abs(resid - np.median(resid, axis=1, keepdims=True)), axis=1)

    it = items.set_index("sku").reindex(skus)
    sup = it.supplier.fillna("?")
    lead = sup.map(lambda x: p.lead_time_days.get(x, p.default_lead_time)).astype(float).to_numpy()
    manual = np.array([1 + (p.growth_pct.get(f"{a}|{b}", p.growth_pct.get(a, 0)) or 0) / 100
                       for a, b in zip(sup, it.category.fillna(""))])
    horizon = lead + p.review_days

    # прогноз по дням горизонта
    max_h = int(horizon.max()) + 1
    days = pd.date_range(asof, periods=max_h, freq="D")
    dm = days.month.to_numpy() - 1
    dim = days.days_in_month.to_numpy()
    t_months = (np.arange(max_h) / 30.4)[None, :]
    daily = (level[:, None] * manual[:, None] * season.to_numpy()[:, dm] / dim[None, :]
             * (1 + trend[:, None]) ** (t_months / 12))
    cum = np.cumsum(daily, axis=1)
    d_h = cum[np.arange(len(skus)), horizon.astype(int) - 1]
    d_lt = cum[np.arange(len(skus)), lead.astype(int) - 1]
    safety = np.minimum(p.service_z * sigma * np.sqrt(horizon / 30.0) * manual, d_h)

    on_hand = stock_now.groupby("sku")["on_hand"].sum().reindex(skus).fillna(0).clip(lower=0).to_numpy()
    tr = transit.copy()
    tr["eta"] = pd.to_datetime(tr["eta"])
    def transit_until(limit_days):
        lim = asof + pd.to_timedelta(limit_days, unit="D")
        t = tr.merge(pd.DataFrame({"sku": skus, "lim": lim}), on="sku")
        return t[t.eta <= t.lim].groupby("sku")["qty"].sum().reindex(skus).fillna(0).to_numpy()
    in_tr_h = transit_until(horizon)
    in_tr_lt = transit_until(lead)
    in_tr_all = tr.groupby("sku")["qty"].sum().reindex(skus).fillna(0).to_numpy()

    need = d_h + safety - on_hand - in_tr_h
    moq = it.moq.fillna(1).clip(lower=1).to_numpy()
    rec = np.where(need > 0.5, np.ceil(np.maximum(need, 0) / moq) * moq, 0)

    daily_avg = np.where(lead > 0, d_lt / lead, 0)
    cover = np.where(daily_avg > 1e-9, (on_hand + in_tr_lt) / np.maximum(daily_avg, 1e-9), np.inf)
    urgency = np.where(cover < lead, "Критично", np.where(cover < lead + p.review_days / 2, "Высокая", "Плановая"))
    risk = np.clip(1 - cover / horizon, 0, 1)

    oneoff_sum = oneoffs.groupby("sku")["excluded"].sum().reindex(skus).fillna(0).to_numpy()
    oneoff_n = oneoffs.groupby("sku").size().reindex(skus).fillna(0).astype(int).to_numpy()
    lost12 = lost[:, -12:].sum(axis=1)
    so_months = (A[:, -12:] < 0.999).sum(axis=1)
    next_m = (cur_m + pd.DateOffset(months=1)).month - 1

    orders = pd.DataFrame({
        "sku": skus, "article": it.article.fillna("").to_numpy(), "name": it.name.to_numpy(),
        "supplier": sup.to_numpy(), "category": it.category.fillna("").to_numpy(),
        "avg_month_raw": raw.to_numpy()[:, -12:].mean(axis=1), "level_month": level,
        "season_next": season.to_numpy()[:, next_m], "season_source": season_src.reindex(skus).to_numpy(),
        "growth_yoy": growth, "trend_applied": trend, "manual_growth": manual - 1,
        "lead_days": lead, "horizon_days": horizon, "demand_horizon": d_h, "safety_stock": safety,
        "on_hand": on_hand, "in_transit_horizon": in_tr_h, "in_transit_total": in_tr_all,
        "need": need, "moq": moq, "rec_qty": rec, "urgency": urgency, "cover_days": cover, "risk": risk,
        "oneoff_excluded": oneoff_sum, "oneoff_docs": oneoff_n, "lost_demand_12m": lost12,
        "stockout_months_12m": so_months, "made_to_order": never_stocked,
    })
    # Излишки: запас сверх максимума политики (прогноз на горизонт + страховой запас) — «замороженные» деньги
    max_stock = d_h + safety
    orders["excess_units"] = np.maximum(on_hand + in_tr_all - max_stock, 0)
    orders["excess_months"] = np.where(level > 1e-9, (on_hand + in_tr_all) / np.maximum(level, 1e-9), np.inf)
    # ABC по годовому регулярному спросу в штуках внутри поставщика (A — 80% объёма, B — следующие 15%, C — 5%)
    annual = corrected[:, -12:].sum(axis=1)
    orders["annual_demand"] = annual
    orders["abc"] = "C"
    for sname, g in orders.groupby("supplier"):
        share = g.annual_demand.sort_values(ascending=False).cumsum() / max(g.annual_demand.sum(), 1e-9)
        orders.loc[share.index, "abc"] = np.where(share <= 0.8, "A", np.where(share <= 0.95, "B", "C"))
    orders.loc[orders.annual_demand <= 0, "abc"] = "—"
    orders["reason"] = [_explain(r, p) for r in orders.itertuples()]

    monthly = pd.DataFrame({
        "sku": np.repeat(skus, n), "month": np.tile(months, len(skus)),
        "raw": raw.to_numpy().ravel(), "clean": C.ravel(), "corrected": corrected.ravel(),
        "avail": A.ravel(), "season": sidx.ravel(),
    })
    fut = pd.date_range(cur_m, periods=6, freq="MS")
    fc = (level[:, None] * manual[:, None] * season.to_numpy()[:, fut.month - 1]
          * (1 + trend[:, None]) ** ((np.arange(6) + 0.5) / 12)[None, :])
    forecast = pd.DataFrame({"sku": np.repeat(skus, 6), "month": np.tile(fut, len(skus)), "forecast": fc.ravel()})
    return Result(orders, monthly, forecast, oneoffs, p)


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def _explain(r, p: Params) -> str:
    parts = [f"Прогноз на {int(r.horizon_days)} дн. (поставка {int(r.lead_days)} + цикл {p.review_days}): "
             f"{_fmt(r.demand_horizon)} шт; базовый спрос {_fmt(r.level_month)}/мес"]
    if abs(r.season_next - 1) >= 0.1:
        parts.append(f"сезонность след. месяца ×{r.season_next:.2f} ({r.season_source})")
    if r.trend_applied:
        parts.append(f"устойчивый {'рост' if r.growth_yoy > 0 else 'спад'} {r.growth_yoy:+.0%} г/г (учтено ×{1 + r.trend_applied:.2f}/год)")
    if r.manual_growth:
        parts.append(f"план прироста {r.manual_growth:+.0%}")
    parts.append(f"страх. запас {_fmt(r.safety_stock)}")
    parts.append(f"− остаток {_fmt(r.on_hand)} − в пути {_fmt(r.in_transit_horizon)}")
    if r.rec_qty > 0:
        parts.append(f"= {_fmt(r.need)} → {_fmt(r.rec_qty)} (кратность {_fmt(r.moq)})")
    else:
        parts.append("= заказ не нужен")
    if r.made_to_order:
        parts.append("товар ни разу не был на складе на начало месяца — вероятно, под заказ; упущенный спрос не оценивается")
    if r.lost_demand_12m >= 1:
        parts.append(f"добавлен упущенный спрос {_fmt(r.lost_demand_12m)} шт за {r.stockout_months_12m} мес. дефицита")
    if r.oneoff_excluded >= 1:
        parts.append(f"исключено разовых заказов: {r.oneoff_docs} ({_fmt(r.oneoff_excluded)} шт)")
    if r.urgency == "Критично":
        parts.append(f"остатка хватит на ~{r.cover_days:.0f} дн. < срока поставки")
    return "; ".join(parts)
