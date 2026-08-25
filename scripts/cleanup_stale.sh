#!/usr/bin/env bash
# =========================================================================
# Уборка результатов, посчитанных от СТАРОЙ даты отсечки (2014-01-01).
# После смены точки передачи управления на 2015-12-01 они невалидны:
# все Δ ЧДД в них считались от неверной планки.
#
#   ./cleanup_stale.sh          показать, что будет удалено
#   ./cleanup_stale.sh --yes    удалить
#   ./cleanup_stale.sh --archive  перенести в ARCHIVE_DIR вместо удаления
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

MODE="${1:---dry}"

# каталоги от старой даты
STALE=(
  "$DATA_ROOT/candidates_single"
  "$DATA_ROOT/candidates_pairs"
  "$DATA_ROOT/candidates_full"
  "$DATA_ROOT/candidates_high"
  "$DATA_ROOT/candidates"
  "$DATA_ROOT/rolling"
  "$DATA_ROOT/runs/history"
  "$DATA_ROOT/runs/sched"
  "$DATA_ROOT/bench"
)

# что НЕ трогаем ни при каких условиях
KEEP=(
  "$DATA_ROOT/runs/history_full   полная история 2007-2015, сверена с эталоном"
  "$DATA_ROOT/runs/baseline       планка от 2015-12-01"
  "$DATA_ROOT/probes              матрица влияния (пересобрать, но пока нужна)"
  "$DATA_ROOT/logs                журналы прогонов"
)

echo "${B}Уборка устаревших результатов${N}"
echo "  причина: дата отсечки изменена 2014-01-01 -> ${CUT_DATE}"
echo
disk_status
echo
echo "${B}Будет удалено:${N}"
TOTAL=0
for d in "${STALE[@]}"; do
    [ -d "$d" ] || continue
    sz=$(du -sm "$d" 2>/dev/null | cut -f1)
    TOTAL=$((TOTAL + sz))
    printf "  %-42s %6s МБ\n" "${d/#$HOME/\~}" "$sz"
done
echo "  ${B}итого: $((TOTAL/1024)) ГБ${N}"
echo
echo "${B}Сохраняется:${N}"
for k in "${KEEP[@]}"; do echo "  ${G}+${N} ${k/#$HOME/\~}"; done

case "$MODE" in
  --yes)
    echo
    for d in "${STALE[@]}"; do
        [ -d "$d" ] || continue
        rm -rf "$d" && echo "  удалено: ${d/#$HOME/\~}"
    done
    echo
    disk_status
    ;;
  --archive)
    [ -n "$ARCHIVE_DIR" ] || { echo "${R}задайте ARCHIVE_DIR${N}"; exit 1; }
    mkdir -p "$ARCHIVE_DIR/stale_cut2014"
    echo
    for d in "${STALE[@]}"; do
        [ -d "$d" ] || continue
        # в архив только ranking и манифесты, прогоны не нужны
        n="$(basename "$d")"
        mkdir -p "$ARCHIVE_DIR/stale_cut2014/$n"
        find "$d" -maxdepth 1 -name '*.json' -exec cp {} "$ARCHIVE_DIR/stale_cut2014/$n/" \; 2>/dev/null
        rm -rf "$d"
        echo "  ${d/#$HOME/\~} -> архив (только сводные json)"
    done
    echo; disk_status
    ;;
  *)
    echo
    echo "  ${Y}это предпросмотр. Удалить: ./cleanup_stale.sh --yes${N}"
    echo "  ${Y}сохранить сводки в архив:  ./cleanup_stale.sh --archive${N}"
    ;;
esac
