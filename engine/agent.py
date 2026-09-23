"""Procurement assistant with data-bound tools and a deterministic offline mode."""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any

import pandas as pd
from dotenv import load_dotenv


SYSTEM_PROMPT = (
    "Ты — ассистент менеджера закупа ТОО Электрокомплект. Отвечай кратко, по-русски, "
    "только на основе данных инструментов, цифры не выдумывай, ссылайся на код 1С. "
    "Ты НЕ отправляешь заказы — только готовишь; отправка только после утверждения человеком. "
    "Код 1С — поле sku, артикул поставщика — поле article; это разные поля, указывай код 1С (sku). "
    "Если спрашивают «почему столько» — опирайся на поле reason и называй составляющие расчёта."
)

_TOOL_SPECS = [
    {"type": "function", "function": {"name": "find_items", "description": "Найти позиции по названию, SKU, артикулу или категории.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "get_item", "description": "Получить поля позиции и её разовые заказы.",
     "parameters": {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]}}},
    {"type": "function", "function": {"name": "top_items", "description": "Показать позиции с наибольшим значением выбранного показателя.",
     "parameters": {"type": "object", "properties": {"supplier": {"type": ["string", "null"]}, "urgency": {"type": ["string", "null"]}, "by": {"type": "string", "default": "risk"}, "limit": {"type": "integer", "default": 10}}}}},
    {"type": "function", "function": {"name": "supplier_summary", "description": "Сводка потребности и риска по поставщику.",
     "parameters": {"type": "object", "properties": {"supplier": {"type": "string"}}, "required": ["supplier"]}}},
]


def _json(data: Any) -> str:
    """Serialize pandas/numpy values safely as short, compact JSON."""
    if isinstance(data, pd.DataFrame):
        data = data.head(30).to_dict(orient="records")
    elif isinstance(data, pd.Series):
        data = data.to_dict()

    def normalize(value):
        if isinstance(value, pd.DataFrame):
            return normalize(value.head(30).to_dict(orient="records"))
        if isinstance(value, pd.Series):
            return normalize(value.to_dict())
        if isinstance(value, dict):
            return {str(k): normalize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(v) for v in value[:30]]
        if value is None or value is pd.NA:
            return None
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return normalize(value.item())
            except (ValueError, TypeError):
                pass
        return value

    return json.dumps(normalize(data), ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False, sort_keys=False)


