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
import time
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
                 temperature: float = 0.0,
                 timeout: int = 600,
                 num_predict: int = 768,
                 num_ctx: int = 8192):
        self.mode = os.environ.get("AIOS_LLM_MODE", mode)
        self.model = os.environ.get("AIOS_LLM_MODEL", model)
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = self.cache_hits = 0

        # Тайм-аут. 180 с не хватало: у схем с вложенными массивами
        # (список гипотез, список кандидатов) модель генерирует в разы
        # больше токенов, чем у плоской схемы критика, а первый вызов
        # ещё и грузит веса в память.
        self.timeout = int(os.environ.get("AIOS_LLM_TIMEOUT", timeout))
        # Потолок длины ответа: без него думающая модель уходит в
        # рассуждение на тысячи токенов.
        self.num_predict = int(os.environ.get("AIOS_LLM_NUM_PREDICT",
                                              num_predict))
        self.num_ctx = int(os.environ.get("AIOS_LLM_NUM_CTX", num_ctx))
        # qwen3 по умолчанию думает перед ответом. В структурированном
        # выводе рассуждение не нужно и стоит десятков секунд.
        self.think = os.environ.get("AIOS_LLM_THINK", "0") not in ("0", "")
        self._think_supported = True
        self.total_s = 0.0

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

        t0 = time.time()
        try:
            raw = self._call(prompt, js, system)
            obj = schema.model_validate_json(raw)
        except Exception as e:
            dt = time.time() - t0
            print(f"  [llm] ошибка за {dt:.0f} с "
                  f"({type(e).__name__}: {e}) — возврат к правилам")
            if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                print(f"       тайм-аут {self.timeout} с. Проверьте:")
                print(f"       ollama ps            модель должна быть 100% GPU")
                print(f"       AIOS_LLM_TIMEOUT=900 AIOS_LLM_NUM_PREDICT=512")
            return fallback

        path.write_text(obj.model_dump_json(), encoding="utf-8")
        self.calls += 1
        return obj

    # ------------------------------------------------------------------
    def _call(self, prompt: str, schema: dict, system: str) -> str:
        import urllib.error
        import urllib.request

        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]

        def _body(with_think: bool) -> bytes:
            payload = {
                "model": self.model,
                "messages": msgs,
                "stream": False,
                "format": schema,             # ограничение декодирования
                # модель остаётся в памяти между вызовами: иначе каждый
                # шаг цикла заново платит за загрузку весов
                "keep_alive": "30m",
                "options": {"temperature": self.temperature, "seed": 42,
                            "num_predict": self.num_predict,
                            "num_ctx": self.num_ctx},
            }
            if with_think:
                payload["think"] = self.think
            return json.dumps(payload).encode()

        def _post(with_think: bool) -> str:
            req = urllib.request.Request(
                f"{self.host}/api/chat", data=_body(with_think),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())["message"]["content"]

        t0 = time.time()
        try:
            out = _post(self._think_supported)
        except urllib.error.HTTPError as e:
            # старые сборки ollama не знают поля think — повторяем без него
            if self._think_supported and e.code in (400, 404, 422):
                self._think_supported = False
                print("  [llm] сервер не принял поле think — повтор без него")
                out = _post(False)
            else:
                raise
        self.total_s += time.time() - t0
        return out

    # ------------------------------------------------------------------
    def stats(self) -> str:
        avg = self.total_s / self.calls if self.calls else 0.0
        return (f"вызовов: {self.calls}, из кэша: {self.cache_hits}, "
                f"среднее {avg:.0f} с, "
                f"режим: {self.mode}, модель: {self.model}")

    def warmup(self) -> tuple[bool, str]:
        """
        Прогрев: короткий вызов без схемы, чтобы веса заехали в память.
        Первый содержательный вызов иначе платит за загрузку и упирается
        в тайм-аут — именно так выглядела ошибка 28.08.
        """
        if self.mode == "off":
            return True, "режим off — прогрев не нужен"
        import urllib.request
        body = json.dumps({
            "model": self.model, "stream": False, "keep_alive": "30m",
            "messages": [{"role": "user", "content": "ok"}],
            "options": {"num_predict": 1},
        }).encode()
        t0 = time.time()
        try:
            req = urllib.request.Request(
                f"{self.host}/api/chat", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                r.read()
            return True, f"модель в памяти, {time.time() - t0:.0f} с"
        except Exception as e:
            return False, f"прогрев не удался за {time.time() - t0:.0f} с: {e}"

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
    if ok:
        wok, wmsg = c.warmup()
        print(f"   прогрев: {wmsg}")
        ok = wok
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

    print(f"\n2. structured output (тайм-аут {c.timeout} с, "
          f"num_predict {c.num_predict}, think {c.think}):")
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
