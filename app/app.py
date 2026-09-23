"""Автозаказ ЕКТ — рабочее место менеджера закупа. Запуск: ./run.sh (или streamlit run app/app.py)."""
from __future__ import annotations

import html
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
from engine.loaders import load_all, load_workbook_upload, template_bytes  # noqa: E402

st.set_page_config(page_title="Автозаказ ЕКТ", page_icon=":material/local_shipping:", layout="wide")
sys.path.insert(0, str(ROOT / "app"))
from ui import hero, inject_css, kpis  # noqa: E402
inject_css()
APPROVED = ROOT / "data" / "approved"


@st.cache_data(show_spinner="Загружаю данные…")
def get_data(upload: bytes | None = None):
    if upload:
        return load_workbook_upload(upload)
    return load_all()


@st.cache_data(show_spinner="Считаю потребность по всем артикулам…")
def run_calc(lead_iek: int, lead_se: int, review: int, z: float, g_iek: float, g_se: float,
             upload: bytes | None = None):
    d = get_data(upload)
    p = Params(lead_time_days={"IEK": lead_iek, "Systeme Electric": lead_se}, review_days=review,
               service_z=z, growth_pct={"IEK": g_iek, "Systeme Electric": g_se},
               default_lead_time=lead_iek)
    return compute_orders(**d, params=p)


def fmt(x) -> str:
    return f"{x:,.0f}".replace(",", " ")


def to_1c_xlsx(df: pd.DataFrame, approval: dict | None = None) -> bytes:
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
        if approval:
            pd.DataFrame([approval]).to_excel(w, sheet_name="Утверждение", index=False)
    return buf.getvalue()


# ------------------------------------------------------------------ параметры (кнопка вместо боковой панели)
top_l, top_r = st.columns([5, 1.3])
with top_r.popover("Данные и параметры", icon=":material/tune:", use_container_width=True):
    src = st.radio("Источник данных", ["Выгрузки 1С ЕКТ (кейс)", "Загрузить свой файл"],
                   help="Свой файл — один Excel с листами sales, stock_now, transit, items, stock_hist")
    upload = None
    if src == "Загрузить свой файл":
        st.download_button("Скачать шаблон (пример)", template_bytes(), file_name="avtozakaz_template.xlsx",
                           icon=":material/download:")
        f = st.file_uploader("Ваш Excel по шаблону", type=["xlsx"])
        upload = f.getvalue() if f else None
    st.caption("Срок поставки, дни (для своих данных — единый срок = значение IEK)")
    lead_iek = st.number_input("IEK", 5, 120, 40, 5)
    lead_se = st.number_input("Systeme Electric", 5, 120, 30, 5)
    review = st.slider("Период пересмотра заказа, дн.", 7, 60, 30, 1,
                       help="Как часто вы формируете заказ. Заказ покрывает срок поставки + этот период.")
    svc = st.select_slider("Уровень сервиса", ["90%", "95%", "98%"], "95%",
                           help="Выше — меньше дефицитов, но больше запас")
    z = {"90%": 1.28, "95%": 1.65, "98%": 2.05}[svc]
    st.caption("План прироста продаж, %")
    g_iek = st.number_input("Прирост IEK, %", -50, 200, 0, 5)
    g_se = st.number_input("Прирост Systeme Electric, %", -50, 200, 0, 5)
top_l.caption("Данные: выгрузки 1С ТОО «Электрокомплект» — продажи по документам, помесячные продажи и остатки, "
              "товары в пути, кратность/MOQ. Клиенты обезличены.")

try:
    res = run_calc(lead_iek, lead_se, review, z, g_iek, g_se, upload)
except Exception as e:  # понятная ошибка вместо трейсбека
    st.error(f"Не удалось прочитать данные: {e}. Проверьте файл по шаблону.")
    st.stop()
orders = res.orders.merge(get_data(upload)["items"][["sku", "unit_cost"]], on="sku", how="left")
orders["order_value"] = orders.rec_qty * orders.unit_cost.fillna(0)
to_order = orders[orders.rec_qty > 0]
_m = res.monthly.sort_values("month")
spark = _m[_m.month >= _m.month.max() - pd.DateOffset(months=11)].groupby("sku")["corrected"].apply(
    lambda x: [round(v) for v in x])

# ------------------------------------------------------------------ первый экран
hero("Заказ поставщикам — за секунды, а не полдня в Excel",
     "Рекомендации по каждому артикулу с обоснованием: сезонность, рост, товары в пути, упущенный спрос. "
     "Разовые крупные продажи не раздувают регулярную закупку.",
     "Автозаказ ЕКТ · IEK · Systeme Electric")
