# -*- coding: utf-8 -*-
"""
АГЕНТНЫЙ СЛОЙ. Три роли поверх правилового цикла скользящего горизонта.

Архитектурное правило FR-7.2: LLM НЕ НАЗЫВАЕТ ЧИСЕЛ, попадающих в расчёт.
Отсюда способ интеграции, который стоит понять до чтения кода:

    кандидатов ГЕНЕРИРУЕТ КОД (make_candidates), из состояния скважин.
    Агент только ОТБИРАЕТ и РАНЖИРУЕТ то, что уже сгенерировано.

Агент физически не может испортить число: он возвращает теги кандидатов,
а не значения дебитов. Худшее, что он сделает — отбракует хорошего
кандидата, и это видно в журнале как разница ЧДД между режимами off и live.

Роли:
    Аналитик  — читает диагностику, формулирует гипотезы (что происходит)
    Стратег   — отбирает K кандидатов из N для прогона (что попробовать)
    Критик    — объясняет выбор по УЖЕ ПОСЧИТАННЫМ числам (почему так)

Режимы наследуются от LLMClient: off / live / replay. В off все три роли
возвращают правиловый результат, цикл работает как раньше.

    python3 agents.py test        проверка ролей на синтетическом состоянии
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from llm_client import LLMClient

# ==========================================================================
# ЭКОНОМИЧЕСКИЙ КОНТЕКСТ.
# Без него модель тянет из литературы предел обводнённости 90-95% и
# предлагает закрывать прибыльные скважины. У нас предел 98,5%.
# Числа здесь — ВХОДНЫЕ константы методики, а не результат расчёта:
# они задают рамку рассуждения и в выходное расписание не попадают.
# ==========================================================================
ECONOMIC_CONTEXT = """\
Контекст задачи (нефтяное месторождение, 49 скважин, синтетическая модель).

Экономика по методике организаторов:
- маржа нефти много выше стоимости подъёма жидкости, поэтому экономический
  предел обводнённости 98,5%, а НЕ привычные из литературы 90-95%;
- закрывать обводнённую скважину почти всегда убыточно: пока обводнённость
  ниже 98,5%, скважина приносит деньги;
- главный рычаг — форсирование отбора жидкости: эффект приходит сразу;
- закачка работает с лагом 6-10 месяцев: у конца горизонта трогать её
  бессмысленно, платим сейчас, эффект не доживёт до оценки;
- смена ЭЦН стоит денег, поэтому частые переключения режима невыгодны;
- горизонт управления закрыт: терминальная стоимость не учитывается.

