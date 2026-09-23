"""Оформление: логистическая палитра, SVG-иконки (без эмодзи), анимированная шапка «трасса», KPI-карточки."""
from __future__ import annotations

import streamlit as st

NAVY = "#0B1F3A"
ORANGE = "#F28C28"

# Иконки — простые stroke-SVG в стиле Lucide (24×24), цвет через currentColor
ICONS = {
    "truck": '<path d="M3 7h11v9H3z"/><path d="M14 10h4l3 3v3h-7z"/><circle cx="7" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
    "alert": '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4"/><circle cx="12" cy="17" r=".6"/>',
    "boxes": '<path d="M3 13h8v8H3zM13 13h8v8h-8zM8 3h8v8H8z"/>',
    "filter": '<path d="M3 5h18l-7 8v6l-4-2v-4L3 5z"/>',
    "restore": '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v4h4"/><path d="M12 8v4l3 2"/>',
    "warehouse": '<path d="M3 21V9l9-5 9 5v12"/><path d="M7 21v-8h10v8"/><path d="M7 17h10"/>',
}


def icon(name: str, size: int = 22, color: str = "currentColor") -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stDataFrame {{ font-family: 'Inter', system-ui, sans-serif; }}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none !important; }}
.block-container {{ padding-top: 1rem; max-width: 1480px; }}
@media (max-width: 760px) {{
  .block-container {{ padding-left: .8rem; padding-right: .8rem; }}
  .hero {{ padding: 18px 18px 48px !important; }} .hero h1 {{ font-size: 1.35rem !important; }} .hero p {{ font-size: .9rem !important; }}
  .kpi .val {{ font-size: 1.5rem !important; }}
}}
.supcard {{ background:#F6F8FB; border:1px solid #E3E8F0; border-radius:12px; padding:12px 16px; margin: 6px 0 10px;
  display:flex; flex-wrap:wrap; gap: 8px 28px; align-items:center; }}
.supcard b {{ color:{NAVY}; font-size:1.05rem; }} .supcard span {{ color:#5B6B82; font-size:.88rem; }}
.supcard .crit {{ color:#C62828; font-weight:700; }}
[data-testid="stSidebar"] {{ background: {NAVY}; }}
[data-testid="stSidebar"] * {{ color: #E6ECF5 !important; }}
[data-testid="stSidebar"] input {{ color: {NAVY} !important; }}
.hero {{ position: relative; overflow: hidden; border-radius: 18px; padding: 26px 30px 54px;
  background: linear-gradient(120deg, {NAVY} 0%, #14335F 60%, #1B4478 100%); color: #fff; }}
.hero h1 {{ font-size: 2.05rem; font-weight: 800; margin: 0 0 6px; color: #fff; letter-spacing: -.02em; }}
.hero p {{ font-size: 1.05rem; color: #C9D6EA; margin: 0; max-width: 860px; }}
.hero .tag {{ display:inline-flex; gap:8px; align-items:center; font-size:.8rem; font-weight:600; letter-spacing:.06em;
  text-transform:uppercase; color:{ORANGE}; margin-bottom:10px; }}
.road {{ position:absolute; left:0; right:0; bottom:18px; height:2px;
  background: repeating-linear-gradient(90deg, rgba(255,255,255,.35) 0 18px, transparent 18px 34px);
  animation: dash 1.2s linear infinite; }}
@keyframes dash {{ to {{ background-position: -34px 0; }} }}
.truck {{ position:absolute; bottom:22px; left:-60px; color:{ORANGE}; animation: drive 9s linear infinite; }}
@keyframes drive {{ 0% {{ left:-60px; }} 100% {{ left: calc(100% + 60px); }} }}
.kpis {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:14px; margin: 16px 0 6px; }}
@media (max-width: 900px) {{ .kpis {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} }}
.kpi {{ background:#fff; border:1px solid #E3E8F0; border-radius:14px; padding:16px 18px;
  box-shadow: 0 1px 2px rgba(11,31,58,.05); animation: rise .5s ease both; }}
.kpi:nth-child(2) {{ animation-delay:.07s }} .kpi:nth-child(3) {{ animation-delay:.14s }} .kpi:nth-child(4) {{ animation-delay:.21s }}
@keyframes rise {{ from {{ opacity:0; transform: translateY(8px); }} to {{ opacity:1; transform:none; }} }}
.kpi .top {{ display:flex; align-items:center; gap:8px; color:#5B6B82; font-size:.82rem; font-weight:600; }}
.kpi .val {{ font-size:1.9rem; font-weight:800; color:{NAVY}; margin-top:6px; letter-spacing:-.02em; }}
.kpi .sub {{ font-size:.8rem; color:#7A889C; }}
.kpi.danger .val {{ color:#C62828; }} .kpi.danger .top {{ color:#C62828; }}
.pill {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:.78rem; font-weight:600; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
.stTabs [data-baseweb="tab"] {{ font-weight:600; }}
.stTabs [aria-selected="true"] {{ color:{ORANGE} !important; }}
@media (prefers-reduced-motion: reduce) {{ .truck, .road, .kpi {{ animation: none; }} }}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str, tag: str):
    st.markdown(f"""
<div class="hero">
  <div class="tag">{icon('warehouse', 16, ORANGE)} {tag}</div>
  <h1>{title}</h1>
  <p>{subtitle}</p>
  <div class="road"></div>
  <div class="truck">{icon('truck', 40, ORANGE)}</div>
</div>""", unsafe_allow_html=True)


def kpis(cards: list[dict]):
    html = "".join(
        f'<div class="kpi {c.get("kind", "")}"><div class="top">{icon(c["icon"], 18)} {c["label"]}</div>'
        f'<div class="val">{c["value"]}</div><div class="sub">{c.get("sub", "")}</div></div>'
        for c in cards)
    st.markdown(f'<div class="kpis">{html}</div>', unsafe_allow_html=True)
