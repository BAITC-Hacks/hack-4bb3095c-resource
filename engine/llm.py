"""Инфраструктурная обёртка над LLM (заготовка, раскрывается в README как prepared infra).
Цепочка: OpenAI -> NVIDIA Build -> demo-кэш. Structured Outputs через pydantic.
Лог всех вызовов в logs/llm_calls.jsonl — пригодится для демо «что делал агент»."""
import hashlib
import json
import os
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()
T = TypeVar("T", bound=BaseModel)
CACHE = Path(__file__).parent / "demo_cache"
LOG = Path(__file__).parent / "logs" / "llm_calls.jsonl"
DEMO = os.getenv("DEMO_MODE") == "1"


def _providers():
    if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL"):
        yield "openai", OpenAI(timeout=60, max_retries=2), os.environ["OPENAI_MODEL"]
    if os.getenv("NVIDIA_API_KEY") and os.getenv("NVIDIA_MODEL"):
        yield "nvidia", OpenAI(
            api_key=os.environ["NVIDIA_API_KEY"],
            base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            timeout=60,
            max_retries=2,
        ), os.environ["NVIDIA_MODEL"]


def _key(system: str, user: str, schema: str) -> str:
    return hashlib.sha256(f"{system}\n{user}\n{schema}".encode()).hexdigest()[:16]


def _log(**rec):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), **rec}, ensure_ascii=False) + "\n")


def ask(system: str, user: str, schema: type[T]) -> T:
    """Вернёт объект schema. Успешные ответы кэшируются -> DEMO_MODE=1 работает без ключей."""
    key = _key(system, user, schema.__name__)
    cached = CACHE / f"{key}.json"
    if DEMO:
        if cached.exists():
            return schema.model_validate_json(cached.read_text(encoding="utf-8"))
        raise RuntimeError("DEMO_MODE: нет кэша для этого запроса — прогоните сценарий с ключом один раз")
    errors = []
    for name, client, model in _providers():
        t0 = time.time()
        try:
            if name == "openai":
                r = client.chat.completions.parse(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format=schema,
                )
                out = r.choices[0].message.parsed
            else:  # NVIDIA: JSON-режим + валидация pydantic
                r = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system + "\nОтвечай ТОЛЬКО JSON по схеме:\n"
                         + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.2,
                )
                txt = r.choices[0].message.content.strip().removeprefix("```json").removesuffix("```")
                out = schema.model_validate_json(txt)
            CACHE.mkdir(exist_ok=True)
            cached.write_text(out.model_dump_json(), encoding="utf-8")
            _log(provider=name, model=model, ok=True, sec=round(time.time() - t0, 2), schema=schema.__name__)
            return out
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
            _log(provider=name, model=model, ok=False, err=str(e)[:300])
    if cached.exists():
        return schema.model_validate_json(cached.read_text(encoding="utf-8"))
    raise RuntimeError("Все провайдеры недоступны: " + " | ".join(errors) if errors else "Нет ключей в .env")
