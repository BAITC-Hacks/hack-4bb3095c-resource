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

tab_order, tab_ai, tab_bt, tab_item, tab_oneoff = st.tabs(
    ["🧾 Заказ поставщикам", "🤖 AI-ассистент закупщика", "⏪ Машина времени", "🔍 Разбор артикула", "🚫 Разовые заказы"])

# ------------------------------------------------------------------ AI-ассистент
with tab_ai:
    from engine.agent import ProcurementAgent
    agent = ProcurementAgent(to_order, res.oneoffs)
    st.markdown("Спросите о заказе обычным языком — ассистент отвечает **только по данным расчёта** "
                "(инструменты: поиск позиции, разбор позиции, топ по риску, сводка по поставщику). "
                "Заказы он **не отправляет** — только готовит черновики.")
    ex = st.columns(3)
    presets = ["Что критично по IEK прямо сейчас?", "Почему по установочной коробке 65х45 такое количество?",
               "Где мы исключили разовые крупные заказы и сколько?"]
    for col, text in zip(ex, presets):
        if col.button(text, use_container_width=True):
            st.session_state["q"] = text
    q_ai = st.text_input("Ваш вопрос", key="q")
    if q_ai:
        with st.spinner("Ассистент смотрит данные…"):
            st.markdown(agent.ask(q_ai))
    st.divider()
    sup_l = st.selectbox("Черновик письма-заказа поставщику", sorted(to_order.supplier.unique()))
    if st.button("✉️ Подготовить черновик письма"):
        items_l = to_order[to_order.supplier == sup_l].sort_values("risk", ascending=False).head(40)
        with st.spinner("Готовлю черновик…"):
            st.text_area("Черновик (требует утверждения, автоматически не отправляется)",
                         agent.draft_supplier_letter(sup_l, items_l), height=360)

# ------------------------------------------------------------------ машина времени
with tab_bt:
    st.markdown("### Что было бы, если бы с марта 2026 заказы считал наш сервис?")
    st.caption("Честный бэктест на реальных данных 1С: старт с фактических остатков на 01.03.2026; каждое 1-е число "
               "сервис считает заказ, видя только прошлое; заказ приходит через срок поставки; заказы, сделанные "
               "компанией до старта, приходят как в реальности. Спрос — регулярный (без разовых крупных заказов, "
               "с учётом упущенного). «Как было» — фактические остатки из 1С за те же месяцы.")
    lvl = st.radio("Уровень сервиса в симуляции", ["95%", "90%"], horizontal=True,
                   help="Выше сервис — меньше дефицитов, но больше запас. Это рычаг менеджера.")
    bt_path = ROOT / "data" / "results" / f"backtest_{'1.65' if lvl == '95%' else '1.28'}.json"
    if bt_path.exists():
        import json
        bt = json.loads(bt_path.read_text(encoding="utf-8"))
        for row in bt["by_supplier"]:
            st.markdown(f"#### {row['supplier']} — {fmt(row['skus'])} активных артикулов, март–август 2026")
            a, c = st.columns(2)
            so_a, so_o = row["actual_stockout_months"], row["ours_stockout_months"]
            a.metric("Случаи «товара нет на складе» (артикул × месяц)", fmt(so_o),
                     f"{(so_o - so_a) / max(so_a, 1):+.0%} (было {fmt(so_a)})", delta_color="inverse")
            if row.get("cost_coverage_skus"):
                st_a, st_o = row["actual_avg_stock_kzt"], row["ours_avg_stock_kzt"]
                c.metric("Средний запас, ₸ по себестоимости", fmt(st_o),
                         f"{(st_o - st_a) / max(st_a, 1):+.0%} (было {fmt(st_a)})", delta_color="inverse")
            else:
                st_a, st_o = row["actual_avg_stock_units"], row["ours_avg_stock_units"]
                c.metric("Средний запас, шт", fmt(st_o),
                         f"{(st_o - st_a) / max(st_a, 1):+.0%} (было {fmt(st_a)})", delta_color="inverse")
        st.caption("Метрика дефицита одинакова для обеих сторон: начальный остаток месяца = 0 при наличии спроса. "
                   "Упрощения: месячный шаг; товар, пришедший до 15-го, доступен для продаж месяца; себестоимость "
                   "есть только у Systeme Electric (IEK — в штуках). Пересчёт: `python -m scripts.backtest 1.65`.")
    else:
        st.info("Бэктест не рассчитан: `python -m scripts.backtest 1.65` и `python -m scripts.backtest 1.28`.")

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
