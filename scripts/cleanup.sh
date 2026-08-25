#!/usr/bin/env bash
# =========================================================================
# Уборка результатов прогонов.
#   ./cleanup.sh                 удалить пробные прогоны (кандидаты, bench)
#   ./cleanup.sh --archive       перенести тяжёлое на архивный диск
#   ./cleanup.sh --snapshots     удалить только снимки UNRST (самое тяжёлое)
#   ./cleanup.sh --all           всё, кроме истории и baseline
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

MODE="${1:---candidates}"
echo "${B}Уборка${N}"; disk_status; echo

case "$MODE" in
  --snapshots)
    n=$(find "$RUNS_DIR" -name '*.UNRST' ! -path '*/history/*' \
        ! -path '*/baseline/*' 2>/dev/null | wc -l)
    sz=$(find "$RUNS_DIR" -name '*.UNRST' ! -path '*/history/*' \
        ! -path '*/baseline/*' -exec du -ck {} + 2>/dev/null | tail -1 | cut -f1)
    echo "  снимков UNRST: ${n}, объём: $((${sz:-0}/1024)) МБ"
    find "$RUNS_DIR" -name '*.UNRST' ! -path '*/history/*' \
        ! -path '*/baseline/*' -delete 2>/dev/null
    echo "  ${G}удалены${N} (история и baseline не тронуты)"
    ;;
  --archive)
    [ -n "$ARCHIVE_DIR" ] || { echo "${R}задайте ARCHIVE_DIR, напр.:${N}"
        echo "  export ARCHIVE_DIR=/mnt/d/aios-archive"; exit 1; }
    mkdir -p "$ARCHIVE_DIR"
    for d in history baseline; do
        src="$RUNS_DIR/$d"
        [ -d "$src" ] || continue
        echo "  переношу $d ..."
        rsync -a --remove-source-files "$src/" "$ARCHIVE_DIR/$d/" 2>/dev/null \
            || cp -r "$src" "$ARCHIVE_DIR/" && rm -rf "$src"
        ln -sfn "$ARCHIVE_DIR/$d" "$src"
        echo "  ${G}$d -> $ARCHIVE_DIR/$d${N} (symlink оставлен)"
    done
    ;;
  --all)
    rm -rf "$RUNS_DIR/candidates"/* "$DATA_ROOT/bench"/* \
           "$DATA_ROOT/smoke/out"/* "$DATA_ROOT/bench_deck" \
           "$DATA_ROOT/batch_deck" 2>/dev/null
    echo "  ${G}удалено всё, кроме history и baseline${N}"
    ;;
  *)
    rm -rf "$RUNS_DIR/candidates"/* "$DATA_ROOT/bench"/par* \
           "$DATA_ROOT/bench_deck" 2>/dev/null
    echo "  ${G}пробные прогоны удалены${N}"
    ;;
esac
echo; disk_status
