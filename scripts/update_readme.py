"""Подставить в README актуальные цифры из data/results/*.json (чтобы README всегда совпадал с расчётом).
Запуск: .venv/bin/python -m scripts.update_readme"""
import json
import re
from pathlib import Path


def pct(new, old):
    return f"{(new - old) / old:+.0%}".replace("+", "+").replace("-", "−") if old else "—"


def sp(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def main():
    r95 = json.loads(Path("data/results/backtest_1.65.json").read_text(encoding="utf-8"))
    r90 = json.loads(Path("data/results/backtest_1.28.json").read_text(encoding="utf-8"))
    acc = json.loads(Path("data/results/accuracy.json").read_text(encoding="utf-8"))
    lines = ["| Поставщик | Активных артикулов | Случаи «нет на складе» (артикул×месяц): было → с сервисом | Средний запас |",
             "|---|---|---|---|"]
    for a, b in zip(r95["by_supplier"], r90["by_supplier"]):
        so = (f"{a['actual_stockout_months']} → **{a['ours_stockout_months']} "
              f"({pct(a['ours_stockout_months'], a['actual_stockout_months'])})** при сервисе 95% · "
              f"{b['ours_stockout_months']} ({pct(b['ours_stockout_months'], b['actual_stockout_months'])}) при 90%")
        if a.get("cost_coverage_skus"):
            stock = (f"{pct(a['ours_avg_stock_kzt'], a['actual_avg_stock_kzt'])} ₸ по себестоимости (95%) · "
                     f"{pct(b['ours_avg_stock_kzt'], b['actual_avg_stock_kzt'])} ₸ (90%)")
        else:
            stock = (f"{pct(a['ours_avg_stock_units'], a['actual_avg_stock_units'])} шт (95%) · "
                     f"{pct(b['ours_avg_stock_units'], b['actual_avg_stock_units'])} шт (90%)")
        lines.append(f"| {a['supplier']} | {a['skus']} | {so} | {stock} |")
    lines += ["", f"Из сравнения исключены {r95.get('made_to_order_excluded', 0)} артикулов «под заказ» (ни разу не были "
              "на складе на начало месяца — склад их держать не должен). Воспроизведение: `python -m scripts.backtest 1.65` "
              "и `python -m scripts.backtest 1.28` → `data/results/*.json`.", "",
              "Неудовлетворённый спрос в штуках (для прозрачности; для «как было» виден только в месяцы с нулём на начало, "
              "поэтому занижен и несопоставим с симуляцией): " + "; ".join(
                  f"{x['supplier']}: было ≥{sp(x['actual_unmet_units'])}, с сервисом {sp(x['ours_unmet_units'])} шт"
                  for x in r95["by_supplier"]) + ".", "",
              "**Точность прогноза на истории** (`python -m scripts.accuracy`: отсечки 01.04–01.06.2026, горизонт 3 мес., "
              "только месяцы с товаром в наличии, WAPE — меньше лучше): " + "; ".join(
                  f"{k} — {v['wape_ours']:.1%} против {v['wape_naive_12m_avg']:.1%} у «среднего за 12 мес.»"
                  for k, v in acc.items()) + ". Помесячный спрос по артикулу сильно зашумлён, поэтому сезонность "
              "применяется только при подтверждённой повторяемости (параметры подобраны по этой проверке); основной "
              "эффект даёт политика заказа — см. таблицу выше."]
    # «Кривая выбора»: уровни сервиса, где сервис лучше факта сразу по дефицитам и запасу
    z2l = {0.84: "80%", 1.28: "90%", 1.65: "95%", 2.05: "98%", 2.33: "99%"}
    runs = sorted((json.loads(f.read_text(encoding="utf-8")) for f in Path("data/results").glob("backtest_*.json")),
                  key=lambda j: j["service_z"])
    curve = []
    for sup in [x["supplier"] for x in r95["by_supplier"]]:
        for j in runs:
            x = next(r for r in j["by_supplier"] if r["supplier"] == sup)
            key = "kzt" if x.get("cost_coverage_skus") else "units"
            a, o = x[f"actual_avg_stock_{key}"], x[f"ours_avg_stock_{key}"]
            if x["ours_stockout_months"] < x["actual_stockout_months"] and o < a:
                curve.append(f"{sup} — при сервисе {z2l.get(j['service_z'], j['service_z'])}: дефицитов "
                             f"{pct(x['ours_stockout_months'], x['actual_stockout_months'])} и запаса {pct(o, a)} "
                             f"{'₸' if key == 'kzt' else 'шт'}")
                break
    if curve:
        lines += ["", "**Кривая выбора** (вкладка «Машина времени», прогоны при сервисе 80/90/95/98/99%): есть уровни сервиса, "
                  "где сервис лучше факта **сразу по обоим показателям** — " + "; ".join(curve) + "."]
    p = Path("README.md")
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"<!-- RESULTS:START -->.*?<!-- RESULTS:END -->",
               "<!-- RESULTS:START -->\n" + "\n".join(lines) + "\n<!-- RESULTS:END -->", s, flags=re.S)
    p.write_text(s, encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
