from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def at():
    app_file = Path(__file__).resolve().parents[1] / "app" / "app.py"
    return AppTest.from_file(app_file, default_timeout=180).run()


def test_app_renders_without_exceptions_and_shows_six_tabs(at):
    assert not at.exception
    tabs = [tab.label for tab in at.tabs]
    expected = [
        "Заказ поставщикам",
        "Излишки",
        "AI-ассистент",
        "Машина времени",
        "Разбор артикула",
        "Разовые заказы",
    ]
    assert len(tabs) == 6
    assert all(any(label in tab for tab in tabs) for label in expected)


def test_order_editor_has_required_columns(at):
    # AppTest exposes st.data_editor as a dataframe element (key starts with ed_).
    editors = [frame for frame in at.dataframe if str(frame.key).startswith("ed_")]
    assert editors
    required = {"Утвердить", "К заказу", "Обоснование"}
    assert any(required <= set(editor.value.columns) for editor in editors)


def test_search_handles_regex_characters_and_cyrillic_query(at, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    search = next(item for item in at.text_input if item.label.startswith("Поиск"))

    search.input("[")
    at.run()
    assert not at.exception

    next(item for item in at.text_input if item.label.startswith("Поиск")).input("УЗО")
    at.run()
    assert not at.exception


def test_all_order_mode_when_app_test_exposes_segmented_control(at):
    controls = at.get("segmented_control")
    if not controls:
        pytest.skip("Streamlit AppTest не предоставляет segmented_control в этом приложении")

    mode = next((control for control in controls if control.label == "Режим"), None)
    if mode is None:
        pytest.skip("AppTest не обнаружил segmented_control «Режим»")
    mode.set_value("Все позиции к заказу")
    at.run()
    assert not at.exception


def test_approval_creates_workbook_with_approver(at, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    approval_dir = Path(__file__).resolve().parents[1] / "data" / "approved"
    before = set(approval_dir.glob("*.xlsx")) if approval_dir.exists() else set()
    try:
        who = next(item for item in at.text_input if item.label == "Кто утверждает (ФИО)")
        who.input("Иван Иванов")
        at.run()
        assert not at.exception

        next(button for button in at.button if button.label == "Утвердить заказ").click()
        at.run()
        assert not at.exception
        assert any("Заказ утверждён" in message.value for message in at.success)

        created = (set(approval_dir.glob("*.xlsx")) if approval_dir.exists() else set()) - before
        assert created, "После утверждения файл .xlsx не создан"
        workbook = pd.read_excel(next(iter(created)), sheet_name=None)
        assert "Утверждение" in workbook
        assert "Иван Иванов" in workbook["Утверждение"].astype(str).to_numpy()
    finally:
        created_after = (set(approval_dir.glob("*.xlsx")) if approval_dir.exists() else set()) - before
        for path in created_after:
            path.unlink(missing_ok=True)
