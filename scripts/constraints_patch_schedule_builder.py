# -*- coding: utf-8 -*-
"""
Встраивание внешних ограничений в ScheduleBuilder.validate().

Запускать из каталога scripts:
    python3 constraints_patch_schedule_builder.py

Добавляет две проверки: воздействие в период недоступности скважины и
превышение лимита воздействий. Обе — уровня error, то есть кандидат
отбраковывается до генерации файла.
"""
import pathlib
import shutil

p = pathlib.Path("schedule_builder.py")
s = p.read_text(encoding="utf-8")

if "внешние ограничения" in s:
    print("правка уже применена"); raise SystemExit(0)

anchor = '''        issues: list[ValidationIssue] = []
        seen: dict[tuple[str, str], int] = {}'''
assert s.count(anchor) == 1, "не найден якорь в validate() — примените вручную"

new = '''        issues: list[ValidationIssue] = []
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
                        f"{u.start}..{u.end}"))'''

shutil.copy(p, p.with_suffix(".py.bak"))
p.write_text(s.replace(anchor, new), encoding="utf-8")

import ast
ast.parse(p.read_text(encoding="utf-8"))
print("schedule_builder.py: проверки добавлены, копия в schedule_builder.py.bak")
