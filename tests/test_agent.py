import json

import pandas as pd
import pytest

import engine.agent as agent_module
from engine.agent import ProcurementAgent


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    # Keep a developer's private local .env from changing this offline test.
    monkeypatch.setattr(agent_module, "load_dotenv", lambda: None)
    orders = pd.DataFrame([
        {"sku": "SKU-1", "article": "1C-001", "name": "Кабель силовой", "supplier": "S1",
         "category": "Кабель", "rec_qty": 12, "urgency": "Критично", "reason": "Покрытие ниже срока поставки",
         "risk": 0.9, "on_hand": 2, "in_transit_total": 0, "level_month": 10, "season_next": 1,
         "growth_yoy": 0.1, "oneoff_excluded": 0, "lost_demand_12m": 0, "cover_days": 6, "moq": 1},
        {"sku": "SKU-2", "article": "1C-002", "name": "Автомат", "supplier": "S2",
         "category": "Защита", "rec_qty": 0, "urgency": "Плановая", "reason": "Запас достаточный",
         "risk": 0.1, "on_hand": 50, "in_transit_total": 0, "level_month": 5, "season_next": 1,
         "growth_yoy": 0, "oneoff_excluded": 0, "lost_demand_12m": 0, "cover_days": 100, "moq": 1},
    ])
    oneoffs = pd.DataFrame([{"sku": "SKU-1", "doc": "D-1", "date": pd.Timestamp("2026-01-01"), "qty": 100,
                             "typical": 5, "excluded": 95, "reason": "разовый заказ"}])
    return ProcurementAgent(orders, oneoffs)


def test_ask_without_api_key_returns_matching_item_reason(agent):
    answer = agent.ask("Что заказать по SKU-1?")
    assert "1C-001" in answer
    assert "Покрытие ниже срока поставки" in answer


def test_ask_without_code_returns_supplier_summaries(agent):
    answer = agent.ask("Покажи общую ситуацию по поставщикам")
    assert "S1: к заказу 1 позиций, 12 шт., критичных 1." in answer
    assert "S2: к заказу 0 позиций, 0 шт., критичных 0." in answer


def test_tools_return_compact_json_and_letter_is_marked_draft(agent):
    assert json.loads(agent.find_items("кабель"))[0]["sku"] == "SKU-1"
    item = json.loads(agent.get_item("SKU-1"))
    assert item["oneoffs"][0]["doc"] == "D-1"
    letter = agent.draft_supplier_letter("S1", agent.orders)
    assert "1C-001" in letter and "12" in letter
    assert "ЧЕРНОВИК — требует утверждения" in letter
