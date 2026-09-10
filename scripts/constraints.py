# -*- coding: utf-8 -*-
"""
ВНЕШНИЕ ОГРАНИЧЕНИЯ.

Единственное место, где живут пределы. До этого модуля они существовали
в двух видах: с комментариями в configs/constraints.yaml и константами в
validate_output.py. Файл был декоративен — правка в нём ничего не меняла,
но выглядела влияющей.

Три уровня ограничений:

  скважина   дебит жидкости, забойные давления, интервал между
             воздействиями — работали и раньше
  фонд       суммарные добыча и закачка, число воздействий за шаг —
             механизма не существовало
  время      недоступность скважины в период (ремонт) — механизма
             не существовало

Валидационный кейс придёт с файлом ограничений: квоты, лимиты закачки,
ремонты с привязкой к датам. Формат организаторами не показан, поэтому
модуль устроен так: механизмы проверки не зависят от формата, а чтение
вынесено в отдельные функции. При появлении формата дописывается один
парсер, всё остальное остаётся как есть.

    python3 constraints.py                 показать загруженную конфигурацию
    python3 constraints.py --test          самопроверка на синтетике
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_PATH = "configs/constraints.yaml"


# ==========================================================================
# Чтение YAML. pyyaml есть не везде, а структура файла простая, поэтому
# при отсутствии библиотеки работает встроенный разбор.
# ==========================================================================
def _load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        return _mini_yaml(text)


def _mini_yaml(text: str) -> dict:
    """Разбор двухуровневого YAML со списками. Только для нашего файла."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    cur_list_key: str | None = None

    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if body.startswith("- "):
            val = _scalar(body[2:].strip())
            if cur_list_key is not None and isinstance(parent, dict):
                parent.setdefault(cur_list_key, []).append(val)
            elif isinstance(parent, list):
                parent.append(val)
            continue

        if ":" not in body:
            continue
        key, _, rest = body.partition(":")
        key, rest = key.strip(), rest.strip()
        if rest == "":
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
            cur_list_key = key
            # список может идти следующим уровнем: держим и dict, и list
            parent[key] = node
        else:
            parent[key] = _scalar(rest)
        cur_list_key = key if rest == "" else cur_list_key

    return _cleanup(root)


def _cleanup(node):
    """Пустые dict, в которые попали только списки, превращаем в списки."""
    if isinstance(node, dict):
        return {k: _cleanup(v) for k, v in node.items()}
    return node


def _scalar(s: str):
    s = s.strip().strip("'\"")
    if s in ("null", "~", ""):
        return None
    if s in ("true", "True", "yes"):
        return True
    if s in ("false", "False", "no"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


# ==========================================================================
@dataclass
class Unavailability:
    """Скважина недоступна в период: ремонт, отсутствие подъездов и т.п."""
    well: str
    start: date
    end: date
    reason: str = "недоступна"

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end

    def overlaps(self, a: date, b: date) -> bool:
        return not (b < self.start or a > self.end)

    def __str__(self) -> str:
        return f"скв. {self.well}: {self.start}..{self.end} — {self.reason}"


@dataclass
class Issue:
    severity: str        # "error" | "warning"
    scope: str           # "well" | "field" | "time"
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.where}: {self.message}"


