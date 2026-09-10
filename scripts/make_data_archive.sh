#!/usr/bin/env bash
# ==========================================================================
# Архив данных для организаторов. Репозиторий с кодом остаётся в git,
# тяжёлое уходит одним файлом на Яндекс.Диск.
#
#   ./make_data_archive.sh              # ~/aios-submit-data.tar.zst
#
# В архив кладётся ровно то, без чего нельзя ни проверить число, ни
# открыть дашборд с живыми данными. Промежуточные деки, прогоны
# кандидатов и каталоги возмущений не кладутся: это десятки гигабайт,
# которые заново не нужны никому.
# ==========================================================================
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-$HOME/aios-data}"
AIOS_ROOT="${AIOS_ROOT:-$HOME/aios}"
BEST="${BEST:-full34}"                       # прогон, из которого сдача
OUT="${OUT:-$HOME/aios-submit-data.tar.zst}"
STAGE="$(mktemp -d)"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
add() {  # add <источник> <куда в архиве> [описание]
    local src="$1" dst="$2"
    if [ -e "$src" ]; then
        mkdir -p "$STAGE/$(dirname "$dst")"
        cp -r "$src" "$STAGE/$dst"
        printf '  + %-42s %s\n' "$dst" "$(du -sh "$src" | cut -f1)"
    else
        printf '  ! нет: %s\n' "$src"
    fi
}

say "Собираю архив в $STAGE"

# Опорные прогоны: планка и сверенная история. Их результат неизменен
# весь проект, из них считается всё остальное.
add "$DATA_ROOT/runs/baseline"                data/runs/baseline
add "$DATA_ROOT/runs/history_full/npv.json"   data/runs/history_full/npv.json
add "$DATA_ROOT/runs/history_full/annual_our.csv" data/runs/history_full/annual_our.csv
add "$DATA_ROOT/runs/history_full/chdd_input.csv" data/runs/history_full/chdd_input.csv
add "$DATA_ROOT/runs/history_full/MODEL_Y.SMSPEC" data/runs/history_full/MODEL_Y.SMSPEC
add "$DATA_ROOT/runs/history_full/MODEL_Y.UNSMRY" data/runs/history_full/MODEL_Y.UNSMRY

# Прогон, из которого расписание в сдаче: журнал, паспорт прогона,
# расписание и финальный расчёт с отчётом валидатора.
add "$DATA_ROOT/$BEST/journal.json"           "data/$BEST/journal.json"
add "$DATA_ROOT/$BEST/result.json"            "data/$BEST/result.json"
add "$DATA_ROOT/$BEST/wells_schedule.inc"     "data/$BEST/wells_schedule.inc"
add "$DATA_ROOT/$BEST/runs/FINAL"             "data/$BEST/runs/FINAL"

# Абляция агента: три режима, доказательство воспроизводимости.
for r in abl_off8 abl_live8 abl_replay8 kam_live8 kam_hard8; do
    add "$DATA_ROOT/$r/result.json"  "data/$r/result.json"
    add "$DATA_ROOT/$r/journal.json" "data/$r/journal.json"
    add "$DATA_ROOT/$r/llm_trace.jsonl" "data/$r/llm_trace.jsonl"
done

# Матрица влияния, паспорт фонда, кэш модели.
add "$DATA_ROOT/probes/response_matrix.csv"   data/probes/response_matrix.csv
add "$DATA_ROOT/well_metrics.json"            data/well_metrics.json
add "$DATA_ROOT/well_cards.json"              data/well_cards.json
add "$DATA_ROOT/well_policy.json"             data/well_policy.json
add "$DATA_ROOT/well_passport.md"             data/well_passport.md
add "$DATA_ROOT/llm_cache"                    data/llm_cache

# Рабочая дека под OPM Flow: без неё расписание не пересчитать.
add "$AIOS_ROOT/model/Model_Y_opm"            model/Model_Y_opm
add "$AIOS_ROOT/model/CHANGES_FOR_OPM.md"     model/CHANGES_FOR_OPM.md

# Для дашборда: живые данные кладутся туда, куда он смотрит.
mkdir -p "$STAGE/data/rolling"
cp -f "$DATA_ROOT/$BEST/journal.json" "$STAGE/data/rolling/journal.json" 2>/dev/null || true
cp -f "$DATA_ROOT/$BEST/result.json"  "$STAGE/data/rolling/result.json"  2>/dev/null || true
echo "  + data/rolling/ (журнал прогона для дашборда)"

# --- манифест -------------------------------------------------------------
cat > "$STAGE/MANIFEST.md" <<EOF
# Архив данных AIOS, трек 1

Собран $(date '+%Y-%m-%d %H:%M'). Код — в репозитории, тег \`submission-v1\`.

## Куда распаковывать

    tar --zstd -xf aios-submit-data.tar.zst -C ~
    mv ~/data ~/aios-data
    mv ~/model ~/aios/model

## Что внутри

| Каталог | Что |
|---|---|
| \`data/runs/baseline\` | планка 1 018,986 млн: режимы заморожены |
| \`data/runs/history_full\` | история 2007–2015, сверена с эталоном (+0,16%) |
| \`data/$BEST\` | прогон, из которого расписание в сдаче |
| \`data/abl_*\`, \`data/kam_*\` | абляция режимов агента и правила гидродинамика |
| \`data/probes\` | матрица влияния, 615 связей |
| \`data/llm_cache\` | ответы модели для режима \`--llm replay\` без сети |
| \`data/well_*\` | экономический паспорт фонда |
| \`model/Model_Y_opm\` | рабочая дека под OPM Flow |

Прогоны кандидатов, промежуточные деки и 99 каталогов возмущений в архив
не входят: они восстанавливаются расчётом и занимают десятки гигабайт.

## Контрольные суммы

См. \`SHA256SUMS\`.
EOF

cd "$STAGE" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS

say "Пакую"
tar --zstd -cf "$OUT" -C "$STAGE" .
rm -rf "$STAGE"

say "ГОТОВО"
ls -lh "$OUT"
echo "  Загрузите на Яндекс.Диск и вставьте ссылку в submission/RUN.md"
echo "  (строка «Архив данных:»), затем повторите тег."
