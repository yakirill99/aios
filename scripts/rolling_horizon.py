# -*- coding: utf-8 -*-
"""
СКОЛЬЗЯЩИЙ ГОРИЗОНТ (Model Predictive Control).

Отличие от статического перебора: на каждом шаге читаем СОСТОЯНИЕ объекта
и выбираем действие с учётом того, что уже произошло. Статика ставит режимы
один раз и не реагирует на обводнение.

Алгоритм шага t:
  1. состояние x_t из последнего зачётного прогона
  2. диагностика: кто обводнился, у кого запас, где закачка вхолостую
  3. генерация кандидатов из словаря действий
  4. оценка каждого прогоном на окне [cut, t + lookahead]
  5. выбор argmax ЧДД, применение, запись в расписание
  6. зачётный прогон -> состояние для шага t+1

    python3 rolling_horizon.py plan       показать план без прогонов
    python3 rolling_horizon.py run        запустить цикл
    python3 rolling_horizon.py resume     продолжить прерванный

Цикл возобновляем: состояние пишется в journal.json после каждого шага.
Ничего не удаляет без --force.

    python3 rolling_horizon.py run --llm off      правила (умолчание)
    python3 rolling_horizon.py run --llm live     агент отбирает кандидатов
    python3 rolling_horizon.py run --llm replay   только кэш, ноль вызовов

Правки после ревью 27.08.2026:
  Д1  обрезка кандидатов перенесена из make_candidates в цикл — срез
      out[:K] отрезал пакетные варианты, которые добавляются последними
  Д2  состояние следующего шага берётся из ПРИНЯТОГО варианта, а не из
      лучшего: при отклонении по порогу выигрыша цикл планировал по
      реальности, которой нет в расписании
  Д3  умолчания CLI приведены к Config и к конфигурации лучшего прогона
  Д4  расчёт ЧДД идёт с SUBMISSION_ASSUMPTIONS — теми же допущениями,
      которыми пройдена сверка с эталоном
  Д5  пороги диагностики берутся из Config, а не дублируются литералами
  Д6  пропущенные шаги пишутся в журнал, resume больше не расходится
  Д7  расписание рендерится один раз; финальная дека собирается из того
      же файла, который уходит в сдачу
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from probe_wells import WellMode, parse_schedule                     # noqa
from schedule_builder import (Action, ScheduleBuilder, build_deck,    # noqa
                              MAX_LIQUID_RATE)

# Внешние ограничения. Без модуля цикл работает как раньше, на
# зашитых пределах уровня скважины.
try:
    from constraints import Constraints
    HAS_CONSTRAINTS = True
except ImportError:
    HAS_CONSTRAINTS = False

# Агентный слой необязателен: без него цикл работает на правилах.
try:
    from agents import AgentLayer
    from llm_client import LLMClient
    HAS_AGENTS = True
except ImportError:
    HAS_AGENTS = False

# Д4. Допущения расчёта ЧДД. ОДНО место на весь проект.
# Сверка с эталоном организаторов выбрала вариант с начислением первичной
# установки ЭЦН: +0,92% против +3,53% без него. Заявляемое число обязано
# считаться теми же допущениями, что и проверенное.
SUBMISSION_ASSUMPTIONS = {"chargeInitialPump": True}

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ==========================================================================
@dataclass
class Config:
    base: Path                          # дека Model_Y_opm
    workdir: Path                       # ~/aios-data/rolling
    cut: date = date(2014, 1, 1)        # начало управления
    end: date = date(2015, 12, 1)       # последний расчётный шаг деки
    step_months: int = 3                # шаг управления
    # На горизонте 23 месяца смотреть вперёд дальше конца бессмысленно:
    # окно всегда обрезается концом деки.
    lookahead_months: int = 23          # глубина прогноза
    max_candidates: int = 25            # K на шаг
    n_prod: int = 4                     # добывающих в рассмотрении
    n_inj: int = 2                      # нагнетательных
    # Д3. Набор, на котором получен лучший результат (+125,2 млн).
    # Умолчания CLI обязаны совпадать с этими — иначе запуск без
    # аргументов молча считает другую задачу.
    levels: tuple = (0.7, 1.25, 1.75, 2.25, 3.0)
    parallel: int = 6
    # Медиана лага отклика по матрице с окном 60 мес — 10 месяцев.
    # Трогать скважину чаще бессмысленно: результат предыдущего
    # воздействия ещё не проявился, а за переключение уже заплачено.
    # Медиана лага 10 мес, горизонт 23 мес. Второе воздействие на ту же
    # скважину успеет проявиться только если сделано в первой трети окна.
    min_interval_months: int = 9        # между воздействиями на одной скважине

    # --- ТРИГГЕРЫ ------------------------------------------------------
    # Вмешиваться на каждом шаге бессмысленно и дорого:
    #   экономика  — переключение стоит ~1 млн, смена ЭЦН 2,35-9,85 млн
    #   физика     — медиана лага отклика 5 мес, раньше результата не видно
    #   вычисления — 14 шагов вместо 36 сокращают бюджет прогонов втрое
    # Прошлый прогон подтвердил: с 10-го шага из 18 все решения были noop.
    use_triggers: bool = True
    trig_watercut: float = 0.975        # прорыв воды (эконом. предел ~98,5%)
    trig_decline: float = 0.08          # темп падения дебита за шаг
    trig_margin_neg: bool = True        # отрицательный вклад в поток
    trig_inj_useless: float = 0.0       # нагнетательная без связей в матрице
    trig_min_gain_mln: float = 2.0      # ниже — не стоит переключения
    tie_eps_mln: float = 1.0            # зона равенства: внутри неё
                                        # предпочитается вариант с меньшим
                                        # числом воздействий
    trig_force_first: bool = True       # первый шаг всегда активный
    headroom_min: float = 0.5           # доля запаса до предела по жидкости
    image: str = "aios/opm:2026.04"
    mem_limit_gb: int = 4
    llm_mode: str = "off"               # off | live | replay
    constraints_path: str = ""          # configs/constraints.yaml
    events_path: str = ""               # файл недоступности скважин


@dataclass
class StepRecord:
    step: int
    date: str
    chosen_tag: str
    chosen_actions: list[dict]
    npv_window: float
    npv_noop: float
    gain: float
    n_candidates: int
    duration_s: float
    diagnosis: dict = field(default_factory=dict)
    # объяснения агентного слоя — источник данных для панели дашборда
    analyst_summary: str = ""
    explanation: str = ""
    risk: str = ""


# ==========================================================================
class WellState:
    """Состояние скважины на шаге — из сводок последнего прогона."""

    def __init__(self, name: str, kind: str):
        self.name, self.kind = name, kind
        self.q_oil = self.q_liq = self.q_inj = 0.0
        self.wct = 0.0
        self.bhp = 0.0
        self.decline = 0.0          # темп падения дебита нефти за шаг
        self.last_action_step = -99

    @property
    def is_open(self) -> bool:
        return self.q_liq > 0.01 or self.q_inj > 0.01

    @property
    def headroom(self) -> float:
        """Запас до предела по жидкости, доля."""
        return max(0.0, (MAX_LIQUID_RATE - self.q_liq) / MAX_LIQUID_RATE)

    @property
    def margin_rub_day(self) -> float:
        """Вклад в денежный поток, руб/сут. Нефть в м3 -> т через 0.833."""
        return 8360 * self.q_oil * 0.833 - 100 * self.q_liq


def read_state(runner, smspec: Path, at: date, prev: dict | None = None
               ) -> dict[str, WellState]:
    """
    Читает состояние фонда через КОНТЕЙНЕР: resdata стоит там, а не на хосте.
    """
    raw = runner.read_state_json(smspec, at)
    out: dict[str, WellState] = {}
    for n, d in raw.get("wells", {}).items():
        st = WellState(n, d["kind"])
        st.q_oil, st.q_liq, st.q_inj = d["q_oil"], d["q_liq"], d["q_inj"]
        st.wct, st.bhp, st.decline = d["wct"], d["bhp"], d["decline"]
        if prev and n in prev:
            st.last_action_step = prev[n].last_action_step
        out[n] = st
    return out


# ==========================================================================
def diagnose(states: dict[str, WellState], step: int, cfg: Config) -> dict:
    """
    Правиловая диагностика. Экономические пороги — из методики ЧДД:
    предел обводнённости ~98.5%, а не привычные 90-95%.
    """
    findings = {"water_breakthrough": [], "unprofitable": [],
                "headroom": [], "declining": [], "idle": []}

    for n, st in states.items():
        if not st.is_open:
            findings["idle"].append(n)
            continue
        if st.kind != "prod":
            continue
        # Д5. Пороги берутся из Config, а не дублируются литералами:
        # иначе систему можно настроить в противоречивое состояние —
        # триггер сработает, а диагностика промолчит.
        if st.wct > cfg.trig_watercut:
            findings["water_breakthrough"].append(n)
        if st.margin_rub_day < 0:
            findings["unprofitable"].append(n)
        if st.headroom > cfg.headroom_min:
            findings["headroom"].append(n)
        if st.decline > cfg.trig_decline:
            findings["declining"].append(n)

    return {k: v[:6] for k, v in findings.items()}


def check_triggers(states: dict[str, WellState], diag: dict, step: int,
                   cfg: Config, lags: dict,
                   months_left: int = 999) -> tuple[bool, list[str]]:
    """
    Нужно ли вмешиваться на этом шаге. Возвращает (да/нет, причины).

    Между срабатываниями система наблюдает: снимает состояние, считает
    производные, пишет трейс — но воздействий не назначает. Расписание
    при этом остаётся помесячным, просто в большинстве месяцев пусто.
    """
    if not cfg.use_triggers:
        return True, ["триггеры отключены"]
    if cfg.trig_force_first and step == 0:
        return True, ["первый шаг: точка передачи управления"]

    fired: list[str] = []

    wc = [n for n, st in states.items()
          if st.kind == "prod" and st.is_open and st.wct > cfg.trig_watercut]
    if wc:
        fired.append(f"прорыв воды >{cfg.trig_watercut:.1%}: {', '.join(wc[:4])}")

    dec = [n for n, st in states.items()
           if st.kind == "prod" and st.is_open and st.decline > cfg.trig_decline]
    if dec:
        fired.append(f"падение дебита >{cfg.trig_decline:.0%}: {', '.join(dec[:4])}")

    if cfg.trig_margin_neg:
        neg = [n for n, st in states.items()
               if st.kind == "prod" and st.is_open and st.margin_rub_day < 0]
        if neg:
            fired.append(f"убыточны: {', '.join(neg[:4])}")

    # нагнетательная, чьи связи не успеют проявиться до конца окна
    useless = [n for n, st in states.items()
               if st.kind == "inj" and st.is_open and st.q_inj > 1
               and useful_degree(lags, n, months_left) <= cfg.trig_inj_useless]
    if useless:
        fired.append(f"закачка без отклика в окне: {', '.join(useless[:4])}")

    # у конца горизонта только быстрые воздействия имеют смысл
    if months_left < 6 and fired:
        fired.append(f"осталось {months_left} мес — только форсирование отбора")

    return bool(fired), fired


def pick_targets(states: dict[str, WellState], diag: dict, step: int,
                 cfg: Config, matrix_lags: dict,
                 months_left: int = 999) -> list[WellState]:
    """
    Кого рассматриваем на этом шаге. Приоритет: запас до предела + связность
    по матрице влияния. Скважины с недавним воздействием пропускаются —
    медиана лага отклика 5 месяцев, дёргать чаще бессмысленно.
    """
    def eligible(st: WellState) -> bool:
        return (st.is_open
                and step - st.last_action_step >= cfg.min_interval_months
                                                  // cfg.step_months)

    def score(st: WellState) -> float:
        # У нагнетательных headroom бессмысленен (константа 0.5), поэтому
        # раньше отбор шёл фактически по связности и скважина 41, которой
        # нужно СНИЖЕНИЕ закачки, ни разу не попадала в рассмотрение.
        # Теперь для них ключевой признак — связность по матрице влияния:
        # нагнетательная без связей качает вхолостую.
        if st.kind == "inj":
            # Закачка работает с лагом. Если до конца окна меньше, чем
            # медиана лага, трогать её бессмысленно: заплатим сейчас,
            # эффект не доживёт до оценки.
            deg = useful_degree(matrix_lags, st.name, months_left)
            if deg == 0 and months_left < 10:
                return -1.0            # исключаем из рассмотрения
            return abs(deg - 2) * 0.2 + 0.3
        s = st.headroom + 0.1 * useful_degree(matrix_lags, st.name, months_left)
        if st.name in diag["declining"]:
            s += 0.3
        if st.name in diag["unprofitable"]:
            s += 0.5
        return s

    prods = sorted((s for s in states.values()
                    if s.kind == "prod" and eligible(s)),
                   key=score, reverse=True)[:cfg.n_prod]
    injs = [s for s in sorted((s for s in states.values()
                               if s.kind == "inj" and eligible(s)),
                              key=score, reverse=True)[:cfg.n_inj]
            if score(s) >= 0]
    return prods + injs


def make_candidates(targets: list[WellState], at: date, cfg: Config
                    ) -> list[tuple[str, list[Action]]]:
    """
    Кандидаты шага. Одиночные воздействия плюс лучшая пара из прошлого
    опыта: форсировать добывающую и перераспределить закачку.
    """
    out: list[tuple[str, list[Action]]] = [("noop", [])]
    d = at.isoformat()

    for st in targets:
        for lv in cfg.levels:
            if abs(lv - 1.0) < 1e-9:
                continue
            if st.kind == "prod":
                base = st.q_liq
                new = min(base * lv, MAX_LIQUID_RATE)
                ctl = "LRAT"
            else:
                base = st.q_inj
                new = base * lv
                ctl = "RATE"
            if base < 0.01 or abs(new - base) < 0.5:
                continue
            a = Action(date=d, well=st.name, control=ctl, value=round(new, 1),
                       reason=f"{base:.0f} -> {new:.0f} (x{lv})")
            out.append((f"{st.name}_x{lv:g}".replace(".", "p"), [a]))

    prods = [s for s in targets if s.kind == "prod"]
    injs = [s for s in targets if s.kind == "inj"]

    # На горизонте 23 месяца форсирование отбора — главный рычаг: эффект
    # приходит сразу, тогда как закачка работает с лагом 6-10 месяцев и
    # до конца окна проявится лишь частично.
    #
    # ПАКЕТНЫЕ кандидаты. Статический перебор показал: шесть одновременных
    # воздействий дают +313 млн, а цикл по одному за шаг — только +183.
    # Причина в дисконте: позднее воздействие стоит меньше раннего, и
    # растягивать их по годам невыгодно.
    if prods:
        acts = [Action(date=d, well=p.name, control="LRAT",
                       value=round(min(p.q_liq * 1.5, MAX_LIQUID_RATE), 1),
                       reason="форсирование пакетом")
                for p in prods[:4] if p.q_liq > 0.01]
        if len(acts) >= 2:
            out.append(("PACK_prod_x1p5", acts))

    # перераспределение закачки: нарастить эффективную, придушить вредную
    if len(injs) >= 2:
        acts = [
            Action(date=d, well=injs[0].name, control="RATE",
                   value=round(injs[0].q_inj * 1.5, 1), reason="нарастить"),
            Action(date=d, well=injs[-1].name, control="RATE",
                   value=round(injs[-1].q_inj * 0.7, 1), reason="придушить"),
        ]
        out.append(("PACK_inj_redistribute", acts))

    # комбинированный пакет: форсирование + перераспределение
    if prods and len(injs) >= 2:
        acts = ([Action(date=d, well=p.name, control="LRAT",
                        value=round(min(p.q_liq * 1.5, MAX_LIQUID_RATE), 1),
                        reason="форсирование")
                 for p in prods[:3] if p.q_liq > 0.01]
                + [Action(date=d, well=injs[0].name, control="RATE",
                          value=round(injs[0].q_inj * 1.5, 1), reason="нарастить"),
                   Action(date=d, well=injs[-1].name, control="RATE",
                          value=round(injs[-1].q_inj * 0.7, 1), reason="придушить")])
        if len(acts) >= 3:
            out.append(("PACK_full", acts))

    # Д1. ОБРЕЗКИ ЗДЕСЬ НЕТ.
    # Пакетные кандидаты добавляются последними, поэтому срез out[:K]
    # отрезал ровно их — те самые, что дают больше одиночных воздействий.
    # Отбор до K перенесён в цикл, после ранжирования (правилового или
    # агентского), где пакеты стоят сразу за noop.
    return out


# ==========================================================================
class Runner:
    """Обёртка запуска симулятора и расчёта ЧДД."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.uid = subprocess.run(["id", "-u"], capture_output=True,
                                  text=True).stdout.strip()
        self.gid = subprocess.run(["id", "-g"], capture_output=True,
                                  text=True).stdout.strip()

    def _docker(self, extra: list[str]) -> list[str]:
        return ["docker", "run", "--rm", "-i",
                "--user", f"{self.uid}:{self.gid}",
                "-w", "/out", "-e", "HOME=/tmp",
                "--memory", f"{self.cfg.mem_limit_gb}g",
                "--memory-swap", f"{self.cfg.mem_limit_gb}g"] + extra

    def simulate(self, deck_dir: Path, out: Path, threads: int = 2) -> bool:
        out.mkdir(parents=True, exist_ok=True)
        deck = next(deck_dir.glob("*.DATA")).name
        cmd = self._docker([
            "-v", f"{deck_dir}:/case:ro", "-v", f"{out}:/out",
            "-e", f"OMP_NUM_THREADS={threads}", self.cfg.image,
            "flow", f"/case/{deck}", "--output-dir=/out"])
        with (out / "flow.log").open("w") as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
        log = (out / "flow.log").read_text(errors="replace")
        return "End of simulation" in log

    def read_state_json(self, smspec: Path, at: date) -> dict:
        """Состояние фонда через контейнер (resdata там)."""
        cmd = self._docker([
            "-v", f"{smspec.parent}:/out", "-v", f"{HERE}:/scripts:ro",
            self.cfg.image, "python3", "/scripts/read_state.py",
            f"/out/{smspec.name}", "--at", at.isoformat()])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            print(f"    не удалось прочитать состояние: {r.stderr[:200]}")
            return {}
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {}

    def npv(self, out: Path, start: date) -> float | None:
        """Сводки -> CSV -> ЧДД эталонным расчётом."""
        smspec = next(out.glob("*.SMSPEC"), None)
        if smspec is None:
            return None
        cmd = self._docker([
            "-v", f"{out}:/out", "-v", f"{HERE}:/scripts:ro", self.cfg.image,
            "python3", "/scripts/summary_to_csv.py",
            f"/out/{smspec.name}", "-o", "/out/chdd_input.csv",
            "--start", start.isoformat()])
        subprocess.run(cmd, capture_output=True)
        csv = out / "chdd_input.csv"
        if not csv.exists():
            return None
        # npv_official работает на хосте: ему нужен только эталонный
        # chdd_model.py, resdata не требуется
        try:
            from npv_official import compute_npv_from_csv
            return compute_npv_from_csv(
                str(csv), start_year=start.year,
                assumptions=dict(SUBMISSION_ASSUMPTIONS)).npv
        except Exception as e:
            print(f"    ошибка ЧДД: {e}")
            return None


