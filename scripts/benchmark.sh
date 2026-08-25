#!/usr/bin/env bash
# =========================================================================
# ЗАМЕР. Отвечает на три вопроса, без которых нельзя планировать:
#   1) сколько длится один прогон
#   2) сколько параллельных процессов имеет смысл (где насыщение)
#   3) сколько места ест один прогон
#
#   ./benchmark.sh              полный замер
#   ./benchmark.sh 5            только 5 параллельных (быстрая проверка)
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

ONLY_N="${1:-}"
OUT="$DATA_ROOT/bench"
require_model "$MODEL_DIR" || exit 1
DECK="$(deck_name "$MODEL_DIR")"

mkdir -p "$OUT" "$DATA_ROOT/logs"
echo "${B}Модель:${N} $MODEL_DIR/$DECK"
disk_status
disk_guard || exit 1

# --- пробные прогоны не должны писать снимки ----------------------------
BENCH_DECK="$DATA_ROOT/bench_deck"
rm -rf "$BENCH_DECK"; cp -r "$MODEL_DIR" "$BENCH_DECK"
SCH="$(find "$BENCH_DECK/INCLUDE" -iname '*sch*.inc' | head -1)"
if [ -n "$SCH" ]; then
    sed -i -E "s/^(RPTRST)/--\1/; s/^( *'BASIC=[0-9]'.*)/--\1/" "$SCH"
    echo "  RPTRST отключён — пробные прогоны пишут только сводки"
fi

# =========================================================================
if [ -z "$ONLY_N" ]; then
echo
echo "${B}1. Одиночный прогон: сколько потоков имеет смысл${N}"
printf "  %-9s %-10s %-12s %s\n" "потоков" "время,с" "память,ГБ" "ускорение"
BASE=""
for t in 1 2 4; do
    disk_guard || exit 1
    d="$OUT/single_t$t"; rm -rf "$d"
    run_flow "$BENCH_DECK" "$DECK" "$d" "$t"
    e="$(elapsed_of "$d")"; m="$(maxrss_of "$d")"
    [ -z "$BASE" ] && BASE="$e"
    printf "  %-9s %-10s %-12s %.2fx\n" "$t" "$e" \
        "$(awk "BEGIN{printf \"%.2f\", $m/1048576}")" \
        "$(awk "BEGIN{printf \"%.2f\", $BASE/$e}")"
done
fi

# =========================================================================
echo
echo "${B}2. Параллельно: где насыщение по пропускной способности${N}"
LEVELS="${ONLY_N:-2 4 6 8}"
printf "  %-12s %-12s %-14s %s\n" "процессов" "всего,с" "на прогон,с" "прогонов/час"
BEST_N=1; BEST_TP=0
for n in $LEVELS; do
    disk_guard || exit 1
    start=$(date +%s)
    for i in $(seq 1 "$n"); do
        d="$OUT/par${n}_$i"; rm -rf "$d"
        run_flow "$BENCH_DECK" "$DECK" "$d" 2 &
    done
    wait
    el=$(( $(date +%s) - start ))
    [ "$el" -eq 0 ] && el=1
    per=$(awk "BEGIN{printf \"%.1f\", $el/$n}")
    tph=$(awk "BEGIN{printf \"%.0f\", $n*3600/$el}")
    printf "  %-12s %-12s %-14s %s\n" "$n" "$el" "$per" "$tph"
    if [ "$(awk "BEGIN{print ($tph > $BEST_TP)}")" = "1" ]; then
        BEST_TP="$tph"; BEST_N="$n"
    fi
    # чистим сразу, чтобы не копить
    rm -rf "$OUT"/par${n}_*
done

# =========================================================================
echo
echo "${B}3. Место на прогон${N}"
d="$OUT/single_t2"; [ -d "$d" ] || d="$(ls -d "$OUT"/* | head -1)"
SZ_KB="$(du -sk "$d" 2>/dev/null | cut -f1)"
SZ_MB=$((SZ_KB / 1024))
echo "  без снимков: ${SZ_MB} МБ на прогон"
ls -lhS "$d" 2>/dev/null | head -6 | sed 's/^/    /'

# =========================================================================
echo
echo "${B}ВЫВОДЫ${N}"
T1="$(elapsed_of "$OUT/single_t2" 2>/dev/null || echo 0)"
[ -z "$T1" ] && T1=0
python3 - "$T1" "$BEST_N" "$BEST_TP" "$SZ_MB" "$(free_gb)" "$MIN_FREE_GB" <<'PY'
import sys
t, n, tph, mb, free, minfree = (float(x) for x in sys.argv[1:7])
print(f"  один прогон:            {t:.0f} с ({t/60:.1f} мин)")
print(f"  оптимум параллельности: {int(n)} процессов -> {int(tph)} прогонов/час")
print()
for label, runs in (("кампания 840 прогонов", 840), ("ночь (10 ч)", int(tph*10))):
    if label.startswith("кампания"):
        print(f"  {label}: {runs/tph:.1f} ч")
    else:
        print(f"  {label}: ~{runs} прогонов")
print()
budget = free - minfree
print(f"  место: {mb:.0f} МБ/прогон, доступно {budget:.0f} ГБ до порога")
print(f"  -> хватит на {budget*1024/max(mb,1):.0f} прогонов без чистки")
if mb > 100:
    print("  ! много на прогон — проверьте, что RPTRST отключён")
PY
echo
disk_status
rm -rf "$BENCH_DECK"