crit = int((to_order.urgency == "Критично").sum())
kpis([
    {"icon": "alert", "label": "Под угрозой дефицита", "value": fmt(crit), "kind": "danger",
     "sub": f"{crit / max(len(to_order), 1):.0%} позиций к заказу · не хватит до прихода поставки"},
    {"icon": "boxes", "label": "Позиций к заказу", "value": fmt(len(to_order)),
     "sub": f"из {fmt(len(orders))} артикулов · {len(to_order) / max(len(orders), 1):.0%}"},
    {"icon": "filter", "label": "Разовых всплесков исключено", "value": fmt(len(res.oneoffs)),
     "sub": f"{fmt(res.oneoffs.excluded.sum())} шт не попали в регулярную потребность"},
    {"icon": "restore", "label": "Упущенный спрос восстановлен", "value": fmt(orders.lost_demand_12m.sum()),
     "sub": "шт за 12 мес., когда товара не было на складе"},
])
st.caption(f"Расчёт на {res.params.asof:%d.%m.%Y}. Параметры — кнопка «Параметры расчёта» справа вверху.")

tab_order, tab_excess, tab_ai, tab_bt, tab_item, tab_oneoff = st.tabs(
    [":material/receipt_long: Заказ поставщикам", ":material/inventory_2: Излишки",
     ":material/smart_toy: AI-ассистент", ":material/history: Машина времени",
     ":material/query_stats: Разбор артикула", ":material/block: Разовые заказы"])

# ------------------------------------------------------------------ излишки
with tab_excess:
    ex = orders[orders.excess_units >= 1].copy()
    ex["excess_value"] = ex.excess_units * ex.unit_cost.fillna(0)
    ex = ex.sort_values(["excess_value", "excess_units"], ascending=False)
    e1, e2, e3 = st.columns(3)
    e1.metric("Позиций с излишком", fmt(len(ex)))
    e2.metric("Излишек, шт", fmt(ex.excess_units.sum()))
    e3.metric("Заморожено, ₸ (SE, по себестоимости)", fmt(ex.excess_value.sum()))
    st.caption("Излишек = остаток + товары в пути − (прогноз на горизонт заказа + страховой запас). "
               "По этим позициям заказ не нужен; их стоит распродать или перераспределить.")
    st.dataframe(pd.DataFrame({
        "Поставщик": ex.supplier, "Код 1С": ex.sku, "Наименование": ex.name, "ABC": ex.abc,
        "Остаток": ex.on_hand.round(), "В пути": ex.in_transit_total.round(),
        "Излишек, шт": ex.excess_units.round(), "Запас, мес.": ex.excess_months.clip(upper=999).round(1),
        "Излишек, ₸": ex.excess_value.round()}),
        hide_index=True, use_container_width=True, height=520,
        column_config={"Излишек, ₸": st.column_config.NumberColumn(format="localized")})

# ------------------------------------------------------------------ AI-ассистент
with tab_ai:
    from engine.agent import ProcurementAgent
    agent = ProcurementAgent(orders, res.oneoffs)
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
    if st.button("Подготовить черновик письма", icon=":material/mail:"):
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
    acc_path = ROOT / "data" / "results" / "accuracy.json"
    if acc_path.exists():
        import json
        acc = json.loads(acc_path.read_text(encoding="utf-8"))
        st.markdown("#### Точность прогноза на истории")
        st.caption("Отсечки 01.04, 01.05, 01.06.2026 → прогноз на 3 месяца вперёд; только месяцы, когда товар был в "
                   "наличии. WAPE — суммарная ошибка в % от спроса (меньше — лучше). База — «как в Excel»: "
                   "среднее за 12 месяцев.")
        st.dataframe(pd.DataFrame([{"Поставщик": k, "WAPE сервиса": f"{v['wape_ours']:.1%}",
                                    "WAPE «среднее 12 мес.»": f"{v['wape_naive_12m_avg']:.1%}",
                                    "Точек": v["points"]} for k, v in acc.items()]), hide_index=True)
        st.caption("Вывод: на уровне отдельного артикула помесячный шум сильнее сезонности, поэтому сезонность "
                   "применяется только при подтверждённой повторяемости. Основной эффект сервиса — в политике "
                   "заказа (разовые заказы, упущенный спрос, товары в пути, страховой запас, кратность) — см. выше.")