# ==========================================================================
@dataclass
class Constraints:
    # уровень скважины
    max_liquid_rate: float = 500.0
    min_bhp_producer: float = 50.0
    max_bhp_injector: float = 300.0
    economic_watercut_limit: float = 0.985
    min_interval_months: int = 9

    # уровень фонда. None означает «ограничение не задано»
    max_total_liquid_rate: float | None = None      # м3/сут, сумма по фонду
    max_total_injection_rate: float | None = None   # м3/сут, сумма по фонду
    max_total_oil_period: float | None = None       # тыс. т за весь период
    max_actions_per_step: int | None = None

    # компенсация закачки
    compensation_min: float = 0.80
    compensation_max: float = 1.20

    # словарь действий
    allowed: list[str] = dc_field(default_factory=list)
    disabled: list[str] = dc_field(default_factory=list)

    # временные ограничения
    unavailable: list[Unavailability] = dc_field(default_factory=list)

    source: str = "(умолчания)"

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Constraints":
        """
        Читает configs/constraints.yaml. Отсутствие файла не ошибка:
        возвращаются умолчания, совпадающие с прежними константами.
        """
        p = Path(path or DEFAULT_PATH)
        if not p.exists():
            # /configs — монтирование внутри контейнера (см. run_py в
            # lib_common.sh). Без него валидатор молча брал умолчания,
            # и правка конфигурации ни на что не влияла.
            import os
            root = os.environ.get("AIOS_ROOT", "")
            alts = [Path("/configs/constraints.yaml"),
                    Path(__file__).resolve().parent.parent / DEFAULT_PATH,
                    Path.home() / "aios" / DEFAULT_PATH]
            if root:
                alts.insert(1, Path(root) / DEFAULT_PATH)
            for alt in alts:
                if alt.exists():
                    p = alt
                    break
        if not p.exists():
            return cls()

        d = _load_yaml(p)
        w = d.get("well") or {}
        f = d.get("field") or {}
        a = d.get("actions") or {}

        c = cls(
            max_liquid_rate=_f(w.get("max_liquid_rate"), 500.0),
            min_bhp_producer=_f(w.get("min_bhp_producer"), 50.0),
            max_bhp_injector=_f(w.get("max_bhp_injector"), 300.0),
            economic_watercut_limit=_f(w.get("economic_watercut_limit"), 0.985),
            min_interval_months=int(_f(w.get("min_interval_months"), 9)),
            # старые имена ключей поддерживаются: в файле они без _rate
            max_total_liquid_rate=_opt(f.get("max_total_liquid_rate",
                                             f.get("max_total_liquid"))),
            max_total_injection_rate=_opt(f.get("max_total_injection_rate",
                                                f.get("max_total_injection"))),
            max_total_oil_period=_opt(f.get("max_total_oil_period")),
            max_actions_per_step=(int(f["max_actions_per_step"])
                                  if f.get("max_actions_per_step") else None),
            compensation_min=_f(f.get("compensation_min"), 0.80),
            compensation_max=_f(f.get("compensation_max"), 1.20),
            allowed=list(a.get("allowed") or []),
            disabled=list(a.get("disabled") or []),
            source=str(p),
        )
        for item in (d.get("unavailable") or []):
            u = _parse_unavailable(item)
            if u:
                c.unavailable.append(u)
        return c

    # ------------------------------------------------------------------
    def load_events(self, path: str | Path) -> int:
        """
        Дочитывает файл событий: ремонты и прочая недоступность.

        Наш формат, пока не известен формат организаторов:
            42  2014-04-01  2014-06-30  ремонт
            7   2015-01-01  2015-02-28  замена НКТ
        Пустые строки и строки с # игнорируются. Разделитель — пробелы,
        точка с запятой или табуляция.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"нет файла событий: {p}")
        n = 0
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.split("#")[0].strip()
            if not line:
                continue
            parts = re.split(r"[;\t]+|\s{1,}", line)
            if len(parts) < 3:
                continue
            try:
                u = Unavailability(
                    well=parts[0].strip().strip("'\""),
                    start=date.fromisoformat(parts[1]),
                    end=date.fromisoformat(parts[2]),
                    reason=" ".join(parts[3:]) or "недоступна")
            except ValueError:
                continue
            self.unavailable.append(u)
            n += 1
        return n

    # --- уровень времени ----------------------------------------------
    def is_available(self, well: str, d: date) -> bool:
        return not any(u.well == well and u.covers(d) for u in self.unavailable)

    def blocked_in(self, well: str, a: date, b: date) -> Unavailability | None:
        """Мешает ли что-то воздействию на скважину в интервале [a, b]."""
        for u in self.unavailable:
            if u.well == well and u.overlaps(a, b):
                return u
        return None

    def available_wells(self, wells: list[str], d: date) -> list[str]:
        return [w for w in wells if self.is_available(w, d)]

    # --- уровень фонда: проверка набора действий -----------------------
    def check_actions(self, actions: list, horizon_end: date | None = None
                      ) -> list[Issue]:
        """
        Проверяет набор действий одного шага: число воздействий и попадание
        в период недоступности. actions — объекты с полями well и d (date).
        """
        issues: list[Issue] = []
        lim = self.max_actions_per_step
        if lim is not None and len(actions) > lim:
            issues.append(Issue(
                "error", "field", "шаг",
                f"воздействий {len(actions)} при лимите {lim}"))

        for a in actions:
            d = getattr(a, "d", None) or getattr(a, "date", None)
            if isinstance(d, str):
                d = date.fromisoformat(d)
            if d is None:
                continue
            end = horizon_end or d
            u = self.blocked_in(str(a.well), d, end)
            if u is not None:
                issues.append(Issue(
                    "error", "time", f"скв. {a.well} {d}",
                    f"воздействие в период недоступности ({u.reason}, "
                    f"{u.start}..{u.end})"))
        return issues

    # --- уровень фонда: проверка результата ----------------------------
    def check_field_rates(self, per_step: list[dict]) -> list[Issue]:
        """
        Проверяет суммарные показатели по фонду на каждом шаге.
        per_step — список словарей с ключами date, liquid_rate,
        injection_rate. Лимит None означает «не задано».
        """
        issues: list[Issue] = []
        checks = (("liquid_rate", self.max_total_liquid_rate,
                   "суммарный отбор жидкости"),
                  ("injection_rate", self.max_total_injection_rate,
                   "суммарная закачка"))
        for key, lim, label in checks:
            if lim is None:
                continue
            worst, worst_d = 0.0, None
            for row in per_step:
                v = float(row.get(key) or 0.0)
                if v > worst:
                    worst, worst_d = v, row.get("date")
            if worst > lim:
                # Дата пика идёт в текст: сообщение может печататься там,
                # где своей привязки к шагу нет, и без неё в отчёте
                # оказывалась первая дата периода вместо даты нарушения.
                issues.append(Issue(
                    "error", "field", str(worst_d),
                    f"{label} {worst:,.1f} > {lim:,.1f} м3/сут "
                    f"(пик {worst_d})"))
        return issues

    def check_field_totals(self, oil_kt: float) -> list[Issue]:
        if self.max_total_oil_period is None:
            return []
        if oil_kt > self.max_total_oil_period:
            return [Issue("error", "field", "период",
                          f"добыча нефти {oil_kt:,.1f} > квоты "
                          f"{self.max_total_oil_period:,.1f} тыс. т")]
        return []

    # ------------------------------------------------------------------
    def describe(self) -> str:
        def s(v, unit=""):
            return "не задано" if v is None else f"{v}{unit}"
        lines = [
            f"источник: {self.source}",
            "скважина:",
            f"  дебит жидкости не выше      {self.max_liquid_rate} м3/сут",
            f"  забойное добывающих от      {self.min_bhp_producer} бар",
            f"  забойное нагнетательных до  {self.max_bhp_injector} бар",
            f"  предел обводнённости        {self.economic_watercut_limit}",
            f"  интервал между воздействиями {self.min_interval_months} мес",
            "фонд:",
            f"  суммарный отбор жидкости    {s(self.max_total_liquid_rate, ' м3/сут')}",
            f"  суммарная закачка           {s(self.max_total_injection_rate, ' м3/сут')}",
            f"  квота на нефть за период    {s(self.max_total_oil_period, ' тыс. т')}",
            f"  воздействий за шаг          {s(self.max_actions_per_step)}",
            f"  компенсация закачки         {self.compensation_min}..{self.compensation_max}",
            f"действия: разрешено {len(self.allowed)}, отключено {len(self.disabled)}",
        ]
        if self.unavailable:
            lines.append(f"недоступность скважин: {len(self.unavailable)} записей")
            for u in self.unavailable[:5]:
                lines.append(f"  {u}")
        else:
            lines.append("недоступность скважин: не задана")
        return "\n".join(lines)


def _f(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _opt(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_unavailable(item) -> Unavailability | None:
    """Запись из YAML: {well: 42, from: 2014-04-01, to: 2014-06-30}."""
    if not isinstance(item, dict):
        return None
    try:
        return Unavailability(
            well=str(item.get("well")),
            start=_as_date(item.get("from") or item.get("start")),
            end=_as_date(item.get("to") or item.get("end")),
            reason=str(item.get("reason") or "недоступна"))
    except (ValueError, TypeError):
        return None


def _as_date(v) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


# ==========================================================================
# ПАРСЕР ВНЕШНЕГО ФАЙЛА ОГРАНИЧЕНИЙ
#
# Формат организаторами не показан и будет известен примерно за час до
# сдачи. Поэтому парсер устроен терпимо: он не требует конкретной
# структуры, а ищет знакомые сущности в любом текстовом виде —
# таблица, пары «ключ: значение», связный текст. Всё нераспознанное
# печатается списком, чтобы за оставшийся час дописать одно правило,
# а не писать парсер с нуля.
#
# Понимает:
#   таблица         42; 2014-04-01; 2014-06-30; ремонт
#   пары            max_total_liquid: 3000
#                   Квота на добычу нефти: 200 тыс. т
#   связный текст   Скважина 42 остановлена на ремонт с 01.04.2014 по 30.06.2014
#   даты            2014-04-01, 01.04.2014, 01/04/2014
# ==========================================================================
_DATE_RE = r"(\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{4})"

# (регулярное выражение, поле Constraints, множитель к значению)
_LIMIT_PATTERNS = [
    (r"(?:макс\w*\s+)?(?:суммарн\w*\s+)?(?:отбор|добыч\w*)\s+жидкост\w*",
     "max_total_liquid_rate", 1.0),
    (r"max[_ ]?total[_ ]?liquid(?:[_ ]?rate)?", "max_total_liquid_rate", 1.0),
    (r"(?:макс\w*\s+)?(?:суммарн\w*\s+)?закачк\w*", 
     "max_total_injection_rate", 1.0),
    (r"max[_ ]?total[_ ]?injection(?:[_ ]?rate)?",
     "max_total_injection_rate", 1.0),
    (r"квот\w*\s+(?:на\s+)?(?:добыч\w*\s+)?нефт\w*", "max_total_oil_period", 1.0),
    (r"max[_ ]?total[_ ]?oil", "max_total_oil_period", 1.0),
    (r"(?:макс\w*\s+)?(?:числ\w*|количеств\w*)\s+воздействи\w*",
     "max_actions_per_step", 1.0),
    (r"max[_ ]?actions[_ ]?per[_ ]?step", "max_actions_per_step", 1.0),
    (r"деби\w*\s+жидкост\w*\s+(?:по\s+)?скважин\w*", "max_liquid_rate", 1.0),
    (r"max[_ ]?liquid[_ ]?rate", "max_liquid_rate", 1.0),
    (r"забойн\w*[^:]*добывающ\w*", "min_bhp_producer", 1.0),
    (r"min[_ ]?bhp[_ ]?prod\w*", "min_bhp_producer", 1.0),
    (r"забойн\w*[^:]*нагнетательн\w*", "max_bhp_injector", 1.0),
    (r"max[_ ]?bhp[_ ]?inj\w*", "max_bhp_injector", 1.0),
]

_STOP_WORDS = r"(?:ремонт|останов\w*|недоступ\w*|простой|workover|shut\w*|"     r"замен\w*|обслуживани\w*)"


def _to_date(s: str) -> date | None:
    s = s.strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    m = re.match(r"^(\d{2})[./](\d{2})[./](\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        try:
            return date(int(y), int(mo), int(d))
        except ValueError:
            return None
    return None


def _to_number(s: str) -> float | None:
    m = re.search(r"[-+]?\d[\d\s ]*(?:[.,]\d+)?", s)
    if not m:
        return None
    txt = m.group(0).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


@dataclass
class ParseReport:
    limits: dict = dc_field(default_factory=dict)
    unavailable: list = dc_field(default_factory=list)
    unrecognized: list = dc_field(default_factory=list)
    lines_total: int = 0

    def summary(self) -> str:
        out = [f"строк прочитано: {self.lines_total}"]
        if self.limits:
            out.append("распознанные лимиты:")
            for k, v in self.limits.items():
                out.append(f"  {k} = {v}")
        else:
            out.append("лимитов не распознано")
        if self.unavailable:
            out.append(f"недоступность скважин: {len(self.unavailable)}")
            for u in self.unavailable[:8]:
                out.append(f"  {u}")
        if self.unrecognized:
            out.append(f"НЕ РАСПОЗНАНО строк: {len(self.unrecognized)}")
            for line in self.unrecognized[:10]:
                out.append(f"  ? {line}")
            out.append("  дописать правило в _LIMIT_PATTERNS или _STOP_WORDS")
        return "\n".join(out)


def parse_external(path: str | Path) -> ParseReport:
    """
    Разбирает файл ограничений неизвестной структуры.
    Ничего не применяет — только сообщает, что понято.
    """
    rep = ParseReport()
    text = Path(path).read_text(encoding="utf-8", errors="ignore")

    for raw in text.splitlines():
        line = raw.split("#")[0].split("--")[0].strip()
        if not line:
            continue
        rep.lines_total += 1
        if _try_unavailable(line, rep) or _try_limit(line, rep):
            continue
        # секции и заголовки без значений не считаем ошибкой
        if line.endswith(":") or re.match(r"^[\[<].*[\]>]$", line):
            continue
        # Заголовок таблицы и строка без единой цифры — не ограничение.
        if not re.search(r"\\d", line):
            continue
        rep.unrecognized.append(line[:70])
    return rep


def _try_unavailable(line: str, rep: ParseReport) -> bool:
    """Ремонты: и табличной строкой, и связным текстом."""
    dates = re.findall(_DATE_RE, line)
    if len(dates) < 2:
        return False
    a, b = _to_date(dates[0]), _to_date(dates[1])
    if not a or not b:
        return False

    # номер скважины: явное упоминание либо первое число строки
    m = re.search(r"(?:скв\w*|well)[\s.№:]*([A-Za-z0-9_-]+)", line, re.I)
    if m:
        well = m.group(1)
    else:
        head = re.split(r"[;\t,]|\s{2,}", line.strip())[0].strip()
        if not re.match(r"^[A-Za-z0-9_-]+$", head):
            return False
        well = head

    reason = "недоступна"
    m = re.search(_STOP_WORDS, line, re.I)
    if m:
        reason = m.group(0).lower()
    rep.unavailable.append(Unavailability(str(well), min(a, b), max(a, b), reason))
    return True


def _try_limit(line: str, rep: ParseReport) -> bool:
    key_part, sep, val_part = line.partition(":")
    if not sep:
        # «максимальный отбор жидкости 3000 м3/сут» — без двоеточия
        key_part, val_part = line, line
    val = _to_number(val_part)
    if val is None:
        return False
    for pattern, field_name, mult in _LIMIT_PATTERNS:
        if re.search(pattern, key_part, re.I):
            rep.limits[field_name] = (int(val) if field_name.startswith("max_actions")
                                      else val * mult)
            return True
    return False


def apply_external(c: "Constraints", path: str | Path,
                   verbose: bool = True) -> ParseReport:
    """Разбирает файл и применяет распознанное к набору ограничений."""
    rep = parse_external(path)
    for k, v in rep.limits.items():
        setattr(c, k, v)
    c.unavailable.extend(rep.unavailable)
    c.source = f"{c.source} + {path}"
    if verbose:
        print(rep.summary())
    return rep


# ==========================================================================
def _self_test() -> int:
    from datetime import date as D

    print("=" * 62)
    c = Constraints.load()
    print(c.describe())

    print("\n" + "=" * 62)
    print("проверка механизмов на синтетике:")
    c.max_actions_per_step = 2
    c.max_total_liquid_rate = 3000.0
    c.max_total_oil_period = 200.0
    c.unavailable.append(Unavailability("42", D(2014, 4, 1), D(2014, 6, 30),
                                        "ремонт"))

    class A:
        def __init__(self, well, d): self.well, self.d = well, d

    acts = [A("42", D(2014, 5, 1)), A("7", D(2014, 5, 1)), A("3", D(2014, 5, 1))]
    issues = c.check_actions(acts)
    for i in issues:
        print(f"  {i}")
    assert any(i.scope == "field" for i in issues), "лимит действий не сработал"
    assert any(i.scope == "time" for i in issues), "недоступность не сработала"

    print(f"  скв. 42 доступна 2014-05-01: {c.is_available('42', D(2014, 5, 1))}")
    print(f"  скв. 42 доступна 2014-07-01: {c.is_available('42', D(2014, 7, 1))}")
    assert not c.is_available("42", D(2014, 5, 1))
    assert c.is_available("42", D(2014, 7, 1))

    per_step = [{"date": D(2014, 5, 1), "liquid_rate": 2800.0},
                {"date": D(2014, 6, 1), "liquid_rate": 3200.0}]
    for i in c.check_field_rates(per_step):
        print(f"  {i}")
    assert c.check_field_rates(per_step), "лимит по фонду не сработал"

    for i in c.check_field_totals(241.6):
        print(f"  {i}")
    assert c.check_field_totals(241.6), "квота на нефть не сработала"
    assert not c.check_field_totals(150.0)

    print("\n  доступные из [42, 7, 3] на 2014-05-01:",
          c.available_wells(["42", "7", "3"], D(2014, 5, 1)))

    print("=" * 62)
    print("ВСЕ МЕХАНИЗМЫ РАБОТАЮТ")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        sys.exit(_self_test())
    if len(sys.argv) > 2 and sys.argv[1] == "--external":
        c = Constraints.load()
        print("=" * 62)
        r = apply_external(c, sys.argv[2])
        print("=" * 62)
        print(c.describe())
        sys.exit(1 if r.unrecognized else 0)
    print(Constraints.load().describe())