# ==========================================================================
def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def load_matrix_links(path: Path, top: int = 3) -> dict[str, list[dict]]:
    """
    Связи между скважинами для промпта стратега.

    Матрица построена возмущающими прогонами и уже используется кодом при
    отборе целей, но агент её не видел и выбирал вслепую. Здесь она
    разворачивается в связи по каждому источнику: на кого влияет, через
    сколько месяцев приходит отклик, в какую сторону.

    Числа тут измеренные, а не выдуманные моделью: это входные данные
    того же рода, что экономические нормативы.
    """
    if not path or not Path(path).exists():
        return {}
    import csv
    raw: dict[str, list[dict]] = {}
    for r in csv.DictReader(Path(path).open(encoding="utf-8-sig")):
        if r.get("metric") != "oil":
            continue
        try:
            src = str(r["source"])
            link = {"observed": str(r["observed"]),
                    "lag": int(r.get("lag_month", 0)),
                    "sign": str(r.get("sign", "")).strip(),
                    "strength": abs(float(r.get("peak_rel", 0) or 0))}
        except (ValueError, KeyError):
            continue
        if link["observed"] == src:
            continue                       # связь скважины с самой собой
        raw.setdefault(src, []).append(link)

    out: dict[str, list[dict]] = {}
    for src, links in raw.items():
        # одна пара встречается на разных уровнях возмущения:
        # оставляем самую сильную по каждой наблюдаемой скважине
        best: dict[str, dict] = {}
        for l in links:
            cur = best.get(l["observed"])
            if cur is None or l["strength"] > cur["strength"]:
                best[l["observed"]] = l
        out[src] = sorted(best.values(), key=lambda l: -l["strength"])[:top]
    return out