Твоя роль — качественная. Ты НЕ называешь дебиты, обводнённости и деньги:
все числа считает симулятор. Ты выбираешь ИЗ ГОТОВОГО СПИСКА вариантов.
"""


# ==========================================================================
# СХЕМЫ. Только они попадают в format при декодировании.
# ==========================================================================
class Hypothesis(BaseModel):
    well: str = Field(description="номер скважины из предложенного списка")
    issue: Literal["watercut", "decline", "unprofitable",
                   "headroom", "injection_useless", "idle"] = Field(
        description="что с ней происходит")
    rationale: str = Field(description="одно предложение, без чисел")
    # Явная уверенность вместо интонации: «недостаточно данных» —
    # допустимый ответ, а не признак плохой работы агента.
    confidence: Literal["высокая", "средняя", "низкая"] = Field(
        description="уверенность в гипотезе")


class AnalysisReport(BaseModel):
    """Аналитик: что происходит на объекте."""
    summary: str = Field(description="два-три предложения о состоянии фонда")
    hypotheses: list[Hypothesis] = Field(description="до пяти гипотез")


class Pick(BaseModel):
    tag: str = Field(description="тег кандидата ТОЧНО из предложенного списка")
    rationale: str = Field(description="одно предложение, без чисел")


class StrategyPlan(BaseModel):
    """Стратег: какие кандидаты отдать в прогон и в каком порядке."""
    picks: list[Pick] = Field(description="отобранные кандидаты, лучший первым")


class Verdict(BaseModel):
    """Критик: объяснение принятого решения для отчёта и дашборда."""
    explanation: str = Field(description="почему выбранный вариант выиграл")
    risk: Literal["low", "medium", "high"] = Field(
        description="риск решения на оставшемся горизонте")
    watch: str = Field(description="за чем следить на следующем шаге")
    # Замыкание контура «гипотеза -> расчёт -> вердикт». Без него критик
    # только комментирует победителя и никогда не признаёт, что гипотеза
    # аналитика не подтвердилась.
    hypothesis_check: Literal["подтверждена", "частично",
                              "не подтверждена", "не проверялась"] = Field(
        description="что стало с гипотезами аналитика после расчёта")


# ==========================================================================
# ЗАЩИТА ОТ ЧИСЕЛ
# ==========================================================================
# Модель охотно пишет «увеличить закачку до 312.5 м3/сут» — число она
# выдумала. В расчёт оно не попадает (в расчёт идёт тег), но на дашборде
# и в отчёте организаторам такая строка выглядит как источник правды.
# Вырезаем всё, что похоже на величину с единицей измерения.
_NUM_UNIT = re.compile(
    r"\b\d[\d\s.,]*\s*(?:м3\s*/?\s*сут|м³\s*/?\s*сут|т\s*/?\s*сут|"
    r"млн(?:\s*руб)?|руб(?:\./?\s*т|/\s*м3)?|бар|%|процент\w*)",
    re.IGNORECASE)


def scrub(text: str) -> str:
    """Убирает выдуманные величины из свободного текста."""
    cleaned = _NUM_UNIT.sub("[значение из расчёта]", text or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def has_invented_numbers(text: str) -> bool:
    return bool(_NUM_UNIT.search(text or ""))


# ==========================================================================
class AgentLayer:
    """
    Обёртка над LLMClient. Каждый метод имеет правиловый fallback и
    возвращает его при mode=off, промахе кэша в replay или любой ошибке.
    """

    def __init__(self, client: LLMClient | None = None,
                 trace_path=None):
        self.llm = client or LLMClient()
        self.trace_path = trace_path
        self.rejected = 0          # сколько ответов отбраковано валидацией

    # ------------------------------------------------------------------
    def _trace(self, role: str, payload: dict) -> None:
        if not self.trace_path:
            return
        import json
        from datetime import datetime
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "role": role, **payload},
                               ensure_ascii=False) + "\n")

    # --- РОЛЬ 1: АНАЛИТИК ---------------------------------------------
    def analyze(self, diag: dict, step: int, months_left: int,
                triggers: list[str]) -> AnalysisReport:
        """Качественная сводка состояния. На расчёт не влияет — только отчёт."""
        fallback = AnalysisReport(
            summary=f"Шаг {step + 1}, до конца горизонта {months_left} мес. "
                    f"Сработавших триггеров: {len(triggers)}.",
            hypotheses=[Hypothesis(well=w, issue=_ISSUE_MAP.get(k, "headroom"),
                                   rationale="правиловая диагностика",
                                   confidence="высокая")
                        for k, ws in diag.items() for w in ws[:2]][:3])

        prompt = (
            f"{ECONOMIC_CONTEXT}\n"
            f"Шаг управления {step + 1}. До конца горизонта {months_left} месяцев.\n\n"
            f"Правиловая диагностика фонда:\n{_fmt_diag(diag)}\n\n"
            f"Сработавшие триггеры:\n" +
            ("\n".join(f"- {t}" for t in triggers) or "- нет") +
            "\n\nСформулируй, что происходит на объекте, и до пяти гипотез. "
            "Не называй никаких величин."
        )
        res = self.llm.complete(prompt, AnalysisReport, fallback=fallback,
                                system="Ты инженер-разработчик месторождения.")
        res = res or fallback
        res.summary = scrub(res.summary)
        for h in res.hypotheses:
            h.rationale = scrub(h.rationale)
        self._trace("analyst", {"step": step, "summary": res.summary,
                                "n_hypotheses": len(res.hypotheses)})
        return res

    # --- РОЛЬ 2: СТРАТЕГ ----------------------------------------------
    def rank_candidates(self, cands: list[tuple], diag: dict, step: int,
                        months_left: int, limit: int) -> list[tuple]:
        """
        ГЛАВНАЯ точка, где агент влияет на результат.

        Вход  — полный список кандидатов, сгенерированный КОДОМ.
        Выход — тот же список, отфильтрованный и переупорядоченный.

        Никаких новых кандидатов агент создать не может: всё, чего нет во
        входе, отбрасывается при валидации. Поэтому режим live не способен
        подсунуть в расписание величину, которой не было в наборе.
        """
        by_tag = dict(cands)
        packs = [t for t, _ in cands if t.startswith("PACK")]
        # правиловый порядок: noop, пакеты, остальное — пакеты давали
        # заметно больше одиночных воздействий
        rule_order = (["noop"] if "noop" in by_tag else []) + packs + \
                     [t for t, _ in cands if t != "noop" and t not in packs]
        fallback = [(t, by_tag[t]) for t in rule_order[:limit]]

        if self.llm.mode == "off" or len(cands) <= limit:
            return fallback

        prompt = (
            f"{ECONOMIC_CONTEXT}\n"
            f"Шаг {step + 1}, до конца горизонта {months_left} месяцев.\n\n"
            f"Диагностика:\n{_fmt_diag(diag)}\n\n"
            f"Варианты воздействий (тег = скважина_множитель, "
            f"PACK = несколько скважин сразу):\n" +
            "\n".join(f"- {t}: {_describe(acts)}" for t, acts in cands) +
            f"\n\nПрогон каждого варианта дорог, посчитать можно только {limit}. "
            f"Отбери {limit} самых перспективных, лучший первым. "
            f"Теги копируй буквально. Величины не называй."
        )
        plan = self.llm.complete(prompt, StrategyPlan, fallback=None,
                                 system="Ты инженер по оптимизации разработки.")

        if plan is None:
            self._trace("strategist", {"step": step, "used": "fallback"})
            return fallback

        # ВАЛИДАЦИЯ: тег обязан быть из входного набора
        picked, unknown = [], []
        for p in plan.picks:
            tag = p.tag.strip()
            if tag in by_tag and tag not in [t for t, _ in picked]:
                picked.append((tag, by_tag[tag]))
            else:
                unknown.append(tag)
        if unknown:
            self.rejected += len(unknown)
            print(f"    [agent] отброшено несуществующих тегов: "
                  f"{len(unknown)} ({', '.join(unknown[:3])})")

        # noop обязателен: без него не с чем сравнивать выигрыш
        if "noop" in by_tag and "noop" not in [t for t, _ in picked]:
            picked.insert(0, ("noop", by_tag["noop"]))

        if len(picked) < 2:
            self._trace("strategist", {"step": step, "used": "fallback",
                                       "reason": "мало валидных тегов"})
            return fallback

        picked = picked[:limit]
        self._trace("strategist", {
            "step": step, "used": "llm",
            "picked": [t for t, _ in picked],
            "rationale": {p.tag: scrub(p.rationale) for p in plan.picks
                          if p.tag in by_tag}})
        return picked

    # --- РОЛЬ 3: КРИТИК -----------------------------------------------
    def explain(self, best_tag: str, gain_mln: float, ranking: dict,
                step: int, months_left: int,
                hypotheses: list | None = None) -> Verdict:
        """
        Объяснение ПОСЛЕ расчёта. Числа в промпт приходят из симулятора,
        обратно в расчёт не идут — только в отчёт и на дашборд.
        """
        fallback = Verdict(
            explanation=f"Вариант {best_tag} дал наибольший ЧДД на окне "
                        f"среди {len(ranking)} посчитанных.",
            risk="low" if months_left > 9 else "medium",
            watch="обводнённость форсированных скважин",
            hypothesis_check="не проверялась")

        # Гипотезы аналитика идут критику на проверку: контур
        # «гипотеза -> расчёт -> вердикт» замыкается, и в трейсе видно,
        # что агент признаёт неподтверждённые гипотезы.
        hyp_block = ""
        if hypotheses:
            hyp_block = ("Гипотезы, выдвинутые до расчёта:\n" +
                         "\n".join(f"- {h.well} ({h.issue}): {h.rationale}"
                                   for h in hypotheses) + "\n")

        top = sorted(ranking.items(), key=lambda kv: -kv[1])[:5]
        prompt = (
            f"{ECONOMIC_CONTEXT}\n"
            f"Шаг {step + 1}, до конца горизонта {months_left} месяцев.\n\n"
            f"Симулятор посчитал ЧДД на окне для вариантов "
            f"(эти числа — результат расчёта, а не твои):\n" +
            "\n".join(f"- {t}: {v:,.1f} млн" for t, v in top) +
            f"\n\nВыбран {best_tag}, выигрыш к бездействию {gain_mln:+,.1f} млн.\n"
            + hyp_block +
            "Объясни инженеру, почему этот вариант выиграл, оцени риск и "
            "скажи, за чем следить дальше. Отдельно скажи, подтвердились ли "
            "гипотезы: неподтверждённую гипотезу называй неподтверждённой, "
            "а не подгоняй под результат. Своих величин не добавляй."
        )
        res = self.llm.complete(prompt, Verdict, fallback=fallback,
                                system="Ты критик-рецензент технических решений.")
        res = res or fallback
        res.explanation = scrub(res.explanation)
        res.watch = scrub(res.watch)
        self._trace("critic", {"step": step, "best": best_tag,
                               "gain": gain_mln, "risk": res.risk,
                               "explanation": res.explanation})
        return res


# ==========================================================================
_ISSUE_MAP = {"water_breakthrough": "watercut", "unprofitable": "unprofitable",
              "headroom": "headroom", "declining": "decline", "idle": "idle"}


def _fmt_diag(diag: dict) -> str:
    lines = [f"- {k}: {', '.join(v)}" for k, v in diag.items() if v]
    return "\n".join(lines) or "- отклонений не найдено"


def _describe(acts) -> str:
    """Качественное описание кандидата: БЕЗ величин, только направление."""
    if not acts:
        return "ничего не менять"
    parts = []
    for a in acts:
        kind = "отбор" if a.control == "LRAT" else "закачка"
        m = re.search(r"x([\d.]+)", a.reason or "")
        direction = "увеличить" if (m and float(m.group(1)) > 1) else "снизить"
        parts.append(f"{a.well}: {direction} {kind}")
    return "; ".join(parts)


# ==========================================================================
def _self_test() -> int:
    diag = {"water_breakthrough": ["12", "31"], "declining": ["42"],
            "headroom": ["5", "17"], "unprofitable": [], "idle": []}

    class A:                      # заглушка Action
        def __init__(self, w, c, r): self.well, self.control, self.reason = w, c, r

    cands = [("noop", []),
             ("42_x1p25", [A("42", "LRAT", "87 -> 109 (x1.25)")]),
             ("42_x1p75", [A("42", "LRAT", "87 -> 152 (x1.75)")]),
             ("12_x0p7", [A("12", "LRAT", "200 -> 140 (x0.7)")]),
             ("PACK_prod_x1p5", [A("42", "LRAT", "x1.5"), A("5", "LRAT", "x1.5")])]

    print("=" * 62)
    print("1. scrub:")
    bad = "Увеличить закачку на 25% до 312.5 м3/сут, эффект ~40 млн руб"
    print(f"   было:  {bad}")
    print(f"   стало: {scrub(bad)}")
    assert not has_invented_numbers(scrub(bad)), "фильтр чисел не сработал"

    for mode in ("off", "live"):
        print(f"\n2. режим {mode}:")
        ag = AgentLayer(LLMClient(mode=mode))
        if mode == "live":
            ok, msg = ag.llm.health()
            print(f"   {msg}")
            if not ok:
                print("   пропуск live"); continue
        rep_ = ag.analyze(diag, step=0, months_left=23,
                          triggers=["первый шаг"])
        print(f"   аналитик: {rep_.summary[:76]}")
        picked = ag.rank_candidates(cands, diag, 0, 23, limit=3)
        print(f"   стратег:  {[t for t, _ in picked]}")
        assert all(t in dict(cands) for t, _ in picked), "выдуманный тег прошёл"
        assert "noop" in [t for t, _ in picked], "noop потерян"
        v = ag.explain("PACK_prod_x1p5", 42.7,
                       {"noop": 1023.4, "PACK_prod_x1p5": 1066.1,
                        "42_x1p25": 1041.0}, 0, 23,
                       hypotheses=rep_.hypotheses)
        print(f"   критик:   риск {v.risk}, гипотеза {v.hypothesis_check}")
        print(f"             {v.explanation[:66]}")

    print("\n" + "=" * 62)
    print("АГЕНТНЫЙ СЛОЙ РАБОТАЕТ (числа в расчёт не попадают)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test() if len(sys.argv) > 1 and sys.argv[1] == "test" else 0)
