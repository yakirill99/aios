#!/usr/bin/env bash
# ==========================================================================
# ПУНКТЫ 2 и 3. Кэш модели в репозиторий, слияние dev в main, тег.
#
#   ./release.sh                      # проверки, кэш, слияние, тег
#   ./release.sh --dry-run            # только проверки, ничего не меняет
#   TAG=submission-v2 ./release.sh
#
# Порядок важен: сначала кэш и проверки на ветке dev, потом слияние.
# После слияния скрипт клонирует репозиторий во временный каталог и
# смотрит на него глазами организатора — это единственная надёжная
# проверка того, что в main не пусто и ничего не отфильтровано .gitignore.
# ==========================================================================
set -euo pipefail

AIOS_ROOT="${AIOS_ROOT:-$HOME/aios}"
DATA_ROOT="${DATA_ROOT:-$HOME/aios-data}"
TAG="${TAG:-submission-v1}"
CACHE_SRC="${CACHE_SRC:-$DATA_ROOT/llm_cache}"
CACHE_DST="${CACHE_DST:-$AIOS_ROOT/cache/llm}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

cd "$AIOS_ROOT"

# --- 1. Состояние репозитория --------------------------------------------
say "1. Репозиторий"
BR="$(git rev-parse --abbrev-ref HEAD)"
echo "  ветка: $BR"
[ "$BR" = "dev" ] || die "ожидалась ветка dev, а сейчас $BR"
[ -z "$(git status --porcelain)" ] || die "есть незакоммиченные правки:
$(git status --short)"

# --- 2. Кэш модели в репозиторий (пункт 3) --------------------------------
say "2. Кэш ответов модели"
if [ -d "$CACHE_SRC" ]; then
    N="$(find "$CACHE_SRC" -name '*.json' | wc -l)"
    echo "  в данных: $N файлов ($CACHE_SRC)"
    [ "$N" -gt 0 ] || die "кэш пуст — режим replay у организаторов не заработает"
    run mkdir -p "$CACHE_DST"
    run cp -f "$CACHE_SRC"/*.json "$CACHE_DST"/
    # .gitignore почти наверняка содержит *.json — тот же капкан, что съел
    # демо-данные дашборда. Исключение прописывается явно.
    if git check-ignore -q "$CACHE_DST/$(ls "$CACHE_SRC" | head -1)" 2>/dev/null; then
        echo "  кэш попадает под .gitignore — добавляю исключение"
        run bash -c "printf '\n# кэш ответов модели: без него не работает --llm replay\n!cache/llm/*.json\n' >> '$AIOS_ROOT/.gitignore'"
    fi
    run git add -f cache/llm .gitignore
    echo "  добавлено в индекс: cache/llm"
else
    echo "  ВНИМАНИЕ: $CACHE_SRC не найден — replay воспроизводить будет нечем"
fi

# --- 3. Проверки перед слиянием -------------------------------------------
say "3. Что уедет организаторам"
for f in submission/wells_schedule.inc submission/npv_claimed.txt submission/RUN.md; do
    [ -f "$f" ] || die "нет $f"
    echo "  есть: $f"
done
echo "  заявлено: $(cat submission/npv_claimed.txt) млн"
echo "  воздействий в расписании: $(grep -c 'WCONPROD\|WCONINJE\|WELOPEN' submission/wells_schedule.inc || true)"

for f in ui/demo/state.json ui/demo/journal.json docker-compose.yml \
         Dockerfile.dashboard .env.example; do
    [ -f "$f" ] || die "нет $f (дашборд у организаторов не поднимется)"
    if git check-ignore -q "$f"; then
        die "$f отфильтрован .gitignore — после клонирования его не будет"
    fi
done
echo "  дашборд: файлы на месте и не отфильтрованы"

if [ -n "$(git status --porcelain)" ]; then
    say "4. Коммит кэша"
    run git commit -m "кэш ответов модели для режима replay"
else
    say "4. Коммитить нечего"
fi

run git push origin dev

# --- 5. Слияние -----------------------------------------------------------
say "5. Слияние dev в main"
echo "  в main сейчас коммитов: $(git rev-list --count origin/main 2>/dev/null || echo '?')"
run git checkout main
run git merge --no-ff dev -m "релиз: слияние dev в main перед сдачей"
run git tag -a "$TAG" -m "сдача трек 1, ЧДД $(cat submission/npv_claimed.txt) млн"
run git push origin main --follow-tags
run git checkout dev

# --- 6. Взгляд организатора ----------------------------------------------
say "6. Проверка клонированием"
if [ "$DRY" = 1 ]; then
    echo "  [dry-run] пропущено"
else
    TMP="$(mktemp -d)"
    git clone -q --branch "$TAG" "$AIOS_ROOT" "$TMP/aios"
    cd "$TMP/aios"
    echo "  файлов в клоне: $(git ls-files | wc -l)"
    for f in submission/wells_schedule.inc submission/npv_claimed.txt \
             submission/RUN.md docker-compose.yml ui/demo/state.json; do
        [ -f "$f" ] && echo "  ок: $f" || echo "  НЕТ: $f  <-- разобраться"
    done
    echo "  кэш модели в клоне: $(find cache/llm -name '*.json' 2>/dev/null | wc -l) файлов"
    cd "$AIOS_ROOT"
    rm -rf "$TMP"
fi

say "ГОТОВО"
echo "  тег: $TAG"
echo "  дальше: ./make_data_archive.sh и ссылка на Яндекс.Диск в RUN.md"