def load_matrix_lags(path: Path) -> dict[str, list[int]]:
    """
    Лаги откликов по каждому источнику. Фильтрация НЕ статическая:
    полезность связи зависит от момента вмешательства.

    Организаторы: «эффект отложенных решений не доживает до оценки за
    пределами горизонта, оптимизируйте строго внутри окна».

    Значит критерий такой:  лаг + месяц вмешательства <= горизонт.
    Связь с лагом 10 месяцев полезна на 5-м месяце и бесполезна на 18-м.
    """
    if not path.exists():
        return {}
    import csv
    out: dict[str, list[int]] = {}
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        if r.get("metric") != "oil":
            continue
        out.setdefault(r["source"], []).append(int(r.get("lag_month", 0)))
    return out


def _rule_order(cands: list[tuple]) -> list[tuple]:
    """
    Правиловый порядок кандидатов: noop, пакеты, одиночные.

    Пакеты идут вперёд намеренно: одновременные воздействия дают заметно
    больше растянутых по шагам, потому что дисконт удешевляет позднее
    воздействие. При обрезке до K первыми должны выживать они.
    """
    noop = [c for c in cands if c[0] == "noop"]
    packs = [c for c in cands if c[0].startswith("PACK")]
    rest = [c for c in cands if c[0] != "noop" and not c[0].startswith("PACK")]
    return noop + packs + rest