class ProcurementAgent:
    def __init__(self, orders: pd.DataFrame, oneoffs: pd.DataFrame):
        self.orders = orders.copy() if isinstance(orders, pd.DataFrame) else pd.DataFrame()
        self.oneoffs = oneoffs.copy() if isinstance(oneoffs, pd.DataFrame) else pd.DataFrame()

    def find_items(self, query: str, limit: int = 10) -> str:
        if self.orders.empty:
            return _json([])
        cols = [c for c in ("name", "sku", "article", "category") if c in self.orders]
        q = str(query or "").casefold().strip()
        if not q:
            return _json([])
        mask = pd.Series(False, index=self.orders.index)
        for col in cols:
            mask |= self.orders[col].fillna("").astype(str).str.casefold().str.contains(re.escape(q), regex=True)
        rows = self.orders[mask]
        return _json(rows.head(min(max(int(limit), 0), 30)))

    def get_item(self, sku: str) -> str:
        rows = self.orders[self.orders.sku.astype(str).str.casefold() == str(sku).casefold()] if "sku" in self.orders else self.orders.iloc[0:0]
        if rows.empty:
            return _json({"error": f"Позиция {sku} не найдена"})
        item = rows.iloc[0].to_dict()
        sku_col = self.oneoffs["sku"].astype(str).str.casefold() == str(sku).casefold() if "sku" in self.oneoffs else pd.Series(False, index=self.oneoffs.index)
        return _json({"item": item, "oneoffs": self.oneoffs[sku_col].head(29)})

    def top_items(self, supplier: str | None = None, urgency: str | None = None,
                  by: str = "risk", limit: int = 10) -> str:
        if self.orders.empty:
            return _json([])
        rows = self.orders.copy()
        if supplier is not None and "supplier" in rows:
            rows = rows[rows.supplier.astype(str).str.casefold() == str(supplier).casefold()]
        if urgency is not None and "urgency" in rows:
            rows = rows[rows.urgency.astype(str).str.casefold().str.contains(re.escape(str(urgency)), regex=True)]
        if by not in rows.columns:
            return _json({"error": f"Неизвестный показатель: {by}"})
        rows = rows.sort_values(by, ascending=False, na_position="last")
        return _json(rows.head(min(max(int(limit), 0), 30)))

    def supplier_summary(self, supplier: str) -> str:
        rows = self.orders[self.orders.supplier.astype(str).str.casefold() == str(supplier).casefold()] if "supplier" in self.orders else self.orders.iloc[0:0]
        to_order = rows[pd.to_numeric(rows.get("rec_qty", 0), errors="coerce").fillna(0) > 0] if not rows.empty else rows
        critical = to_order.urgency.astype(str).str.contains("крит", case=False, na=False).sum() if "urgency" in to_order else 0
        risk_top = to_order.sort_values("risk", ascending=False).head(5) if "risk" in to_order else to_order.head(5)
        return _json({
            "supplier": supplier,
            "positions_to_order": int(len(to_order)),
            "total_qty": float(pd.to_numeric(to_order.get("rec_qty", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
            "critical_positions": int(critical),
            "top_by_risk": risk_top,
        })

    def _fallback(self, question: str) -> str:
        q = str(question or "")
        # Check full codes/names first, then meaningful words from the question.
        match = self.orders.iloc[0:0]
        for col in ("sku", "article", "name"):
            if col not in self.orders:
                continue
            for value in self.orders[col].dropna().astype(str):
                if value and value.casefold() in q.casefold():
                    match = self.orders[self.orders[col].astype(str).str.casefold() == value.casefold()].head(1)
                    break
            if not match.empty:
                break
        if match.empty:
            tokens = [t for t in re.findall(r"[\w.-]+", q, flags=re.UNICODE) if len(t) >= 3]
            for token in tokens:
                for col in ("sku", "article", "name", "category"):
                    if col in self.orders:
                        found = self.orders[self.orders[col].fillna("").astype(str).str.casefold().str.contains(re.escape(token.casefold()), regex=True)]
                        if not found.empty:
                            match = found.head(1)
                            break
                if not match.empty:
                    break
        if not match.empty:
            row = match.iloc[0]
            return f"{row.get('article', row.get('sku', ''))} ({row.get('sku', '')}): {row.get('reason', 'Причина не указана в данных.') }"
        suppliers = self.orders.supplier.dropna().astype(str).drop_duplicates().tolist() if "supplier" in self.orders else []
        if not suppliers:
            return "В данных нет позиций и сводок по поставщикам."
        summaries = []
        for supplier in suppliers[:30]:
            try:
                s = json.loads(self.supplier_summary(supplier))
                summaries.append(f"{supplier}: к заказу {s['positions_to_order']} позиций, {s['total_qty']:g} шт., критичных {s['critical_positions']}.")
            except Exception:
                continue
        return "\n".join(summaries) or "Не удалось сформировать сводку по поставщикам."

    def ask(self, question: str, history: list | None = None) -> str:
        try:
            load_dotenv()
            api_key, model = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL")
            if not api_key or not model:
                return self._fallback(question)
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=30, max_retries=1)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            if history:
                messages.extend(history[-20:])
            messages.append({"role": "user", "content": str(question)})
            for _ in range(5):
                response = client.chat.completions.create(model=model, messages=messages, tools=_TOOL_SPECS, tool_choice="auto")
                message = response.choices[0].message
                calls = getattr(message, "tool_calls", None)
                if not calls:
                    return message.content or self._fallback(question)
                messages.append(message.model_dump(exclude_none=True))
                for call in calls:
                    try:
                        args = json.loads(call.function.arguments or "{}")
                        tool = getattr(self, call.function.name, None)
                        output = tool(**args) if call.function.name in {"find_items", "get_item", "top_items", "supplier_summary"} else _json({"error": "Неизвестный инструмент"})
                    except Exception as exc:
                        output = _json({"error": f"Ошибка инструмента: {type(exc).__name__}"})
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
            return self._fallback(question)
        except Exception:
            return self._fallback(question)

    def draft_supplier_letter(self, supplier: str, items: pd.DataFrame) -> str:
        try:
            rows = items.copy() if isinstance(items, pd.DataFrame) else pd.DataFrame(items)
            if "supplier" in rows:
                rows = rows[rows.supplier.astype(str).str.casefold() == str(supplier).casefold()]
            lines = ["Добрый день!", "", "Просим подготовить к поставке следующие позиции:", "", "| Артикул | Наименование | Количество |", "|---|---|---:|"]
            for row in rows.head(30).to_dict(orient="records"):
                article = row.get("article", row.get("sku", ""))
                name = row.get("name", "")
                qty = row.get("rec_qty", row.get("qty", ""))
                lines.append(f"| {article} | {name} | {qty} |")
            lines.extend(["", "Будем признательны за подтверждение наличия и срока поставки.", "", "С уважением,", "ТОО Электрокомплект", "", "ЧЕРНОВИК — требует утверждения"])
            return "\n".join(lines)
        except Exception:
            return "Добрый день!\n\nЧЕРНОВИК — требует утверждения"
