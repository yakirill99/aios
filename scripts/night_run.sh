#!/usr/bin/env bash
# =========================================================================
# НОЧНОЙ ПРОГОН — два этапа без присмотра.
#
#   Этап 1: single по ВСЕМ скважинам — вклад каждой по отдельности
#   Этап 2: pairs по победителям этапа 1 — взаимодействия
#
# Второй этап опирается на результаты первого, поэтому запускать надо
# именно так, а не двумя командами вручную.
#
#   ./night_run.sh                  оба этапа
#   ./night_run.sh --hours 8        ограничить время (по умолчанию 9)
#
# Ничего не удаляет: посчитанное пропускается.
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

# --- ЗАЩИТА ОТ ПОВТОРНОГО ЗАПУСКА --------------------------------------
# Два экземпляра поднимут вдвое больше контейнеров, память кончится и
# прогоны начнут падать по OOM.
LOCK="$DATA_ROOT/.$(basename "$0").lock"
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    echo "${R}уже запущен: PID $(cat "$LOCK")${N}"
    echo "  остановить:  pkill -f $(basename "$0")"
    exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

HOURS=9
TOP_WELLS=8
while [ $# -gt 0 ]; do
    case "$1" in
        --hours) HOURS="$2"; shift 2 ;;
        --top)   TOP_WELLS="$2"; shift 2 ;;
        *) shift ;;
    esac
done

STAGE1="$DATA_ROOT/candidates_single"
STAGE2="$DATA_ROOT/candidates_pairs"
LOG="$LOGS_DIR/night_$(date +%Y%m%d_%H%M%S).log"
DEADLINE=$(( $(date +%s) + HOURS * 3600 ))

{
echo "======================================================================"
echo "НОЧНОЙ ПРОГОН   старт $(date '+%F %T')   лимит ${HOURS} ч"
echo "======================================================================"
disk_status; mem_status
echo

# ---------------------------------------------------------------- этап 1
echo "${B}ЭТАП 1: вклад каждой скважины по отдельности${N}"
CANDS_DIR="$STAGE1" python3 "$SCRIPT_DIR/candidates.py" make \
    --mode single --prod 33 --inj 16 \
    --levels 0.6,0.8,1.0,1.25,1.5 --limit 400 \
    --out "$STAGE1" || exit 1

CANDS_DIR="$STAGE1" "$SCRIPT_DIR/run_candidates.sh" || true

echo
echo "${B}Ранжирование этапа 1${N}"
python3 "$SCRIPT_DIR/candidates.py" rank --runs "$STAGE1" --top 25

# --- выбираем победителей: скважины с лучшей дельтой -------------------
BEST_WELLS="$(python3 - "$STAGE1/ranking.json" "$TOP_WELLS" <<'PY'
import json, re, sys
rank = json.load(open(sys.argv[1], encoding='utf-8'))
top_n = int(sys.argv[2])
seen, out = set(), []
for r in rank:
    if r.get("delta") is None or r["delta"] <= 0:
        continue
    m = re.match(r"^(\w+?)_x", r["tag"])
    if not m:
        continue
    w = m.group(1)
    if w not in seen:
        seen.add(w); out.append(w)
    if len(out) >= top_n:
        break
print(",".join(out))
PY
)"

if [ -z "$BEST_WELLS" ]; then
    echo "${Y}ни один одиночный вариант не превзошёл планку —"
    echo "второй этап пропускаю, нужен другой набор уровней${N}"
    exit 0
fi
echo
echo "  победители этапа 1: $BEST_WELLS"

NOW=$(date +%s)
LEFT=$(( (DEADLINE - NOW) / 60 ))
if [ "$LEFT" -lt 60 ]; then
    echo "${Y}осталось ${LEFT} мин — на второй этап не хватит, останавливаюсь${N}"
    exit 0
fi
echo "  на второй этап остаётся ${LEFT} мин"

# ---------------------------------------------------------------- этап 2
echo
echo "${B}ЭТАП 2: взаимодействия между победителями${N}"
LIMIT=$(( LEFT * 60 * 6 / 180 ))          # ~180 с на прогон, 6 процессов
[ "$LIMIT" -gt 1200 ] && LIMIT=1200
echo "  бюджет: $LIMIT кандидатов"

python3 "$SCRIPT_DIR/candidates.py" make \
    --mode pairs --only-wells "$BEST_WELLS" \
    --levels 0.7,1.0,1.3 --limit "$LIMIT" \
    --out "$STAGE2" || exit 1

CANDS_DIR="$STAGE2" "$SCRIPT_DIR/run_candidates.sh" || true

echo
echo "${B}Ранжирование этапа 2${N}"
python3 "$SCRIPT_DIR/candidates.py" rank --runs "$STAGE2" --top 25

echo
echo "======================================================================"
echo "ГОТОВО  $(date '+%F %T')"
echo "  этап 1: $STAGE1/ranking.json"
echo "  этап 2: $STAGE2/ranking.json"
echo "======================================================================"
disk_status
} 2>&1 | tee "$LOG"

echo "лог: $LOG"