def useful_degree(lags: dict[str, list[int]], well: str,
                  months_left: int) -> int:
    """Сколько связей скважины успеют проявиться за оставшееся время."""
    return sum(1 for l in lags.get(well, []) if l <= months_left)


class RollingHorizon:
    def __init__(self, cfg: Config, matrix: Path | None = None):
        self.cfg = cfg
        self.runner = Runner(cfg)
        self.schedule: list[Action] = []
        self.journal: list[StepRecord] = []
        self.lags = load_matrix_lags(matrix) if matrix else {}
        # Те же данные, развёрнутые в связи: кто на кого влияет и с каким
        # лагом. Нужны стратегу — раньше он отбирал кандидатов, не зная,
        # какие скважины связаны между собой.
        self.links = load_matrix_links(matrix) if matrix else {}
        if self.lags:
            allv = [l for v in self.lags.values() for l in v]
            allv.sort()
            n_links = sum(len(v) for v in self.links.values())
            print(f"  матрица: {len(self.lags)} источников, "
                  f"медиана лага {allv[len(allv)//2]} мес, "
                  f"связей для агента {n_links}")
        self.states: dict[str, WellState] = {}

        sch = next(cfg.base.glob("INCLUDE/*sch*.inc"), None)
        if sch is None:
            sys.exit(f"не найден sch.inc в {cfg.base}/INCLUDE")
        self.modes = parse_schedule(sch, cfg.cut)

        self.wd = cfg.workdir
        self.wd.mkdir(parents=True, exist_ok=True)
        self.jpath = self.wd / "journal.json"
        self.lock = self.wd / ".lock"

        # Внешние ограничения: квоты по фонду, недоступность скважин.
        self.limits = None
        if HAS_CONSTRAINTS:
            self.limits = Constraints.load(cfg.constraints_path or None)
            if cfg.events_path:
                k = self.limits.load_events(cfg.events_path)
                print(f"  ограничений по времени: {k}")
            act = [x for x in (self.limits.max_total_liquid_rate,
                               self.limits.max_total_injection_rate,
                               self.limits.max_total_oil_period,
                               self.limits.max_actions_per_step)
                   if x is not None]
            print(f"  ограничения: {self.limits.source}, "
                  f"групповых задано {len(act)}, "
                  f"недоступностей {len(self.limits.unavailable)}")

        # Агентный слой. В режиме off все роли возвращают правиловый
        # результат, цикл считает ровно то же, что и без него.
        if HAS_AGENTS:
            self.agents = AgentLayer(LLMClient(mode=cfg.llm_mode),
                                     trace_path=self.wd / "llm_trace.jsonl")
            # Пороги из конфигурации — не выдумка модели: разрешаем их
            # в свободном тексте, иначе объяснения теряют смысл.
            try:
                from agents import add_known_values
                add_known_values(
                    f"{cfg.trig_watercut * 100:.1f}%".replace(".", ","),
                    f"{cfg.trig_decline * 100:.0f}%",
                    f"{self.limits.economic_watercut_limit * 100:.1f}%"
                    .replace(".", ",") if self.limits else "",
                    f"{self.limits.max_liquid_rate:.0f} м3/сут"
                    if self.limits else "")
            except ImportError:
                pass
            # Печатаем состояние КЛИЕНТА, а не значение конфигурации.
            print(f"  агентный слой: {self.agents.llm.describe()}")
            if self.agents.llm.mode != cfg.llm_mode:
                print(f"  ВНИМАНИЕ: запрошен режим {cfg.llm_mode}, "
                      f"клиент работает в {self.agents.llm.mode}")
        else:
            self.agents = None
            if cfg.llm_mode != "off":
                print("  agents.py не найден — цикл идёт на правилах")

    def acquire_lock(self) -> None:
        """
        Защита от повторного запуска. Два экземпляра пишут в один каталог
        и затирают результаты друг друга — так уже терялись часы прогонов.
        """
        import os
        if self.lock.exists():
            try:
                pid = int(self.lock.read_text().strip())
                os.kill(pid, 0)          # процесс жив?
                sys.exit(
                    f"уже запущен: PID {pid}\n"
                    f"  остановить:  kill {pid}\n"
                    f"  снять замок:  rm {self.lock}")
            except (ValueError, ProcessLookupError, PermissionError):
                print(f"  найден остаточный замок — снимаю")
        self.lock.write_text(str(os.getpid()))
        import atexit
        atexit.register(lambda: self.lock.unlink(missing_ok=True))

    # ------------------------------------------------------------------
    def _log_skip(self, step: int, cur, reason: str, diag: dict,
                  t0: float) -> None:
        """Шаг без решения тоже попадает в журнал — иначе resume поедет."""
        self.journal.append(StepRecord(
            step=step, date=cur.isoformat(), chosen_tag=reason,
            chosen_actions=[], npv_window=0.0, npv_noop=0.0, gain=0.0,
            n_candidates=0, duration_s=time.time() - t0, diagnosis=diag))
        self.save()

    def n_steps(self) -> int:
        n, d = 0, self.cfg.cut
        while d < self.cfg.end:
            d = add_months(d, self.cfg.step_months)
            n += 1
        return n

    def save(self) -> None:
        self.jpath.write_text(json.dumps({
            "config": {k: (str(v) if isinstance(v, (Path, date)) else v)
                       for k, v in asdict(self.cfg).items()},
            "schedule": [asdict(a) for a in self.schedule],
            "journal": [asdict(r) for r in self.journal],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> int:
        if not self.jpath.exists():
            return 0
        data = json.loads(self.jpath.read_text(encoding="utf-8"))
        self.schedule = [Action(**a) for a in data["schedule"]]
        self.journal = [StepRecord(**r) for r in data["journal"]]
        print(f"возобновление: выполнено шагов {len(self.journal)}, "
              f"воздействий {len(self.schedule)}")
        return len(self.journal)

    # ------------------------------------------------------------------
    def build(self, tag: str, actions: list[Action], horizon_end: date) -> Path:
        """Собирает деку: история + накопленное расписание + кандидат."""
        d = self.wd / "decks" / tag
        b = ScheduleBuilder(self.modes, self.cfg.cut, horizon_end)
        b.add_many(self.schedule + actions)
        issues = [i for i in b.validate() if i.severity == "error"]
        if issues:
            raise ValueError(issues[0].message)
        inc = self.wd / f"{tag}.inc"
        inc.write_text(b.render(header=False), encoding="utf-8")
        build_deck(self.cfg.base, d, self.cfg.cut, inc, force=True)
        inc.unlink()
        return d

    def evaluate(self, cands: list[tuple[str, list[Action]]],
                 horizon_end: date, step: int) -> dict[str, float]:
        """Параллельная оценка кандидатов."""
        from concurrent.futures import ThreadPoolExecutor

        jobs = []
        for tag, acts in cands:
            try:
                deck = self.build(f"s{step:02d}_{tag}", acts, horizon_end)
            except ValueError as e:
                print(f"    {tag}: отбракован — {e}")
                continue
            jobs.append((tag, deck, self.wd / "runs" / f"s{step:02d}_{tag}"))

        def one(job):
            tag, deck, out = job
            if not self.runner.simulate(deck, out):
                return tag, None
            return tag, self.runner.npv(out, self.cfg.cut)

        res: dict[str, float] = {}
        t_eval = time.time()
        print(f"  [{time.strftime('%H:%M:%S')}] запуск {len(jobs)} прогонов "
              f"по {self.cfg.parallel} параллельно...")
        with ThreadPoolExecutor(max_workers=self.cfg.parallel) as ex:
            for tag, npv in ex.map(one, jobs):
                if npv is not None:
                    res[tag] = npv
                else:
                    print(f"    {tag}: прогон не удался")
        print(f"  [{time.strftime('%H:%M:%S')}] оценка заняла "
              f"{(time.time()-t_eval)/60:.1f} мин, посчитано {len(res)}")
        return res

    # ------------------------------------------------------------------
    def run(self, resume: bool = False) -> None:
        self.acquire_lock()
        start_step = self.load() if resume else 0
        total = self.n_steps()

        self.t_start = time.time()
        print(f"\nстарт: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"версия: пакетные кандидаты, уровни {self.cfg.levels}")
        print(f"дата передачи управления: {self.cfg.cut}")
        print(f"шагов управления: {total} "
              f"(по {self.cfg.step_months} мес), "
              f"глубина прогноза {self.cfg.lookahead_months} мес")
        print(f"кандидатов на шаг: до {self.cfg.max_candidates}")
        print(f"каталог: {self.wd}\n")

        # начальное состояние — из опорного прогона без воздействий
        if start_step == 0:
            print("опорный прогон для стартового состояния...")
            deck = self.build("base", [], self.cfg.end)
            out = self.wd / "runs" / "base"
            if not self.runner.simulate(deck, out, threads=4):
                sys.exit("опорный прогон не удался")
            self.states = read_state(self.runner, next(out.glob("*.SMSPEC")), self.cfg.cut)
            npv0 = self.runner.npv(out, self.cfg.cut)
            print(f"  ЧДД без вмешательств: {npv0:,.1f} млн руб\n")
            self.baseline = npv0
        else:
            self.baseline = self.journal[0].npv_noop

        cur = add_months(self.cfg.cut, start_step * self.cfg.step_months)

        for step in range(start_step, total):
            t0 = time.time()
            horizon_end = min(add_months(cur, self.cfg.lookahead_months),
                              self.cfg.end)
            el = time.time() - self.t_start
            eta = (el / step * (total - step)) if step > 0 else 0
            print(f"=== шаг {step+1}/{total}  {cur}  окно до {horizon_end} ===")
            print(f"  время: {time.strftime('%H:%M:%S')}, "
                  f"прошло {el/60:.0f} мин"
                  + (f", осталось ~{eta/60:.0f} мин "
                     f"(до {time.strftime('%H:%M', time.localtime(time.time()+eta))})"
                     if step > 0 else ""))

            diag = diagnose(self.states, step, self.cfg)
            act_diag = {k: v for k, v in diag.items() if v}
            if act_diag:
                for k, v in act_diag.items():
                    print(f"  {k}: {', '.join(v)}")

            months_left = (self.cfg.end.year - cur.year) * 12 + \
                          (self.cfg.end.month - cur.month)
            print(f"  до конца горизонта: {months_left} мес")
            need, reasons = check_triggers(self.states, diag, step,
                                           self.cfg, self.lags, months_left)
            if not need:
                print("  ${Y}триггеры не сработали — только наблюдение${N}"
                      .replace("${Y}", "").replace("${N}", ""))
                self.journal.append(StepRecord(
                    step=step, date=cur.isoformat(), chosen_tag="observe",
                    chosen_actions=[], npv_window=0.0, npv_noop=0.0, gain=0.0,
                    n_candidates=0, duration_s=time.time() - t0,
                    diagnosis=act_diag))
                self.save()
                cur = add_months(cur, self.cfg.step_months)
                print()
                continue
            for r in reasons:
                print(f"  ТРИГГЕР: {r}")

            analyst, rep_ = "", None
            if self.agents is not None:
                rep_ = self.agents.analyze(act_diag, step, months_left, reasons)
                analyst = rep_.summary
                print(f"  аналитик: {analyst}")

            targets = pick_targets(self.states, diag, step, self.cfg,
                                   self.lags, months_left)

            # Скважина в ремонте не должна попадать в рассмотрение: без
            # этого система продолжала бы предлагать по ней воздействия,
            # не зная о недоступности.
            if self.limits is not None and self.limits.unavailable:
                before = list(targets)
                targets = [t for t in targets
                           if self.limits.is_available(str(t), cur)]
                skipped = [t for t in before if t not in targets]
                if skipped:
                    print(f"  недоступны на {cur}: {', '.join(map(str, skipped))}")
            if not targets:
                # Д6. Пропуск тоже пишется в журнал: иначе resume пройдёт
                # шаг заново, но уже с другим состоянием, и цикл перестанет
                # быть воспроизводимым.
                print("  нет доступных скважин (интервал между воздействиями)")
                self._log_skip(step, cur, "no_targets", act_diag, t0)
                cur = add_months(cur, self.cfg.step_months)
                continue
            print(f"  рассматриваем: {', '.join(t.name for t in targets)}")

            cands = make_candidates(targets, cur, self.cfg)
            print(f"  кандидатов сгенерировано: {len(cands)}")

            # Отбраковка по внешним ограничениям с записью причины.
            if self.limits is not None:
                kept, dropped = [], []
                for tag, acts in cands:
                    bad = self.limits.check_actions(acts, horizon_end)
                    errs = [i for i in bad if i.severity == "error"]
                    if errs and tag != "noop":
                        dropped.append((tag, errs[0].message))
                    else:
                        kept.append((tag, acts))
                if dropped:
                    print(f"  отбраковано ограничениями: {len(dropped)}"
                          f"  ({dropped[0][0]}: {dropped[0][1]})")
                    cands = kept

            # Д1. Отбор до max_candidates происходит ЗДЕСЬ, после
            # ранжирования. В режиме off порядок правиловый:
            # noop, пакеты, одиночные — пакеты больше не отрезаются.
            if self.agents is not None:
                cands = self.agents.rank_candidates(
                    cands, act_diag, step, months_left,
                    limit=self.cfg.max_candidates, links=self.links)
                # Источник берётся из слоя по факту вызова, а не из
                # конфигурации: раньше в логе стояло «(агент)» даже когда
                # роль strategist не вызывалась ни разу.
                who = self.agents.last_source
            else:
                cands = _rule_order(cands)[:self.cfg.max_candidates]
                who = "правила (agents.py не установлен)"
            print(f"  отобрано на прогон: {len(cands)} ({who})")

            res = self.evaluate(cands, horizon_end, step)
            if not res:
                print("  ни один кандидат не посчитался — пропуск шага")
                self._log_skip(step, cur, "no_results", act_diag, t0)
                cur = add_months(cur, self.cfg.step_months)
                continue

            best_tag = max(res, key=res.get)
            # Принцип минимального вмешательства: если несколько вариантов
            # укладываются в tie_eps_mln по ЧДД, берём тот, где меньше
            # воздействий. Каждое лишнее переключение — риск смены ЭЦН и
            # лишняя строка в расписании, которую придётся объяснять.
            near = [t for t, v in res.items()
                    if res[best_tag] - v <= self.cfg.tie_eps_mln]
            if len(near) > 1:
                acts = {t: len(dict(cands).get(t, [])) for t in near}
                lean = min(near, key=lambda t: (acts[t], -res[t]))
                if lean != best_tag and acts[lean] < acts[best_tag]:
                    print(f"  {lean}: в пределах {self.cfg.tie_eps_mln} млн "
                          f"от {best_tag}, но воздействий {acts[lean]} "
                          f"против {acts[best_tag]} — берём его")
                    best_tag = lean
            best_npv = res[best_tag]
            noop_npv = res.get("noop", best_npv)
            gain = best_npv - noop_npv

            print(f"  лучший: {best_tag}  {best_npv:,.1f} млн "
                  f"({gain:+.1f} к noop)")

            chosen = dict(cands)[best_tag]
            accept = (best_tag != "noop"
                      and gain >= self.cfg.trig_min_gain_mln)
            if accept:
                self.schedule.extend(chosen)
                for a in chosen:
                    if a.well in self.states:
                        self.states[a.well].last_action_step = step
            else:
                chosen = []
                print(f"  выигрыш {gain:+.1f} млн ниже порога "
                      f"{self.cfg.trig_min_gain_mln} — не переключаемся")

            # Д2. Зачётным становится прогон ПРИНЯТОГО варианта.
            # Раньше при отклонении в расписание не шло ничего, а состояние
            # читалось из прогона С этим воздействием — цикл планировал по
            # реальности, которой нет, и ошибка копилась по шагам.
            accepted_tag = best_tag if accept else (
                "noop" if "noop" in res else best_tag)
            if accepted_tag != best_tag:
                print(f"  состояние берём из {accepted_tag}, "
                      f"а не из {best_tag}")
            zach = self.wd / "runs" / f"s{step:02d}_ACCEPTED"
            src = self.wd / "runs" / f"s{step:02d}_{accepted_tag}"
            if src.exists():
                if zach.exists():
                    shutil.rmtree(zach)
                shutil.copytree(src, zach)
            nxt = add_months(cur, self.cfg.step_months)
            sm = next(zach.glob("*.SMSPEC"), None)
            if sm:
                self.states = read_state(self.runner, sm, nxt, self.states)

            explanation, risk = "", ""
            if self.agents is not None:
                v = self.agents.explain(
                    best_tag, gain, res, step, months_left,
                    hypotheses=getattr(rep_, "hypotheses", None))
                explanation, risk = v.explanation, v.risk
                print(f"  критик: {explanation} (риск {risk})")

            self.journal.append(StepRecord(
                step=step, date=cur.isoformat(),
                chosen_tag=best_tag if accept else "rejected",
                chosen_actions=[asdict(a) for a in chosen],
                npv_window=best_npv, npv_noop=noop_npv, gain=gain,
                n_candidates=len(cands), duration_s=time.time() - t0,
                diagnosis=act_diag, analyst_summary=analyst,
                explanation=explanation, risk=risk))
            self.save()

            # чистим пробные прогоны шага, оставляем зачётный
            for d in (self.wd / "runs").glob(f"s{step:02d}_*"):
                if d.name.endswith("ACCEPTED"):
                    continue
                for f in d.glob("*"):
                    if f.suffix not in (".csv", ".log"):
                        f.unlink(missing_ok=True)
            shutil.rmtree(self.wd / "decks", ignore_errors=True)

            print(f"  шаг занял {(time.time()-t0)/60:.1f} мин, "
                  f"воздействий накоплено {len(self.schedule)}, "
                  f"всего прошло {(time.time()-self.t_start)/60:.0f} мин\n")
            cur = nxt

        self.finalize()

    # ------------------------------------------------------------------
    def finalize(self) -> None:
        print("=== финальный прогон полного расписания ===")

        # Д7. Расписание рендерится ОДИН раз, и финальная дека собирается
        # из того же файла, который уходит в сдачу. Раньше их было два:
        # проверяли один объект, сдавали другой.
        inc = self.wd / "wells_schedule.inc"
        b = ScheduleBuilder(self.modes, self.cfg.cut, self.cfg.end)
        b.add_many(self.schedule)
        issues = [i for i in b.validate() if i.severity == "error"]
        if issues:
            print(f"расписание не прошло валидацию: {issues[0].message}")
            return
        inc.write_text(b.render(), encoding="utf-8")

        deck = self.wd / "decks" / "FINAL"
        build_deck(self.cfg.base, deck, self.cfg.cut, inc, force=True)
        out = self.wd / "runs" / "FINAL"
        if not self.runner.simulate(deck, out, threads=4):
            print("финальный прогон не удался")
            return
        npv = self.runner.npv(out, self.cfg.cut)

        print(f"\nвсего времени: {(time.time()-self.t_start)/60:.0f} мин")
        print(f"ЧДД итогового расписания: {npv:,.1f} млн руб")
        if hasattr(self, "baseline"):
            print(f"планка:                   {self.baseline:,.1f} млн руб")
            print(f"дельта:                   {npv - self.baseline:+,.1f} млн "
                  f"({(npv/self.baseline - 1)*100:+.2f}%)")
        print(f"воздействий: {len(self.schedule)}")
        print(f"расписание:  {inc}")

        if self.agents is not None and self.cfg.llm_mode != "off":
            print(f"\nагентный слой: {self.agents.status()}")
            print(f"  {self.agents.llm.stats()}")

        print(f"\nПРОВЕРЬТЕ ПЕРЕД СДАЧЕЙ:")
        print(f"  ./validate.sh {out} --since {self.cfg.cut}")

        (self.wd / "result.json").write_text(json.dumps({
            "npv": npv, "baseline": getattr(self, "baseline", None),
            "n_actions": len(self.schedule),
            "assumptions": SUBMISSION_ASSUMPTIONS,
            "cut": self.cfg.cut.isoformat(), "end": self.cfg.end.isoformat(),
            "levels": list(self.cfg.levels), "step_months": self.cfg.step_months,
            "llm_mode": self.cfg.llm_mode,
            "llm_mode_effective": (self.agents.llm.mode
                                   if self.agents else "none"),
            "llm_steps": (self.agents.llm_steps if self.agents else 0),
            "rule_steps": (self.agents.rule_steps if self.agents else 0),
            "schedule": [asdict(a) for a in self.schedule],
        }, ensure_ascii=False, indent=2), encoding="utf-8")


# ==========================================================================
# Пути берутся из окружения, как в lib_common.sh. Зашитый ~/aios ломал
# работу во второй среде WSL, где репозиторий лежит по другому пути, и
# цикл молча уходил искать деку не туда.
AIOS_ROOT = Path(os.environ.get("AIOS_ROOT", "~/aios")).expanduser()
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "~/aios-data")).expanduser()


def main() -> None:
    p = argparse.ArgumentParser(description="скользящий горизонт")
    p.add_argument("cmd", choices=["plan", "run", "resume"])
    p.add_argument("--base", default=str(AIOS_ROOT / "model/Model_Y_opm"),
                   help="дека, адаптированная под OPM")
    p.add_argument("--workdir", default=str(DATA_ROOT / "rolling"))
    # Даты тоже не зашиты: в валидационном кейсе придут свои.
    p.add_argument("--cut", default=os.environ.get("CUT_DATE", "2014-01-01"),
                   help="дата передачи управления (конец истории)")
    p.add_argument("--end", default=os.environ.get("END_DATE", "2015-12-01"),
                   help="горизонт ЧДД ограничен декой")
    # Д3. Умолчания совпадают с Config и с конфигурацией, на которой
    # получен лучший результат. Запуск без аргументов обязан его
    # воспроизводить.
    _d = Config(base=Path("."), workdir=Path("."))
    p.add_argument("--step", type=int, default=_d.step_months,
                   help="шаг управления, мес")
    p.add_argument("--lookahead", type=int, default=_d.lookahead_months,
                   help="прогноз, мес")
    p.add_argument("--candidates", type=int, default=_d.max_candidates)
    p.add_argument("--prod", type=int, default=4)
    p.add_argument("--inj", type=int, default=2)
    p.add_argument("--levels",
                   default=",".join(f"{x:g}" for x in _d.levels),
                   help="множители режима")
    p.add_argument("--parallel", type=int, default=6)
    p.add_argument("--no-triggers", action="store_true",
                   help="вмешиваться на каждом шаге (дороже, обычно не нужно)")
    p.add_argument("--min-gain", type=float, default=2.0,
                   help="порог выигрыша, млн руб: ниже не переключаемся")
    p.add_argument("--matrix",
                   default=str(DATA_ROOT / "probes/response_matrix.csv"))
    p.add_argument("--llm", default="off", choices=["off", "live", "replay"],
                   help="режим агентного слоя")
    p.add_argument("--constraints", default="",
                   help="путь к configs/constraints.yaml")
    p.add_argument("--events", default="",
                   help="файл недоступности скважин (ремонты по датам)")
    a = p.parse_args()

    cfg = Config(
        base=Path(a.base).expanduser(),
        workdir=Path(a.workdir).expanduser(),
        cut=date.fromisoformat(a.cut), end=date.fromisoformat(a.end),
        step_months=a.step, lookahead_months=a.lookahead,
        max_candidates=a.candidates, n_prod=a.prod, n_inj=a.inj,
        levels=tuple(float(x) for x in a.levels.split(",")),
        parallel=a.parallel,
        use_triggers=not a.no_triggers,
        trig_min_gain_mln=a.min_gain,
        llm_mode=a.llm,
        constraints_path=a.constraints,
        events_path=a.events)

    rh = RollingHorizon(cfg, Path(a.matrix).expanduser() if a.matrix else None)

    if a.cmd == "plan":
        n = rh.n_steps()
        runs = n * (cfg.max_candidates + 1)
        print(f"шагов управления:  {n} (по {cfg.step_months} мес)")
        print(f"кандидатов на шаг: до {cfg.max_candidates}")
        print(f"прогонов всего:    ~{runs}")
        print(f"оценка времени:    {runs * 240 / cfg.parallel / 3600:.1f} ч "
              f"при 240 с на прогон")
        print(f"место:             ~{runs * 25 / 1024:.1f} ГБ (чистится по ходу)")
        print(f"\nскважин в модели:  {len(rh.modes)}")
        print(f"матрица влияния:   "
              f"{'загружена' if rh.lags else 'не найдена'}")
        return

    rh.run(resume=(a.cmd == "resume"))


if __name__ == "__main__":
    main()
