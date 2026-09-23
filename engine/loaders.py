"""Загрузка выгрузок 1С ТОО «Электрокомплект» (IEK, Systeme Electric) в единую схему движка.

Единая схема (всё, что нужно engine.core.compute_orders):
  sales      : date, sku, qty (>0 продажа, <0 возврат), doc, warehouse, client (опц.)
  stock_hist : sku, month (Timestamp, 1-е число), begin_stock        -> периоды stockout
  stock_now  : sku, on_hand                                           -> текущий остаток
  transit    : sku, qty, eta (Timestamp)                              -> товары в пути
  items      : sku, name, article, supplier, category, moq
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"

RU_MONTHS = {
    "янв": 1, "февр": 2, "фев": 2, "март": 3, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июнь": 6, "июн": 6,
    "июль": 7, "июл": 7, "авг": 8, "сент": 9, "сен": 9, "окт": 10, "нояб": 11, "ноя": 11, "дек": 12,
}
SUPPLIERS = {"iek": "IEK", "se": "Systeme Electric"}


def _month_from_header(h) -> pd.Timestamp | None:
    """'янв. 2024' / 'Январь 2024 г.' -> Timestamp(2024-01-01)."""
    if not isinstance(h, str):
        return None
    m = re.match(r"\s*([А-Яа-я]+)\.?\s+(\d{4})", h)
    if not m:
        return None
    word = m.group(1).lower()
    for k, v in sorted(RU_MONTHS.items(), key=lambda kv: -len(kv[0])):
        if word.startswith(k):
            return pd.Timestamp(int(m.group(2)), v, 1)
    return None


def _code(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip()
    return s or None


def _read(name: str, **kw) -> pd.DataFrame:
    return pd.read_excel(RAW / name, header=None, **kw)


def load_sales(tag: str) -> pd.DataFrame:
    df = pd.read_excel(RAW / f"{tag}_sales_dynamics.xlsx")
    df.columns = ["date", "num", "document", "sku", "name", "unit", "warehouse", "qty"]
    df["date"] = pd.to_datetime(df["date"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    df["sku"] = df["sku"].map(_code)
    # Отгрузки — «Расходная накладная», количество > 0; редкие отрицательные строки — корректировки
    # (учитываются как возврат). «Заказ покупателя» — не отгрузка, исключаем; строку «Итого» отбрасываем.
    df = df[df["document"].astype(str).str.startswith("Расходная накладная")]
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["doc"] = df["num"].astype(str)
    df = df.dropna(subset=["date", "sku"])
    return df[["date", "sku", "qty", "doc", "warehouse", "name"]]


def load_stock_hist(tag: str) -> pd.DataFrame:
    raw = _read(f"{tag}_monthly_stock.xlsx")
    header = raw.iloc[0].tolist()
    code_col = header.index("Номенклатура.Код")
    month_cols = {i: _month_from_header(h) for i, h in enumerate(header)}
    month_cols = {i: m for i, m in month_cols.items() if m is not None}
    body = raw.iloc[3:]
    recs = []
    for _, row in body.iterrows():
        sku = _code(row.iloc[code_col])
        if not sku:
            continue
        for i, m in month_cols.items():
            v = row.iloc[i]
            recs.append((sku, m, 0.0 if pd.isna(v) else float(v)))
    return pd.DataFrame(recs, columns=["sku", "month", "begin_stock"])


def load_moq(tag: str) -> pd.DataFrame:
    raw = _read(f"{tag}_moq.xlsx")
    header = [str(h).strip() if h is not None else "" for h in raw.iloc[0].tolist()]
    if tag == "iek":
        code_i, art_i, name_i, moq_i = header.index("Код 1с"), header.index("Артикул поставщика"), header.index("Наименование"), 4
    else:
        code_i, art_i, name_i, moq_i = header.index("Номенклатура.Код"), header.index("Артикул"), header.index("Номенклатура"), header.index("Кратность")
    body = raw.iloc[1:]
    out = pd.DataFrame({
        "sku": body.iloc[:, code_i].map(_code),
        "article": body.iloc[:, art_i].astype(str).str.strip(),
        "name": body.iloc[:, name_i].astype(str).str.strip(),
        "moq": pd.to_numeric(body.iloc[:, moq_i], errors="coerce"),
    }).dropna(subset=["sku"])
    out["moq"] = out["moq"].fillna(1).clip(lower=1)
    return out.drop_duplicates("sku")


def load_transit(tag: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (transit[sku, qty, eta], extra[sku, category, on_hand_free]) ; extra только у SE."""
    if tag == "iek":
        raw = _read("iek_in_transit.xlsx")
        header = raw.iloc[0].tolist()
        recs = []
        for i, h in enumerate(header):
            m = re.search(r"поступление до (\d{2}\.\d{2}\.\d{4})", str(h))
            if not m:
                continue
            eta = pd.to_datetime(m.group(1), format="%d.%m.%Y")
            for _, row in raw.iloc[1:].iterrows():
                q = pd.to_numeric(row.iloc[i], errors="coerce")
                if pd.notna(q) and q > 0:
                    recs.append((_code(row.iloc[0]), float(q), eta))
        return pd.DataFrame(recs, columns=["sku", "qty", "eta"]).dropna(subset=["sku"]), pd.DataFrame(
            columns=["sku", "category", "on_hand_free"])
    raw = _read("se_in_transit.xlsx")
    header = [str(h).strip() if h is not None else "" for h in raw.iloc[1].tolist()]
    body = raw.iloc[2:]
    code_i = header.index("Код 1с")
    transit_i = next(i for i, h in enumerate(header) if h.startswith("СЭ в пути"))
    m = re.search(r"(\d{2})\.(\d{2})", header[transit_i])
    eta = pd.Timestamp(2026, int(m.group(2)), int(m.group(1))) if m else pd.Timestamp("2026-09-24")
    sku = body.iloc[:, code_i].map(_code)
    q = pd.to_numeric(body.iloc[:, transit_i], errors="coerce").fillna(0)
    transit = pd.DataFrame({"sku": sku, "qty": q, "eta": eta})
    transit = transit[(transit.qty > 0) & transit.sku.notna()]
    extra = pd.DataFrame({
        "sku": sku,
        "category": "Кат. " + body.iloc[:, header.index("Категория 2026")].astype(str).str.strip(),
        "on_hand_free": pd.to_numeric(body.iloc[:, header.index("Свободный остаток")], errors="coerce"),
    }).dropna(subset=["sku"]).drop_duplicates("sku")
    return transit, extra


