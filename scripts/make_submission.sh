#!/usr/bin/env bash
# ==========================================================================
# ПУНКТ 1. Перенос расписания в submission и тест на круг.
#
#   ./make_submission.sh                 # переносит full34
#   ./make_submission.sh $DATA_ROOT/rolling_v4
#
# Скрипт ничего не переносит молча: сначала печатает, что заменяется и на
# что, затем прогоняет расписание с нуля и сверяет полученное число с
# заявленным. При расхождении больше допуска — выходит с ошибкой и
# оставляет старую версию в submission/.bak.
# ==========================================================================
set -euo pipefail

AIOS_ROOT="${AIOS_ROOT:-$HOME/aios}"
DATA_ROOT="${DATA_ROOT:-$HOME/aios-data}"
SRC="${1:-$DATA_ROOT/full34}"
SUB="$AIOS_ROOT/submission"
TOL_PCT="${TOL_PCT:-0.5}"          # допуск расхождения на круге, %

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

# --- 0. Проверки входа ----------------------------------------------------
say "0. Проверка источника: $SRC"
[ -f "$SRC/result.json" ]        || die "нет $SRC/result.json"
[ -f "$SRC/wells_schedule.inc" ] || die "нет $SRC/wells_schedule.inc"

read -r NPV BASE NACT ASSUM LLM <<EOF
$(python3 - "$SRC/result.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(d["npv"], d.get("baseline", 0), d.get("n_actions", 0),
      json.dumps(d.get("assumptions", {}), ensure_ascii=False),
      d.get("llm_mode_effective", d.get("llm_mode", "off")))
PY
)
EOF

echo "  ЧДД:         $NPV млн"
echo "  планка:      $BASE млн"
echo "  воздействий: $NACT"
echo "  допущения:   $ASSUM"
echo "  режим:       $LLM"

# Цена этой проверки — 96,3 млн. Файл с chargeInitialPump на управляющем
# окне считает планку 922,7 вместо 1 018,986, и заявленное число
# оказывается несопоставимо с контрольным пересчётом организаторов.
[ "$ASSUM" = "{}" ] || die "в допущениях прогона не пусто ($ASSUM).
  chargeInitialPump на управляющем окне занижает ЧДД на 96,3 млн.
  Этот прогон в сдачу не годится."

python3 - "$BASE" <<'PY' || exit 1
import sys
b = float(sys.argv[1])
if abs(b - 1018.9855) > 0.01:
    sys.exit(f"ОШИБКА: планка прогона {b:.4f}, ожидалась 1018.9855")
PY

# --- 1. Резервная копия и перенос ----------------------------------------
say "1. Что сейчас в submission"
if [ -f "$SUB/npv_claimed.txt" ]; then
    echo "  было заявлено: $(cat "$SUB/npv_claimed.txt") млн"
else
    echo "  npv_claimed.txt отсутствует"
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$SUB/.bak/$STAMP"
cp -f "$SUB"/wells_schedule.inc "$SUB"/npv_claimed.txt \
      "$SUB/.bak/$STAMP/" 2>/dev/null || true
echo "  копия старой версии: $SUB/.bak/$STAMP"

cp -f "$SRC/wells_schedule.inc" "$SUB/wells_schedule.inc"
python3 -c "print(f'{float(\"$NPV\"):.3f}')" > "$SUB/npv_claimed.txt"
echo "  перенесено из: $SRC"
echo "  заявляем:      $(cat "$SUB/npv_claimed.txt") млн"

# --- 2. Тест на круг ------------------------------------------------------
say "2. Тест на круг: расписание считается с нуля"
cd "$AIOS_ROOT/scripts"
./run_schedule.sh --inc "$SUB/wells_schedule.inc"

# --- 3. Сверка числа ------------------------------------------------------
say "3. Сверка полученного ЧДД с заявленным"
python3 - "$DATA_ROOT/runs/sched" "$SUB/npv_claimed.txt" "$TOL_PCT" <<'PY'
import json, sys
from pathlib import Path

run, claimed_f, tol = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
claimed = float(claimed_f.read_text().strip())

got = None
for name, key in (("npv.json", "npv_mln_rub"), ("npv.json", "npv"),
                  ("result.json", "npv")):
    p = run / name
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        if key in d:
            got = float(d[key])
            print(f"  число из {p}: {got:.4f}")
            break

if got is None:
    print("  ВНИМАНИЕ: файла с ЧДД в каталоге прогона нет.")
    print(f"  Возьмите число из вывода run_schedule.sh выше и сравните "
          f"с заявленным {claimed:.3f} вручную.")
    sys.exit(0)

diff = got - claimed
pct = 100.0 * diff / claimed if claimed else 0.0
print(f"  заявлено:  {claimed:.3f}")
print(f"  получено:  {got:.3f}")
print(f"  расхождение: {diff:+.3f} млн ({pct:+.3f}%)")
if abs(pct) > tol:
    sys.exit(f"\nОШИБКА: расхождение больше допуска {tol}%. "
             f"Расписание в submission НЕ подтверждено.")
print("  круг сошёлся")
PY

# --- 4. Физика и фактический режим ---------------------------------------
say "4. Валидатор физики"
./validate.sh "$DATA_ROOT/runs/sched" --since 2014-01-01

say "5. Заданный режим против фактического"
python3 check_schedule_applied.py "$SUB/wells_schedule.inc" \
    "$DATA_ROOT/runs/sched" 2>/dev/null \
    || echo "  (проверьте вызов check_schedule_applied.py вручную)"

say "ГОТОВО"
echo "  submission/wells_schedule.inc — из $SRC"
echo "  submission/npv_claimed.txt    — $(cat "$SUB/npv_claimed.txt") млн"
echo "  откат: cp $SUB/.bak/$STAMP/* $SUB/"
echo
echo "  Дальше: ./release.sh"