# ------------------------------------------------------------------ заказ
with tab_order:
    mode = st.segmented_control("Режим", ["Требует решения сейчас", "Все позиции к заказу"],
                                default="Требует решения сейчас", label_visibility="collapsed")
    c1, c2, c3 = st.columns([2, 2, 3])
    sups = c1.multiselect("Поставщик", sorted(orders.supplier.unique()), default=sorted(orders.supplier.unique()))
    all_urg = ["Критично", "Высокая", "Плановая"]
    urg = c2.multiselect("Срочность", all_urg,
                         default=["Критично"] if mode == "Требует решения сейчас" else all_urg)
    q = c3.text_input("Поиск по наименованию / коду / категории", placeholder="например: УЗО, 030200192_, Рамка")
    view = to_order[to_order.supplier.isin(sups) & to_order.urgency.isin(urg)]
    if q:
        m = (view.name.str.contains(q, case=False, na=False, regex=False)
             | view.sku.str.contains(q, case=False, na=False, regex=False)
             | view.category.str.contains(q, case=False, na=False, regex=False)
             | view.article.str.contains(q, case=False, na=False, regex=False))
        view = view[m]
    urank = {"Критично": 0, "Высокая": 1, "Плановая": 2}
    view = view.assign(_u=view.urgency.map(urank)).sort_values(["_u", "risk", "demand_horizon"],
                                                               ascending=[True, False, False])
    if mode == "Требует решения сейчас":
        st.caption("Очередь решений: позиции, где остатка и товаров в пути не хватит до прихода новой поставки. "
                   "Разберите их — остальное можно утвердить пакетом в режиме «Все позиции».")

    edited_all = []
    for sup in sorted(view.supplier.unique()):
        v = view[view.supplier == sup]
        val = v.order_value.sum()
        st.markdown(
            f'<div class="supcard"><b>{html.escape(str(sup))}</b><span>{len(v)} позиций</span><span>{fmt(v.rec_qty.sum())} шт</span>'
            + (f'<span>≈ {fmt(val)} ₸ по себестоимости</span>' if val > 0 else '')
            + f'<span class="crit">критичных: {(v.urgency == "Критично").sum()}</span></div>', unsafe_allow_html=True)
        ver = st.session_state.get(f"ver_{sup}", 0)
        default_on = st.session_state.get(f"on_{sup}", True)
        bb1, bb2, _ = st.columns([1, 1, 4])
        if bb1.button("Отметить все", key=f"all_{sup}", icon=":material/done_all:"):
            st.session_state[f"on_{sup}"], st.session_state[f"ver_{sup}"] = True, ver + 1
            st.rerun()
        if bb2.button("Снять все", key=f"none_{sup}", icon=":material/remove_done:"):
            st.session_state[f"on_{sup}"], st.session_state[f"ver_{sup}"] = False, ver + 1
            st.rerun()
        tbl = pd.DataFrame({
            "Утвердить": default_on, "Срочность": v.urgency.values, "Код 1С": v.sku.values,
            "Наименование": v.name.values, "Спрос 12 мес": v.sku.map(spark).values,
            "Риск дефицита": v.risk.values, "Остаток": v.on_hand.round().values,
            "В пути": v.in_transit_total.round().values, "Рекомендовано": v.rec_qty.values,
            "К заказу": v.rec_qty.values, "ABC": v.abc.values, "Артикул": v.article.values, "Категория": v.category.values,
            "Обоснование": v.reason.values,
        })
        ed = st.data_editor(
            tbl, key=f"ed_{sup}_{ver}_{mode}", hide_index=True, use_container_width=True,
            height=min(420, 38 + 35 * len(tbl)),
            disabled=[c for c in tbl.columns if c not in ("Утвердить", "К заказу")],
            column_config={
                "Спрос 12 мес": st.column_config.LineChartColumn(width="small",
                                                                 help="Регулярный спрос по месяцам (без разовых)"),
                "Риск дефицита": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent",
                                                                 width="small"),
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
        b1.download_button("Выгрузить заказ для 1С (Excel)", to_1c_xlsx(final), icon=":material/download:",
                           file_name=f"zakaz_postavshikam_{datetime.now():%Y%m%d_%H%M}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        who = b2.text_input("Кто утверждает (ФИО)", key="who")
        if b2.button("Утвердить заказ", icon=":material/task_alt:", type="primary", disabled=not who):
            APPROVED.mkdir(parents=True, exist_ok=True)
            path = APPROVED / f"approved_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
            path.write_bytes(to_1c_xlsx(final, {"Утвердил": who, "Дата и время": f"{datetime.now():%d.%m.%Y %H:%M:%S}",
                                                "Позиций": len(final), "Всего шт": float(final.final_qty.sum())}))
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
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", y=-0.12, x=0), plot_bgcolor="#fff")
        st.caption("Столбцы — факт продаж; синяя линия — регулярный спрос (без разовых, с упущенным); "
                   "оранжевая — прогноз. Розовым подсвечены месяцы без товара на складе.")
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
