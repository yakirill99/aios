# -*- coding: utf-8 -*-
"""
Клиент LLM с тремя режимами. Единственное место, где система разговаривает
с языковой моделью.

  off    — NullAgent: система работает на правилах. Обязан работать всегда:
           это и страховка демо, и доказательство, что числа не приходят
           из генерации текста (FR-7.2, FR-7.5).
  live   — реальный вызов, ответ пишется в кэш
  replay — только кэш; повторный прогон детерминирован (NFR-3)

Structured output обязателен: схема передаётся в format, декодирование
ограничивается ею. Свободный текст в расчётный контур не попадает.

    python3 llm_client.py test          проверка связки
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Type

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = object


class LLMClient:
    def __init__(self, mode: str = "off",
                 model: str = "qwen3:14b",
                 host: str = "http://localhost:11434",
                 cache_dir: str | Path = "~/aios-data/llm_cache",
                 temperature: float = 0.0):
        self.mode = os.environ.get("AIOS_LLM_MODE", mode)
        self.model = os.environ.get("AIOS_LLM_MODEL", model)
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = self.cache_hits = 0

    # ------------------------------------------------------------------
    def _key(self, prompt: str, schema: dict) -> Path:
        blob = f"{self.model}|{self.temperature}|{prompt}|{json.dumps(schema, sort_keys=True)}"
        return self.cache / f"{hashlib.sha256(blob.encode()).hexdigest()[:32]}.json"

    def complete(self, prompt: str, schema: Type, fallback: Any = None,
                 system: str = "") -> Any:
        """
        Возвращает объект схемы. В режиме off — fallback, то есть решение,
        принятое правилами.
        """
        if self.mode == "off":
            return fallback

        js = schema.model_json_schema()
        path = self._key(prompt, js)

        if path.exists():
            self.cache_hits += 1
            return schema.model_validate_json(path.read_text(encoding="utf-8"))

        if self.mode == "replay":
            print("  [llm] промах кэша в режиме replay — возврат к правилам")
            return fallback

        try:
            raw = self._call(prompt, js, system)
            obj = schema.model_validate_json(raw)
        except Exception as e:
            print(f"  [llm] ошибка ({type(e).__name__}: {e}) — возврат к правилам")
            return fallback

        path.write_text(obj.model_dump_json(), encoding="utf-8")
        self.calls += 1
        return obj

    # ------------------------------------------------------------------
    def _call(self, prompt: str, schema: dict, system: str) -> str:
        import urllib.request

        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        body = json.dumps({
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "format": schema,                 # ограничение декодирования
            "options": {"temperature": self.temperature, "seed": 42},
        }).encode()

        req = urllib.request.Request(
            f"{self.host}/api/chat", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())["message"]["content"]

    # ------------------------------------------------------------------
    def stats(self) -> str:
        return (f"вызовов: {self.calls}, из кэша: {self.cache_hits}, "
                f"режим: {self.mode}, модель: {self.model}")

    def health(self) -> tuple[bool, str]:
        if self.mode == "off":
            return True, "режим off — модель не нужна"
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as r:
                names = [m["name"] for m in json.loads(r.read())["models"]]
            if not any(self.model.split(":")[0] in n for n in names):
                return False, f"модель {self.model} не найдена. Есть: {names}"
            return True, f"ollama доступен, модель {self.model} на месте"
        except Exception as e:
            return False, f"ollama недоступен: {e}"


# ==========================================================================
def _self_test() -> int:
    """Проверка связки: доступность, схема, детерминизм, режимы."""
    from pydantic import Field
    from typing import Literal

    class WellAction(BaseModel):
        well: str = Field(description="номер скважины")
        control: Literal["LRAT", "RATE", "SHUT"] = Field(
            description="тип воздействия")
        # Д11. Набор совпадает с рабочей сеткой оптимизатора: при демо
        # разные наборы в тесте и в цикле выглядят как рассинхрон.
        level: Literal[0.7, 1.25, 1.75, 2.25, 3.0] = Field(
            description="множитель текущего режима")
        reason: str = Field(description="обоснование, одно предложение")

    print("=" * 60)
    c = LLMClient(mode="live")
    ok, msg = c.health()
    print(f"1. связь: {msg}")
    if not ok:
        print("\n   что проверить:")
        print("     ollama serve            запущен ли сервер")
        print("     ollama list             какие модели есть")
        print("     AIOS_LLM_MODEL=имя      указать свою")
        return 1

    prompt = (
        "Скважина 42 — нагнетательная, приёмистость 250 м3/сут.\n"
        "Матрица влияния: она подпирает добывающую 56 (+12,5 м3/сут нефти, "
        "лаг 15 мес).\n"
        "Экономика: маржа нефти 8360 руб/т, закачка 30 руб/м3, "
        "предел обводнённости 98,5%.\n"
        "Предложи одно воздействие из словаря."
    )

    print("\n2. structured output:")
    r1 = c.complete(prompt, WellAction)
    if r1 is None:
        print("   не получен объект"); return 1
    print(f"   {r1.well} {r1.control} x{r1.level}")
    print(f"   обоснование: {r1.reason[:70]}")

    print("\n3. детерминизм (повтор из кэша):")
    r2 = c.complete(prompt, WellAction)
    same = (r1.well, r1.control, r1.level) == (r2.well, r2.control, r2.level)
    print(f"   {'совпало' if same else 'РАЗОШЛОСЬ'}  {c.stats()}")

    print("\n4. режим off (деградация до правил):")
    off = LLMClient(mode="off")
    fb = WellAction(well="42", control="RATE", level=1.25, reason="правило")
    r3 = off.complete(prompt, WellAction, fallback=fb)
    print(f"   вернул fallback: {r3.well} {r3.control} x{r3.level}")

    print("\n5. режим replay (только кэш):")
    rp = LLMClient(mode="replay")
    r4 = rp.complete(prompt, WellAction, fallback=fb)
    print(f"   {'из кэша' if rp.cache_hits else 'промах -> fallback'}: "
          f"{r4.well} x{r4.level}")

    print("=" * 60)
    print("СВЯЗКА РАБОТАЕТ" if same else "ПРОВЕРЬТЕ ДЕТЕРМИНИЗМ")
    return 0 if same else 1


if __name__ == "__main__":
    import sys
    sys.exit(_self_test() if len(sys.argv) > 1 and sys.argv[1] == "test" else 0)
