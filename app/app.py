"""Автозаказ ЕКТ — рабочее место менеджера закупа. Запуск: ./run.sh (или streamlit run app/app.py)."""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.core import Params, compute_orders  # noqa: E402
from engine.loaders import load_all  # noqa: E402

st.set_page_config(page_title="Автозаказ ЕКТ", page_icon="📦", layout="wide")
APPROVED = ROOT / "data" / "approved"


@st.cache_data(show_spinner="Загружаю выгрузки 1С…")
def get_data():
    return load_all()


@st.cache_data(show_spinner="Считаю потребность по всем артикулам…")
def run_calc(lead_iek: int, lead_se: int, review: int, z: float, g_iek: float, g_se: float):
    d = get_data()
    p = Params(lead_time_days={"IEK": lead_iek, "Systeme Electric": lead_se}, review_days=review,
               service_z=z, growth_pct={"IEK": g_iek, "Systeme Electric": g_se})
    return compute_orders(**d, params=p)


def fmt(x) -> str:
    return f"{x:,.0f}".replace(",", " ")


def to_1c_xlsx(df: pd.DataFrame) -> bytes:
    """Формат, совместимый с загрузкой «Заказ поставщику» в 1С: код номенклатуры, артикул, кол-во."""
    out = df.rename(columns={"sku": "Код 1С", "article": "Артикул поставщика", "name": "Наименование",
                             "final_qty": "Количество", "supplier": "Поставщик", "urgency": "Срочность",
                             "reason": "Обоснование"})
    cols = ["Поставщик", "Код 1С", "Артикул поставщика", "Наименование", "Количество", "Срочность", "Обоснование"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        for sup, g in out.groupby("Поставщик"):
            g[cols].to_excel(w, sheet_name=sup[:31], index=False)
            ws = w.sheets[sup[:31]]
            ws.set_column(0, 1, 14); ws.set_column(2, 2, 22); ws.set_column(3, 3, 60); ws.set_column(6, 6, 120)
    return buf.getvalue()


# ------------------------------------------------------------------ сайдбар: параметры
with st.sidebar:
    st.header("⚙️ Параметры расчёта")
    st.caption("Справочник поставщиков: срок поставки (дни)")
    lead_iek = st.number_input("IEK", 5, 120, 40, 5)
    lead_se = st.number_input("Systeme Electric", 5, 120, 30, 5)
    review = st.slider("Период пересмотра заказа, дн.", 7, 60, 30, 1,
                       help="Как часто вы формируете заказ. Заказ покрывает срок поставки + этот период.")
    svc = st.select_slider("Уровень сервиса", ["90%", "95%", "98%"], "95%")
    z = {"90%": 1.28, "95%": 1.65, "98%": 2.05}[svc]
    st.caption("Прогноз прироста продаж (план отдела продаж), %")
    g_iek = st.number_input("Прирост IEK, %", -50, 200, 0, 5)
    g_se = st.number_input("Прирост Systeme Electric, %", -50, 200, 0, 5)
    st.divider()
    st.caption("Данные: выгрузки 1С ТОО «Электрокомплект» (история продаж по документам, "
               "помесячные продажи и остатки, товары в пути, кратность/MOQ). Клиенты обезличены.")

res = run_calc(lead_iek, lead_se, review, z, g_iek, g_se)
orders = res.orders.copy()
to_order = orders[orders.rec_qty > 0]

# ------------------------------------------------------------------ первый экран
st.title("📦 Автозаказ поставщикам")
st.markdown("#### Заказ по всему складу — за секунды вместо полдня в Excel. "
            "С обоснованием каждой строки, без искажений от разовых крупных продаж.")
k1, k2, k3, k4 = st.columns(4)
k1.metric("🔴 Позиций под угрозой дефицита", fmt((to_order.urgency.str.startswith("🔴")).sum()),
          help="Остатка + товаров в пути не хватит до прихода нового заказа")
k2.metric("Позиций к заказу", fmt(len(to_order)), help=f"из {fmt(len(orders))} артикулов")
k3.metric("Разовых всплесков исключено", fmt(len(res.oneoffs)),
          help=f"{fmt(res.oneoffs.excluded.sum())} шт не попали в регулярную потребность")
k4.metric("Упущенный спрос восстановлен", fmt(orders.lost_demand_12m.sum()) + " шт",
          help="Продажи, которых не было из-за отсутствия товара (stockout) за 12 мес.")
st.caption(f"Расчёт на {res.params.asof:%d.%m.%Y}. Параметры — в панели слева.")

tab_order, tab_item, tab_oneoff = st.tabs(["🧾 Заказ поставщикам", "🔍 Разбор артикула", "🚫 Разовые заказы"])

# ------------------------------------------------------------------ заказ
with tab_order:
    c1, c2, c3 = st.columns([2, 2, 3])
    sups = c1.multiselect("Поставщик", sorted(orders.supplier.unique()), default=sorted(orders.supplier.unique()))
    urg = c2.multiselect("Срочность", ["🔴 Критично", "🟠 Высокая", "🟢 Плановая"],
                         default=["🔴 Критично", "🟠 Высокая", "🟢 Плановая"])
    q = c3.text_input("Поиск по наименованию / коду / категории")
    view = to_order[to_order.supplier.isin(sups) & to_order.urgency.isin(urg)]
    if q:
        m = (view.name.str.contains(q, case=False, na=False) | view.sku.str.contains(q, case=False, na=False)
             | view.category.str.contains(q, case=False, na=False) | view.article.str.contains(q, case=False, na=False))
        view = view[m]
    urank = {"🔴 Критично": 0, "🟠 Высокая": 1, "🟢 Плановая": 2}
    view = view.assign(_u=view.urgency.map(urank)).sort_values(["_u", "risk", "demand_horizon"],
                                                               ascending=[True, False, False])

    edited_all = []
    for sup in sorted(view.supplier.unique()):
        v = view[view.supplier == sup]
        with st.expander(f"**{sup}** — {len(v)} позиций, {fmt(v.rec_qty.sum())} шт, "
                         f"🔴 {(v.urgency.str.startswith('🔴')).sum()}", expanded=True):
            tbl = pd.DataFrame({
                "Утвердить": True, "Срочность": v.urgency.values, "Код 1С": v.sku.values,
                "Артикул": v.article.values, "Наименование": v.name.values, "Категория": v.category.values,
                "Остаток": v.on_hand.round().values, "В пути": v.in_transit_total.round().values,
                "Рекомендовано": v.rec_qty.values, "К заказу": v.rec_qty.values,
                "Обоснование": v.reason.values,
            })
            ed = st.data_editor(
                tbl, key=f"ed_{sup}", hide_index=True, use_container_width=True, height=380,
                disabled=[c for c in tbl.columns if c not in ("Утвердить", "К заказу")],
                column_config={
                    "К заказу": st.column_config.NumberColumn(min_value=0, step=1, help="Можно скорректировать"),
                    "Обоснование": st.column_config.TextColumn(width="large"),
                    "Наименование": st.column_config.TextColumn(width="medium"),
                })
            ed["supplier"] = sup
            edited_all.append(ed)

    if edited_all:
        ed = pd.concat(edited_all)
        chosen = ed[ed["Утвердить"] & (ed["К заказу"] > 0)]
        final = chosen.rename(columns={"Код 1С": "sku", "Артикул": "article", "Наименование": "name",
                                       "К заказу": "final_qty", "Срочность": "urgency", "Обоснование": "reason"})
        changed = (chosen["К заказу"] != chosen["Рекомендовано"]).sum()
        st.info(f"К утверждению: **{len(chosen)}** позиций, **{fmt(chosen['К заказу'].sum())}** шт. "
                f"Скорректировано вручную: {changed}. Заказ **не отправляется** поставщику автоматически — "
                f"только после утверждения ответственным.")
        b1, b2 = st.columns(2)
        b1.download_button("⬇️ Выгрузить заказ для 1С (Excel)", to_1c_xlsx(final),
                           file_name=f"zakaz_postavshikam_{datetime.now():%Y%m%d_%H%M}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        who = b2.text_input("Кто утверждает (ФИО)", key="who")
        if b2.button("✅ Утвердить заказ", type="primary", disabled=not who):
            APPROVED.mkdir(parents=True, exist_ok=True)
            path = APPROVED / f"approved_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
            path.write_bytes(to_1c_xlsx(final))
            st.success(f"Заказ утверждён: {who}, {datetime.now():%d.%m.%Y %H:%M}. Файл: data/approved/{path.name}")

# ------------------------------------------------------------------ артикул
with tab_item:
    opts = to_order.sort_values("demand_horizon", ascending=False)
    label = (opts.sku + " · " + opts.name.str.slice(0, 70)).tolist()
    pick = st.selectbox("Артикул (из рекомендованных к заказу)", label)
    if pick:
        sku = pick.split(" · ")[0]
        r = orders.set_index("sku").loc[sku]
        m = res.monthly[res.monthly.sku == sku]
        f = res.forecast[res.forecast.sku == sku]
        a, b, c, d = st.columns(4)
        a.metric("К заказу", fmt(r.rec_qty), help=f"кратность {fmt(r.moq)}")
        b.metric("Срочность", r.urgency, help=f"покрытие остатком ~{r.cover_days:.0f} дн.")
        c.metric("Базовый спрос / мес", fmt(r.level_month), f"{r.growth_yoy:+.0%} г/г" if r.growth_yoy else None)
        d.metric("Сезонность след. мес.", f"×{r.season_next:.2f}", r.season_source, delta_color="off")
        st.markdown(f"**Обоснование:** {r.reason}")
        fig = go.Figure()
        fig.add_bar(x=m.month, y=m.raw, name="Факт продаж", marker_color="#b8c4d6")
        fig.add_scatter(x=m.month, y=m.corrected, name="Регулярный спрос (без разовых, + упущенный)",
                        mode="lines+markers", line=dict(color="#1f5fbf", width=3))
        fig.add_scatter(x=f.month, y=f.forecast, name="Прогноз", mode="lines+markers",
                        line=dict(color="#e0752d", width=3, dash="dash"))
        for mo in m[m.avail < 0.999].month:
            fig.add_vrect(x0=mo - pd.Timedelta(days=14), x1=mo + pd.Timedelta(days=14),
                          fillcolor="red", opacity=0.08, line_width=0)
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.1),
                          title="Красным — месяцы без товара на складе (stockout)")
        st.plotly_chart(fig, use_container_width=True)
        oo = res.oneoffs[res.oneoffs.sku == sku]
        if len(oo):
            st.markdown("**Исключённые разовые заказы по артикулу**")
            st.dataframe(oo.rename(columns={"doc": "Документ", "date": "Дата", "qty": "Кол-во",
                                            "typical": "Обычный заказ", "excluded": "Исключено", "reason": "Причина"}),
                         hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ разовые
with tab_oneoff:
    st.markdown("Крупные разовые заказы урезаются до «обычного» размера заказа по артикулу и **не влияют** "
                "на регулярную потребность. Повторяющиеся крупные заказы (≥4 месяцев за год) считаются "
                "регулярным оптом и остаются в расчёте.")
    oo = res.oneoffs.merge(orders[["sku", "name", "supplier"]], on="sku", how="left")
    st.dataframe(oo[["supplier", "sku", "name", "doc", "date", "qty", "typical", "excluded", "reason"]].rename(
        columns={"supplier": "Поставщик", "sku": "Код 1С", "name": "Наименование", "doc": "Документ", "date": "Дата",
                 "qty": "Кол-во в документе", "typical": "Обычный заказ", "excluded": "Исключено", "reason": "Причина"}),
        hide_index=True, use_container_width=True, height=520)