def load_supplier(tag: str) -> dict[str, pd.DataFrame]:
    sales = load_sales(tag)
    stock_hist = load_stock_hist(tag)
    moq = load_moq(tag)
    transit, extra = load_transit(tag)

    names = sales.groupby("sku")["name"].last()
    skus = sorted(set(sales.sku) | set(moq.sku) | set(stock_hist.sku))
    items = pd.DataFrame({"sku": skus})
    items = items.merge(moq, on="sku", how="left")
    items["name"] = items["name"].fillna(items["sku"].map(names)).fillna(items["sku"])
    items["article"] = items["article"].fillna("")
    items["moq"] = items["moq"].fillna(1)
    items["supplier"] = SUPPLIERS[tag]
    items = items.merge(extra[["sku", "category"]], on="sku", how="left")
    items["category"] = items["category"].fillna(items["name"].map(_category_from_name))

    # Текущий остаток: у SE есть «Свободный остаток» из рабочей таблицы; иначе — последний
    # начальный остаток месяца минус продажи с начала этого месяца (оценка, см. README).
    last_m = stock_hist["month"].max()
    base = stock_hist[stock_hist.month == last_m].set_index("sku")["begin_stock"]
    sold = sales[sales.date >= last_m].groupby("sku")["qty"].sum()
    est = (base.sub(sold, fill_value=0)).clip(lower=0)
    on_hand = est.rename("on_hand").reset_index()
    if not extra.empty:
        free = extra.dropna(subset=["on_hand_free"]).set_index("sku")["on_hand_free"]
        on_hand = on_hand.set_index("sku")
        on_hand.loc[free.index.intersection(on_hand.index), "on_hand"] = free
        on_hand = on_hand.reset_index()
    return {"sales": sales.drop(columns=["name"]), "stock_hist": stock_hist, "stock_now": on_hand,
            "transit": transit, "items": items}


def _category_from_name(name: str) -> str:
    """Для IEK категории нет в выгрузке — берём тип изделия по первому слову наименования."""
    w = re.sub(r"[^А-Яа-яA-Za-z]", " ", str(name)).split()
    return (w[0].capitalize() if w else "Прочее")[:24]


def load_all(use_cache: bool = True) -> dict[str, pd.DataFrame]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / "all.pkl"
    if use_cache and cache.exists() and cache.stat().st_mtime > max(p.stat().st_mtime for p in RAW.glob("*.xlsx")):
        return pd.read_pickle(cache)
    parts = [load_supplier(t) for t in SUPPLIERS]
    data = {k: pd.concat([p[k] for p in parts], ignore_index=True) for k in parts[0]}
    pd.to_pickle(data, cache)
    return data
