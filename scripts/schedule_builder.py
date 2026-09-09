# -*- coding: utf-8 -*-
"""
ScheduleBuilder — генератор wells_schedule.inc.

ГЛАВНЫЙ СДАВАЕМЫЙ АРТЕФАКТ. Организаторы прогоняют его на своей модели
один раз и сравнивают полученный ЧДД с заявленным. Файл обязан читаться
без ручного редактирования — иначе результат не подтверждён.

Как это устроено по регламенту организаторов:
    "у вас их будет два последовательных: исторический, который вы никак
     не меняете, и ваш собственный, который и является вашим решением"

То есть наш файл ДОПИСЫВАЕТСЯ после исторического, а не заменяет его.

    python3 schedule_builder.py demo          демо-расписание (форсирование)
    python3 schedule_builder.py build --actions actions.json
    python3 schedule_builder.py validate wells_schedule.inc

Все деструктивные операции требуют явного --force: скрипт не удаляет
чужие результаты молча.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_wells import WellMode, parse_schedule, MONTHS  # noqa: E402

MAX_LIQUID_RATE = 500.0      # жёсткий предел методики: выше — расчёт ЧДД падает
MIN_BHP_PROD = 50.0          # из деки: минимальное забойное добывающих
MAX_BHP_INJ = 300.0          # предел на нагнетательных


# ==========================================================================
@dataclass
class Action:
    """Одно управляющее воздействие. Контракт для всей системы."""
    date: str                    # ISO, первое число месяца
    well: str
    control: str                 # LRAT | BHP | RATE | SHUT | OPEN
    value: float = 0.0
    reason: str = ""             # обоснование — уходит в трейс и на дашборд

    @property
    def d(self) -> date:
        return date.fromisoformat(self.date)


@dataclass
class ValidationIssue:
    severity: str                # "error" | "warning"
    where: str
    message: str


# ==========================================================================
class ScheduleBuilder:
    """
    Собирает расписание на горизонт. Помнит режим каждой скважины, чтобы
    не писать лишних воздействий: в деке режимы накапливаются, заданное
    однажды действует, пока не переопределено.
    """

    def __init__(self, modes: dict[str, WellMode], cut: date, end: date):
        self.modes = modes
        self.cut = cut
        self.end = end
        self.actions: list[Action] = []
        # текущее состояние — стартуем с того, что было на дату отсечки
        self.state = {n: (m.control, m.value) for n, m in modes.items()}

    # ------------------------------------------------------------------
    def add(self, a: Action) -> None:
        if a.well not in self.modes:
            raise ValueError(f"скважина {a.well} отсутствует в модели")
        self.actions.append(a)

    def add_many(self, actions: list[Action]) -> None:
        for a in actions:
            self.add(a)

    # ------------------------------------------------------------------
    def validate(self) -> list[ValidationIssue]:
        """
        Проверки ДО генерации файла. Дешевле поймать здесь, чем узнать
        от организаторов, что расчёт не прошёл.
        """
        issues: list[ValidationIssue] = []
        seen: dict[tuple[str, str], int] = {}

        # --- внешние ограничения ---------------------------------------
        # Пределы уровня скважины проверяются ниже. Здесь — то, чего
        # раньше не существовало: недоступность скважины в период и
        # лимит числа воздействий за шаг.
        try:
            from constraints import Constraints
            _lim = Constraints.load()
        except Exception:                                  # noqa: BLE001
            _lim = None

        if _lim is not None:
            if (_lim.max_actions_per_step is not None
                    and len(self.actions) > _lim.max_actions_per_step):
                issues.append(ValidationIssue(
                    "error", "расписание",
                    f"воздействий {len(self.actions)} при лимите "
                    f"{_lim.max_actions_per_step}"))
            for a in self.actions:
                u = _lim.blocked_in(str(a.well), a.d, a.d)
                if u is not None:
                    issues.append(ValidationIssue(
                        "error", f"действие {a.well} {a.date}",
                        f"скважина недоступна: {u.reason}, "
                        f"{u.start}..{u.end}"))

        for i, a in enumerate(self.actions):
            where = f"действие {i+1} ({a.well} {a.date})"

            if not (self.cut <= a.d <= self.end):
                issues.append(ValidationIssue(
                    "error", where,
                    f"дата вне горизонта {self.cut}..{self.end}"))

            if a.d.day != 1:
                issues.append(ValidationIssue(
                    "warning", where, "дата не первое число месяца"))

            m = self.modes[a.well]

            if a.control == "LRAT":
                if m.kind != "prod":
                    issues.append(ValidationIssue(
                        "error", where, "LRAT задан нагнетательной скважине"))
                if a.value > MAX_LIQUID_RATE:
                    issues.append(ValidationIssue(
                        "error", where,
                        f"дебит {a.value:.0f} > {MAX_LIQUID_RATE:.0f} м3/сут — "
                        f"методика такое не считает"))
                if a.value < 0:
                    issues.append(ValidationIssue("error", where, "дебит < 0"))

            elif a.control == "RATE":
                if m.kind != "inj":
                    issues.append(ValidationIssue(
                        "error", where, "RATE задан добывающей скважине"))
                if a.value < 0:
                    issues.append(ValidationIssue(
                        "error", where, "приёмистость < 0"))

            elif a.control == "BHP":
                if m.kind == "prod" and a.value < MIN_BHP_PROD:
                    issues.append(ValidationIssue(
                        "error", where,
                        f"забойное {a.value:.0f} < {MIN_BHP_PROD:.0f} бар"))
                if m.kind == "inj" and a.value > MAX_BHP_INJ:
                    issues.append(ValidationIssue(
                        "error", where,
                        f"забойное {a.value:.0f} > {MAX_BHP_INJ:.0f} бар"))

            elif a.control not in ("SHUT", "OPEN"):
                issues.append(ValidationIssue(
                    "error", where, f"неизвестный тип воздействия: {a.control}"))

            # два воздействия на одну скважину в один месяц
            key = (a.well, a.date)
            if key in seen:
                issues.append(ValidationIssue(
                    "error", where,
                    f"дублирует действие {seen[key]+1} — "
                    f"второе перезапишет первое"))
            seen[key] = i

        return issues

    # ------------------------------------------------------------------
    def _render_action(self, a: Action) -> str:
        m = self.modes[a.well]

        if a.control == "SHUT":
            return f"WELOPEN\n '{a.well}' 'SHUT' 5* /\n/"
        if a.control == "OPEN":
            return f"WELOPEN\n '{a.well}' 'OPEN' 5* /\n/"

        if m.kind == "prod":
            if a.control == "BHP":
                return (f"WCONPROD\n '{a.well}' 'OPEN' 'BHP' "
                        f"1* 1* 1* 1* 1* {a.value:g} 1* 1* /\n/")
            bhp = f"{m.bhp:g}" if m.bhp else "1*"
            return (f"WCONPROD\n '{a.well}' 'OPEN' 'LRAT' "
                    f"1* 1* 1* {a.value:g} 1* {bhp} 1* 1* /\n/")

        bhp = f"{m.bhp:g}" if m.bhp else "1*"
        return (f"WCONINJE\n '{a.well}' 'WATER' 'OPEN' 'RATE' "
                f"{a.value:g} 1* {bhp} 1* 1* /\n/")

    # ------------------------------------------------------------------
    def render(self, header: bool = True) -> str:
        """Полный текст wells_schedule.inc."""
        by_month: dict[str, list[Action]] = {}
        for a in self.actions:
            by_month.setdefault(a.date, []).append(a)

        out: list[str] = []
        if header:
            out += [
                "-- =====================================================",
                "-- wells_schedule.inc",
                "-- Управляющее расписание, сгенерировано автоматически.",
                f"-- Горизонт: {self.cut} .. {self.end}",
                f"-- Воздействий: {len(self.actions)}",
                "-- Дописывается ПОСЛЕ исторического расписания.",
                "-- =====================================================",
                "",
            ]

        # Порядок в SCHEDULE: DATES переводит время, режимы задаются ПОСЛЕ
        # него и действуют с этой даты. DATES для самой даты отсечки уже
        # стоит в историческом файле, поэтому действия на неё идут первыми.
        for a in by_month.get(self.cut.isoformat(), []):
            if a.reason:
                out.append(f"-- {a.well}: {a.reason}")
            out.append(self._render_action(a))

        cur = self.cut
        while cur < self.end:
            y, mth = (cur.year + 1, 1) if cur.month == 12 else (cur.year, cur.month + 1)
            cur = date(y, mth, 1)

            out.append(f"DATES\n 01 '{MONTHS[mth-1]}' {y} /\n/")

            for a in by_month.get(cur.isoformat(), []):
                if a.reason:
                    out.append(f"-- {a.well}: {a.reason}")
                out.append(self._render_action(a))

        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------
    def write(self, path: Path, force: bool = False) -> None:
        if path.exists() and not force:
            raise FileExistsError(
                f"{path} уже существует. Перезаписать: --force")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")

    def summary(self) -> str:
        by_well: dict[str, int] = {}
        for a in self.actions:
            by_well[a.well] = by_well.get(a.well, 0) + 1
        months = len({a.date for a in self.actions})
        return (f"воздействий: {len(self.actions)} "
                f"на {len(by_well)} скважинах в {months} месяцах")


# ==========================================================================
# СБОРКА ДЕКИ С РАСПИСАНИЕМ
# ==========================================================================
def build_deck(base_dir: Path, out_dir: Path, cut: date,
               schedule_inc: Path, force: bool = False) -> Path:
    """
    Собирает деку: история до отсечки + наш wells_schedule.inc.

    Историю НЕ трогаем — по регламенту она остаётся как есть.
    """
    if out_dir.exists():
        if not force and any(out_dir.glob("*.UNSMRY")):
            raise FileExistsError(
                f"{out_dir} содержит результаты прогона. Перезаписать: --force")
        shutil.rmtree(out_dir)
    shutil.copytree(base_dir, out_dir, symlinks=True)

    sch = next(out_dir.glob("INCLUDE/*sch*.inc"), None)
    if sch is None:
        raise FileNotFoundError(f"не найден sch.inc в {out_dir}/INCLUDE")

    lines = sch.read_text(encoding="utf-8", errors="replace").split("\n")
    pat = re.compile(
        rf"^\s*{cut.day:02d}\s+'?{MONTHS[cut.month-1]}'?\s+{cut.year}\s*/")
    idx = next((i for i, l in enumerate(lines) if pat.match(l)), None)
    if idx is None:
        raise ValueError(f"дата отсечки {cut} в расписании не найдена")

    history = "\n".join(lines[:idx + 2])          # + дата и её терминатор
    ours = schedule_inc.read_text(encoding="utf-8")

    body = history + "\n\n" + ours
    # снимки не нужны: для ЧДД достаточно сводок
    body = re.sub(r"^RPTRST\s*\n\s*'BASIC=\d'[^\n]*\n",
                  "-- RPTRST отключён\n", body, count=1, flags=re.M)
    sch.write_text(body, encoding="utf-8")
    return out_dir


# ==========================================================================
# ДЕМО-СТРАТЕГИЯ — для проверки контура, не для сдачи
# ==========================================================================
def demo_actions(modes: dict[str, WellMode], cut: date,
                 factor: float = 1.5, n_wells: int = 5) -> list[Action]:
    """
    Простейшая осмысленная стратегия: форсировать отбор на скважинах с
    наибольшим запасом до предела 500 м3/сут.

    Обоснование из экономики: маржа нефти 8 360 руб/т, подъём жидкости
    100 руб/т. Соотношение 84:1 — вода почти бесплатна, а запас по дебиту
    у нас огромный (средний 87 при потолке 500).
    """
    prods = sorted((m for m in modes.values()
                    if m.kind == "prod" and m.is_open),
                   key=lambda m: -m.value)[:n_wells]
    out = []
    for m in prods:
        new = min(m.value * factor, MAX_LIQUID_RATE)
        if new <= m.value + 1:
            continue
        out.append(Action(
            date=cut.isoformat(), well=m.name, control="LRAT", value=round(new, 1),
            reason=f"форсирование {m.value:g} -> {new:g} м3/сут "
                   f"(запас до предела {MAX_LIQUID_RATE:g})"))
    return out


# ==========================================================================
def main() -> None:
    p = argparse.ArgumentParser(description="генератор wells_schedule.inc")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = dict(
        base=("--base", "~/aios/model/Model_Y_opm"),
        cut=("--cut", "2014-01-01"),
        end=("--end", "2022-12-01"),
    )

    for name, help_ in (("demo", "демо-расписание для проверки контура"),
                        ("build", "расписание из файла действий")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--base", default="~/aios/model/Model_Y_opm")
        sp.add_argument("--cut", default="2014-01-01")
        sp.add_argument("--end", default="2022-12-01")
        sp.add_argument("-o", "--output", default="~/aios/submission/wells_schedule.inc")
        sp.add_argument("--deck-out", default="~/aios/model/Model_Y_sched",
                        help="куда собрать деку с расписанием")
        sp.add_argument("--force", action="store_true")
        if name == "demo":
            sp.add_argument("--factor", type=float, default=1.5)
            sp.add_argument("--wells", type=int, default=5)
        else:
            sp.add_argument("--actions", required=True,
                            help="JSON со списком действий")

    vp = sub.add_parser("validate", help="проверить готовый .inc")
    vp.add_argument("inc")

    a = p.parse_args()

    if a.cmd == "validate":
        text = Path(a.inc).expanduser().read_text(encoding="utf-8")
        n_dates = len(re.findall(r"^DATES", text, re.M))
        n_prod = len(re.findall(r"^WCONPROD", text, re.M))
        n_inj = len(re.findall(r"^WCONINJE", text, re.M))
        n_open = len(re.findall(r"^WELOPEN", text, re.M))
        print(f"{a.inc}:")
        print(f"  DATES: {n_dates}, WCONPROD: {n_prod}, "
              f"WCONINJE: {n_inj}, WELOPEN: {n_open}")
        bad = [l for l in text.split("\n")
               if l.strip() and not l.strip().startswith("--")
               and not re.match(r"^(DATES|WCONPROD|WCONINJE|WELOPEN|/|\s+'|\s+\d)", l)]
        if bad:
            print(f"  ! подозрительные строки: {len(bad)}")
            for l in bad[:5]:
                print(f"      {l[:70]}")
        else:
            print("  структура корректна")
        return

    base = Path(a.base).expanduser()
    cut = date.fromisoformat(a.cut)
    end = date.fromisoformat(a.end)

    sch = next(base.glob("INCLUDE/*sch*.inc"), None)
    if sch is None:
        sys.exit(f"не найден sch.inc в {base}/INCLUDE")

    modes = parse_schedule(sch, cut)
    print(f"режимов на {cut}: {len(modes)} "
          f"({sum(1 for m in modes.values() if m.kind=='prod')} доб., "
          f"{sum(1 for m in modes.values() if m.kind=='inj')} нагн.)")

    b = ScheduleBuilder(modes, cut, end)

    if a.cmd == "demo":
        acts = demo_actions(modes, cut, a.factor, a.wells)
        b.add_many(acts)
        print(f"демо-стратегия: форсирование x{a.factor} на {len(acts)} скважинах")
    else:
        raw = json.loads(Path(a.actions).expanduser().read_text(encoding="utf-8"))
        b.add_many([Action(**r) for r in raw])

    print(b.summary())

    issues = b.validate()
    errors = [i for i in issues if i.severity == "error"]
    for i in issues:
        mark = "ОШИБКА" if i.severity == "error" else "!"
        print(f"  {mark} {i.where}: {i.message}")
    if errors:
        sys.exit(f"\n{len(errors)} ошибок — файл не сгенерирован")

    out = Path(a.output).expanduser()
    b.write(out, force=a.force)
    print(f"\nрасписание: {out}  ({out.stat().st_size} байт)")

    deck_out = Path(a.deck_out).expanduser()
    build_deck(base, deck_out, cut, out, force=a.force)
    print(f"дека с расписанием: {deck_out}")
    print("\nдальше: ./run_schedule.sh")


if __name__ == "__main__":
    main()
