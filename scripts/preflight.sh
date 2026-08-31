#!/usr/bin/env bash
# =========================================================================
# ПРЕДПОЛЁТНАЯ ПРОВЕРКА. Прогоняет весь контур и печатает сводку.
#
#   ./preflight.sh              быстрые проверки, ~2 минуты
#   ./preflight.sh --full       + прогоны симулятора и тест на круг, ~20 мин
#   ./preflight.sh --llm        + проверка модели (нужен ollama)
#   ./preflight.sh --full --llm всё вместе
#
# Ничего не удаляет и ничего не пересчитывает поверх существующего.
# Код возврата: 0 — всё зелёное, 1 — есть красное.
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

FULL=0; LLM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --full) FULL=1; shift ;;
    --llm)  LLM=1; shift ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "неизвестный аргумент: $1"; exit 2 ;;
  esac
done

PASS=0; FAIL=0; WARN=0
T0=$(date +%s)
ok()   { printf "  ${G}✓${N} %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  ${R}✗${N} %s\n" "$1"; [ $# -gt 1 ] && printf "      %s\n" "$2"; FAIL=$((FAIL+1)); }
warn() { printf "  ${Y}!${N} %s\n" "$1"; [ $# -gt 1 ] && printf "      %s\n" "$2"; WARN=$((WARN+1)); }
head_() { printf "\n${B}%s${N}\n" "$1"; }

echo "${B}ПРЕДПОЛЁТНАЯ ПРОВЕРКА${N}   $(date '+%F %T')"
echo "  корень:  ${AIOS_ROOT/#$HOME/\~}"
echo "  данные:  ${DATA_ROOT/#$HOME/\~}"
echo "  режимы:  full=${FULL} llm=${LLM}"

# =========================================================================
head_ "1. Окружение"

CORES=$(nproc --all)      # nproc без --all уважает OMP_NUM_THREADS
if [ "$CORES" -ge 6 ]; then ok "ядер: $CORES"
elif [ "$CORES" -ge 3 ]; then warn "ядер: $CORES" "мало для параллельных прогонов, проверьте .wslconfig"
else bad "ядер: $CORES" "прогоны пойдут по одному; processors= в %USERPROFILE%\\.wslconfig"; fi

RAM=$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)
[ "$RAM" -ge 12 ] && ok "память: ${RAM} ГБ" || warn "память: ${RAM} ГБ" "мало для параллельности"

FREE=$(free_gb)
[ "$FREE" -gt "$MIN_FREE_GB" ] && ok "диск: ${FREE} ГБ свободно" \
    || bad "диск: ${FREE} ГБ" "порог ${MIN_FREE_GB} ГБ, освободите место"

echo "      параллельность по стражу: $(safe_parallel 99)"

# =========================================================================
head_ "2. Инструменты"

if docker info >/dev/null 2>&1; then ok "docker отвечает"
else bad "docker не отвечает" "включите WSL Integration для этой среды"; fi

if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    SZ=$(docker image inspect "$IMAGE" --format '{{.Size}}' | awk '{printf "%.1f ГБ", $1/1073741824}')
    ok "образ $IMAGE ($SZ)"
else
    bad "нет образа $IMAGE" "docker load < opm-2026.04.tgz"
fi

V=$(docker run --rm "$IMAGE" flow --version 2>/dev/null | head -1)
[ -n "$V" ] && ok "симулятор в контейнере: $V" || bad "flow в контейнере не запускается"

if docker run --rm "$IMAGE" python3 -c "import resdata" 2>/dev/null; then
    ok "resdata в контейнере"
else
    bad "нет resdata в контейнере" "мост UNSMRY→CSV не заработает"
fi

command -v flow >/dev/null && ok "нативный flow: $(flow --version 2>/dev/null | head -1)" \
    || warn "нативного flow нет" "не обязателен, всё идёт через контейнер"

python3 -c "import pydantic" 2>/dev/null \
    && ok "pydantic $(python3 -c 'import pydantic;print(pydantic.VERSION)')" \
    || bad "нет pydantic" "pip install pydantic --break-system-packages"

# =========================================================================
head_ "3. Данные и код"

[ -f "$CHDD_PATH/chdd_model.py" ] && ok "эталон ЧДД на месте" \
    || bad "нет $CHDD_PATH/chdd_model.py"

EV=$(python3 -c "
import sys; sys.path.insert(0,'$SCRIPT_DIR')
import npv_official as n; print(n.VERSION)" 2>/dev/null)
[ -n "$EV" ] && ok "эталон импортируется: $EV" || bad "эталон не импортируется"

for d in "$MODEL_DIR" "$MODEL_BASE"; do
    if find "$d" -maxdepth 1 -iname '*.DATA' 2>/dev/null | grep -q .; then
        ok "дека: ${d/#$HOME/\~}"
    else
        bad "нет деки: ${d/#$HOME/\~}"
    fi
done

for f in rolling_horizon.py agents.py llm_client.py validate_output.py \
         summary_to_csv.py npv_official.py schedule_builder.py; do
    [ -f "$SCRIPT_DIR/$f" ] || bad "нет скрипта $f"
done
python3 -c "
import ast,sys
for f in ['rolling_horizon.py','agents.py','llm_client.py','validate_output.py',
          'summary_to_csv.py','npv_official.py']:
    ast.parse(open('$SCRIPT_DIR/'+f,encoding='utf-8').read())
" 2>/dev/null && ok "все скрипты синтаксически валидны" || bad "ошибка синтаксиса в скриптах"

# версия кода: маркеры правок после ревью
M=$(grep -c "SUBMISSION_ASSUMPTIONS\|rank_candidates\|accepted_tag\|_log_skip\|tie_eps_mln" \
    "$SCRIPT_DIR/rolling_horizon.py" 2>/dev/null || echo 0)
[ "$M" -ge 5 ] && ok "rolling_horizon свежий ($M маркеров правок)" \
    || bad "rolling_horizon старой версии" "маркеров: $M, ожидается ≥5"

grep -q "hypothesis_check" "$SCRIPT_DIR/agents.py" 2>/dev/null \
    && ok "agents.py свежий (проверка гипотез)" \
    || bad "agents.py старой версии" "нет поля hypothesis_check"

grep -q "AIOS_LLM_HOST" "$SCRIPT_DIR/llm_client.py" 2>/dev/null \
    && ok "llm_client свежий (настраиваемый адрес)" \
    || warn "llm_client старой версии" "адрес ollama не переопределяется"

# =========================================================================
head_ "4. Git"

BR=$(git -C "$AIOS_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)
SH=$(git -C "$AIOS_ROOT" rev-parse --short HEAD 2>/dev/null)
[ -n "$BR" ] && ok "ветка $BR @ $SH" || warn "не репозиторий git"
if [ -n "$BR" ]; then
    DIRTY=$(git -C "$AIOS_ROOT" status --porcelain | wc -l)
    [ "$DIRTY" -eq 0 ] && ok "рабочее дерево чистое" \
        || warn "незакоммиченных изменений: $DIRTY" "git status"
fi

# =========================================================================
head_ "5. Планка"

BJ="$RUNS_DIR/baseline/baseline_npv.json"
if [ -f "$BJ" ]; then
    python3 - "$BJ" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
npv=d["npv_mln_rub"]; years=d.get("years",[])
tot=sum(int(y.get("months") or 0) for y in years)
last=[int(y.get("months") or 0) for y in years if int(y.get("year",0))==2015]
G,R,N="\033[32m","\033[31m","\033[0m"
print(f"  {G}✓{N} планка {npv:,.3f} млн руб")
bad=[]
if abs(npv-1023.355)>0.01: bad.append(f"ожидается 1 023,355, расхождение {npv-1023.355:+.3f}")
if tot!=23: bad.append(f"месяцев {tot}, ожидается 23")
if last and last[0]!=11: bad.append(f"2015: {last[0]} мес, ожидается 11")
for b in bad: print(f"  {R}✗{N} {b}")
sys.exit(1 if bad else 0)
PY
    if [ $? -eq 0 ]; then PASS=$((PASS+1)); ok "раскладка 12 + 11 = 23"
    else FAIL=$((FAIL+1)); fi
else
    bad "нет планки" "./prepare_baseline.sh $CUT_DATE $END_YEAR"
fi

# =========================================================================
head_ "6. Физика планки"

if has_result "$RUNS_DIR/baseline"; then
    SM=$(find "$RUNS_DIR/baseline" -name '*.SMSPEC' | head -1)
    OUT=$(run_py validate_output.py "$RUNS_DIR/baseline" "/out/$(basename "$SM")" \
          --since "$CUT_DATE" 2>&1)
    RC=$?
    COMP=$(echo "$OUT" | grep -oP 'компенсация закачки: \K[0-9.]+' | tail -1)
    if [ "$RC" -eq 0 ]; then
        ok "физика чиста на всём горизонте и на управляющем периоде"
        [ -n "$COMP" ] && echo "      компенсация закачки: $COMP"
    else
        bad "есть нарушения физики" "$(echo "$OUT" | grep -A3 'ОШИБОК' | head -4)"
    fi
else
    warn "прогон планки не найден" "проверка физики пропущена"
fi

# =========================================================================
head_ "7. Конфигурация цикла"

PLAN=$(python3 -u "$SCRIPT_DIR/rolling_horizon.py" plan \
         --base "$MODEL_DIR" --matrix "$PROBES_DIR/response_matrix.csv" 2>&1)
echo "$PLAN" | sed 's/^/      /'
echo "$PLAN" | grep -q "по 3 мес" && ok "шаг управления 3 мес" \
    || bad "шаг управления не 3 мес" "умолчания CLI разошлись с Config"
echo "$PLAN" | grep -q "до 25" && ok "кандидатов на шаг 25" \
    || warn "кандидатов на шаг не 25"
echo "$PLAN" | grep -q "загружена" && ok "матрица влияния загружена" \
    || warn "матрицы влияния нет" "цикл пойдёт без фильтра связей"

# =========================================================================
head_ "8. Агентный слой (режим off)"

if AIOS_LLM_MODE=off python3 "$SCRIPT_DIR/agents.py" test >/tmp/agents.out 2>&1; then
    ok "три роли работают без модели"
    grep -q "значение из расчёта" /tmp/agents.out \
        && ok "фильтр выдуманных величин срабатывает" \
        || bad "фильтр величин не сработал"
else
    bad "agents.py test упал" "$(tail -3 /tmp/agents.out)"
fi

# =========================================================================
if [ "$LLM" = "1" ]; then
head_ "9. Языковая модель"

# AIOS_LLM_MODE=off в окружении перебивает аргумент конструктора,
# поэтому на время проверки режим форсируется.
AIOS_LLM_MODE=live python3 - <<'PY'
import sys, time
sys.path.insert(0, ".")
from llm_client import LLMClient
G,R,Y,N="\033[32m","\033[31m","\033[33m","\033[0m"
c = LLMClient(mode="live")
ok_, msg = c.health()
print(f"  {G+'✓' if ok_ else R+'✗'}{N} {msg}")
if not ok_: sys.exit(1)
t=time.time(); wok,wmsg = c.warmup()
print(f"  {G+'✓' if wok else R+'✗'}{N} прогрев: {wmsg}")
if not wok: sys.exit(1)

from pydantic import BaseModel, Field
from typing import Literal
class Probe(BaseModel):
    well: str = Field(description="номер скважины")
    control: Literal["LRAT","RATE","SHUT"]
    level: Literal[0.7,1.25,1.75,2.25,3.0]
    reason: str

t=time.time()
r1=c.complete("Скважина 42, есть запас по отбору. Предложи одно воздействие.", Probe)
dt=time.time()-t
if r1 is None:
    print(f"  {R}✗{N} структурированный вызов не удался за {dt:.0f} с"); sys.exit(1)
mark = G+"✓" if dt < 60 else Y+"!"
print(f"  {mark}{N} структурированный вызов: {dt:.1f} с  ({r1.well} {r1.control} x{r1.level})")

r2=c.complete("Скважина 42, есть запас по отбору. Предложи одно воздействие.", Probe)
same=(r1.well,r1.control,r1.level)==(r2.well,r2.control,r2.level)
print(f"  {G+'✓' if same else R+'✗'}{N} детерминизм из кэша: {'совпало' if same else 'РАЗОШЛОСЬ'}")
print(f"      {c.stats()}")
sys.exit(0 if same else 1)
PY
[ $? -eq 0 ] && { ok "модель отвечает и воспроизводима"; } || bad "проверка модели не пройдена"

CACHE=$(ls "$DATA_ROOT/llm_cache"/*.json 2>/dev/null | wc -l)
[ "$CACHE" -gt 0 ] && ok "кэш модели: $CACHE ответов" \
    || warn "кэш модели пуст" "режим replay пока не воспроизведёт прогон"
fi

# =========================================================================
if [ "$FULL" = "1" ]; then
head_ "10. Короткий цикл и тест на круг"

WD="$DATA_ROOT/preflight"
rm -rf "$WD"
echo "  запускаю цикл на одном шаге, ~5-10 мин..."
if python3 -u "$SCRIPT_DIR/rolling_horizon.py" run \
        --base "$MODEL_DIR" --matrix "$PROBES_DIR/response_matrix.csv" \
        --workdir "$WD" --end 2014-04-01 --llm off \
        --parallel "$(safe_parallel 4)" > "$LOGS_DIR/preflight_run.log" 2>&1; then
    NPV=$(python3 -c "
import json;print(json.load(open('$WD/result.json'))['npv'])" 2>/dev/null)
    ok "цикл отработал, ЧДД окна: ${NPV:-?}"
else
    bad "цикл упал" "см. $LOGS_DIR/preflight_run.log"
fi

# паспорт прогона
python3 - "$WD/result.json" <<'PY' 2>/dev/null
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
need=["assumptions","cut","end","levels","step_months","llm_mode"]
miss=[k for k in need if k not in d]
G,R,N="\033[32m","\033[31m","\033[0m"
if miss: print(f"  {R}✗{N} в result.json нет полей: {', '.join(miss)}"); sys.exit(1)
print(f"  {G}✓{N} паспорт прогона полон (допущения: {d['assumptions']})")
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

# тест на круг: сдаваемый .inc в чистой копии деки
INC="$WD/wells_schedule.inc"
if [ -f "$INC" ]; then
    RT="$DATA_ROOT/preflight_roundtrip"
    rm -rf "$RT"; mkdir -p "$RT"
    cp -r "$MODEL_DIR" "$RT/deck"
    cp "$INC" "$RT/deck/INCLUDE/"
    ok "расписание собрано: $(grep -c WCON "$INC" 2>/dev/null) строк управления"
    warn "полный тест на круг" "запустите ./run_schedule.sh с MODEL_DIR=$RT/deck и сверьте ЧДД"
else
    bad "нет wells_schedule.inc после цикла"
fi
fi

# =========================================================================
head_ "ИТОГ"
DT=$(( $(date +%s) - T0 ))
printf "  ${G}пройдено %d${N}, ${Y}предупреждений %d${N}, ${R}провалено %d${N}   за %d с\n" \
    "$PASS" "$WARN" "$FAIL" "$DT"
if [ "$FAIL" -eq 0 ]; then
    echo "  ${G}контур готов к работе${N}"
    [ "$FULL" = "0" ] && echo "  для полной проверки: ./preflight.sh --full --llm"
    exit 0
else
    echo "  ${R}есть красное — разберитесь до запуска боевого цикла${N}"
    exit 1
fi
