#!/usr/bin/env bash
# =========================================================================
# Структура проекта AIOS: создание и проверка.
#
#   ./setup_dirs.sh            создать недостающее, ничего не удаляя
#   ./setup_dirs.sh check      только проверить
#   ./setup_dirs.sh fix        починить владельца файлов (sudo)
#   ./setup_dirs.sh migrate    перенести данные из старых мест в новые
#
# Скрипт НИКОГДА не удаляет и не перезаписывает существующее.
# Всё, что уже есть, помечается как "было" и остаётся нетронутым.
# =========================================================================
set -uo pipefail

MODE="${1:-init}"
ME="$(id -un)"; UID_ME="$(id -u)"; GID_ME="$(id -g)"
ROOT="${AIOS_ROOT:-$HOME/aios}"
DATA="${DATA_ROOT:-$HOME/aios-data}"

if [ -t 1 ]; then
    R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; C=$'\e[36m'; B=$'\e[1m'; N=$'\e[0m'
else R=""; G=""; Y=""; C=""; B=""; N=""; fi

CREATED=0; EXISTED=0; ERRORS=0; WARNINGS=0
head1(){ echo; echo "${B}$*${N}"; }
ok()   { echo "  ${G}+${N} $*"; }
was()  { echo "    ${C}·${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; WARNINGS=$((WARNINGS+1)); }
bad()  { echo "  ${R}x${N} $*"; ERRORS=$((ERRORS+1)); }

# --- создать каталог, если его нет -------------------------------------
mk() {
    local d="$1" note="${2:-}"
    if [ -e "$d" ]; then
        if [ -L "$d" ]; then
            was "$(short "$d") -> $(readlink "$d")"
        elif [ ! -d "$d" ]; then
            bad "$(short "$d") — существует, но это не каталог"
            return
        else
            local owner; owner="$(stat -c '%U' "$d" 2>/dev/null)"
            if [ "$owner" != "$ME" ]; then
                bad "$(short "$d") — владелец ${owner}"
            elif [ ! -w "$d" ]; then
                bad "$(short "$d") — нет прав на запись"
            else
                was "$(short "$d")"
            fi
        fi
        EXISTED=$((EXISTED+1))
        return
    fi
    [ "$MODE" = "check" ] && { warn "$(short "$d") — отсутствует${note:+ ($note)}"; return; }
    if mkdir -p "$d" 2>/dev/null; then
        ok "$(short "$d")${note:+  ${C}$note${N}}"
        CREATED=$((CREATED+1))
    else
        bad "$(short "$d") — не удалось создать"
    fi
}

short() { echo "${1/#$HOME/~}"; }

# --- создать файл-заглушку, только если его нет ------------------------
mkfile() {
    local f="$1"; shift
    [ -e "$f" ] && { EXISTED=$((EXISTED+1)); return; }
    [ "$MODE" = "check" ] && { warn "$(short "$f") — отсутствует"; return; }
    printf '%s\n' "$@" > "$f" && { ok "$(short "$f")"; CREATED=$((CREATED+1)); }
}

# =========================================================================
echo "${B}Структура AIOS${N}   корень: $(short "$ROOT")   данные: $(short "$DATA")"
[ "$MODE" = "check" ] && echo "${Y}режим проверки — ничего не создаётся${N}"

# --- 1. vendor: эталон организаторов -----------------------------------
head1 "1. vendor/ — материалы организаторов (не править)"
mk "$ROOT/vendor"              "эталон, только чтение"
mk "$ROOT/vendor/model"
mk "$ROOT/vendor/model/Model_Y" "дека как выдали"
mk "$ROOT/vendor/chdd"          "CHDD_PYTHON целиком"
mk "$ROOT/vendor/docs"          "ТЗ, методика, регламент, лекции"

# --- 2. model: рабочие копии -------------------------------------------
head1 "2. model/ — рабочие копии деки"
mk "$ROOT/model"
mk "$ROOT/model/Model_Y_opm"   "адаптирована под OPM Flow"
mk "$ROOT/model/Model_Y_hist"  "обрезана на дату отсечки"

# --- 3. конфиги ---------------------------------------------------------
head1 "3. configs/"
mk "$ROOT/configs"
mk "$ROOT/configs/cases"       "валидационные кейсы"

# --- 4. скрипты обвязки -------------------------------------------------
head1 "4. scripts/ — обвязка вокруг симулятора"
mk "$ROOT/scripts"

# --- 5. исходники -------------------------------------------------------
head1 "5. src/aios/ — код системы"
mk "$ROOT/src"
mk "$ROOT/src/aios"
mk "$ROOT/src/aios/core"          "чистая логика, без внешних зависимостей"
mk "$ROOT/src/aios/interference"  "матрица связей между скважинами"
mk "$ROOT/src/aios/adapters"      "симулятор, LLM, пул процессов"
mk "$ROOT/src/aios/agents"
mk "$ROOT/src/aios/agents/prompts" "промпты отдельными файлами"
mk "$ROOT/src/aios/optimize"
mk "$ROOT/src/aios/orchestrator"
mk "$ROOT/src/aios/api"
mk "$ROOT/src/aios/api/routes"
mk "$ROOT/src/aios/storage"

# --- 6. интерфейс -------------------------------------------------------
head1 "6. ui/ — дашборд"
mk "$ROOT/ui"
mk "$ROOT/ui/src"
mk "$ROOT/ui/src/components"   "NPVCurve, Gantt, WellTable, FieldMap"
mk "$ROOT/ui/src/pages"

# --- 7. тесты и документация -------------------------------------------
head1 "7. tests/ и docs/"
mk "$ROOT/tests"
mk "$ROOT/docs"
mk "$ROOT/docs/decisions"      "ADR: одно решение — один файл"
mk "$ROOT/docs/diagrams"       ".drawio исходники"

# --- 8. пакет сдачи -----------------------------------------------------
head1 "8. submission/ — что уходит организаторам"
mk "$ROOT/submission"

# --- 9. данные прогонов (вне репозитория) -------------------------------
head1 "9. ~/aios-data/ — результаты прогонов, вне git"
mk "$DATA"
mk "$DATA/runs"
mk "$DATA/runs/history"        "прогон истории: снимок + датасет"
mk "$DATA/runs/baseline"       "опорный сценарий"
mk "$DATA/runs/candidates"     "пробные прогоны, чистятся"
mk "$DATA/cache"               "хеш деки -> результат"
mk "$DATA/artifacts"
mk "$DATA/logs"
mk "$DATA/smoke"
mk "$DATA/smoke/out"
mk "$DATA/bench"

# --- 10. служебные файлы ------------------------------------------------
head1 "10. Служебные файлы"
mkfile "$ROOT/.gitignore" \
    "# тяжёлые файлы модели" \
    "vendor/model/*/INCLUDE/*.inc" \
    "vendor/model/*/*.inc" \
    "model/*/" \
    "" \
    "# выход симулятора" \
    "*.UNSMRY" "*.UNRST" "*.ESMRY" "*.SMSPEC" "*.EGRID" "*.INIT" "*.PRT" "*.DBG" \
    "" \
    "# окружение" \
    "__pycache__/" "*.pyc" ".venv/" "node_modules/" ".env" \
    "" \
    "# результаты" \
    "runs/" "cache/" "*.log"

mkfile "$ROOT/vendor/VENDOR.md" \
    "# Материалы организаторов" \
    "" \
    "Эталон. Ничего здесь не правится — все изменения живут в model/ и" \
    "записываются в model/CHANGES_FOR_OPM.md." \
    "" \
    "| Что | Откуда | Получено |" \
    "|---|---|---|" \
    "| model/Model_Y | Model_Y.zip | |" \
    "| chdd/ | CHDD_PYTHON.zip | |" \
    "| docs/ | ТЗ, методика ЧДД, регламент | |" \
    "" \
    "Тяжёлые .inc в git не идут — см. .gitignore."

mkfile "$ROOT/model/CHANGES_FOR_OPM.md" \
    "# Правки деки для OPM Flow" \
    "" \
    "Каждая строка — потенциальный источник расхождения при контрольном" \
    "пересчёте. Нужно уметь объяснить каждую." \
    "" \
    "| Что | Где | Почему |" \
    "|---|---|---|" \
    "| блок \`#if 0 ... #endif\` | MODEL_Y.DATA | препроцессор tNavigator |" \
    "| \`IMPLICIT\` | MODEL_Y.DATA | ключевое слово tNavigator |" \
    "| CRLF -> LF | MODEL_Y.DATA | окончания строк Windows |" \
    "| лишний \`/\` после WELLDIMS | MODEL_Y.DATA | двойной терминатор |" \
    "| убраны WOMT/WOMR/WTHP/WEFF | SUMMARY.INC | OPM их не знает; масса считается из WOPT x 0.833 |"

mkfile "$ROOT/docs/methodology_notes.md" \
    "# Трактовки спорных мест методики ЧДД" \
    "" \
    "Что делать с неотвеченными вопросами: записать сюда принятое допущение" \
    "и продолжать работу. Если ответ придёт другой — будет ясно, что менять." \
    "" \
    "## Открытые вопросы к организаторам" \
    "" \
    "- управляется ли приёмистость нагнетательных в треке 1" \
    "- разрешён ли перевод добывающей под закачку" \
    "- налог на имущество: текст относит к OPEX, в формуле его нет" \
    "- стоимость ЭЦН-50 = 0.90397 млн — не опечатка ли" \
    "" \
    "## Принятые допущения" \
    "" \
    "- плотность нефти 0.833 т/м3 (DENSITY: 832 и 834 по двум PVT-регионам)" \
    "- THP, BHP, WEFF в формулы не входят — проверено по коду эталона"

for d in core interference adapters agents optimize orchestrator api storage; do
    mkfile "$ROOT/src/aios/$d/__init__.py" ""
done
mkfile "$ROOT/src/aios/__init__.py" ""

# .gitkeep в пустых каталогах, чтобы структура попала в git
if [ "$MODE" != "check" ]; then
    for d in "$ROOT/configs/cases" "$ROOT/submission" "$ROOT/docs/decisions" \
             "$ROOT/docs/diagrams" "$ROOT/tests" "$ROOT/ui/src/components"; do
        [ -d "$d" ] && [ -z "$(ls -A "$d" 2>/dev/null)" ] && touch "$d/.gitkeep"
    done
fi

# =========================================================================
head1 "11. Что уже лежит не на своём месте"

check_move() {
    local old="$1" new="$2" what="$3"
    [ -d "$old" ] || return
    if [ -d "$new" ] && [ -n "$(ls -A "$new" 2>/dev/null)" ]; then
        was "$what уже в $(short "$new")"
        return
    fi
    warn "$what лежит в $(short "$old"), по схеме — $(short "$new")"
    echo "        ${C}mv \"$old\" \"$new\"${N}"
    NEED_MIGRATE=1
    MIGRATIONS+=("$old|$new")
}

NEED_MIGRATE=0
declare -a MIGRATIONS=()
check_move "$ROOT/data/model/Model_Y"      "$ROOT/vendor/model/Model_Y" "дека организаторов"
check_move "$ROOT/data/model/Model_Y_opm"  "$ROOT/model/Model_Y_opm"    "рабочая копия"
check_move "$ROOT/data/model/Model_Y_hist" "$ROOT/model/Model_Y_hist"   "дека истории"
check_move "$ROOT/data/CHDD_PYTHON"        "$ROOT/vendor/chdd"          "расчёт ЧДД"
check_move "$ROOT/CHDD_PYTHON"             "$ROOT/vendor/chdd"          "расчёт ЧДД"

if [ "$NEED_MIGRATE" -eq 0 ]; then
    ok "всё на своих местах"
elif [ "$MODE" = "migrate" ]; then
    echo
    echo "  ${B}Переношу${N} (копированием, оригиналы останутся):"
    for m in "${MIGRATIONS[@]}"; do
        old="${m%%|*}"; new="${m##*|}"
        mkdir -p "$(dirname "$new")"
        if cp -rn "$old/." "$new/" 2>/dev/null; then
            ok "$(short "$old") -> $(short "$new")"
            echo "        оригинал не тронут, удалите вручную когда убедитесь"
        else
            bad "не удалось скопировать $(short "$old")"
        fi
    done
else
    echo
    echo "  ${Y}-> ./setup_dirs.sh migrate${N} скопирует (оригиналы останутся на месте)"
fi

# =========================================================================
head1 "12. Чужие файлы (остатки Docker от root)"
FOREIGN=0
for d in "$DATA"; do
    [ -d "$d" ] || continue
    n="$(find "$d" -maxdepth 4 ! -user "$UID_ME" 2>/dev/null | wc -l)"
    [ "$n" -eq 0 ] && continue
    FOREIGN=$((FOREIGN+n))
    warn "$(short "$d") — ${n} объектов чужого владельца"
    find "$d" -maxdepth 4 ! -user "$UID_ME" 2>/dev/null | head -3 \
        | sed "s|$HOME|~|" | sed 's/^/        /'
done
if [ "$FOREIGN" -eq 0 ]; then
    ok "чужих файлов нет"
elif [ "$MODE" = "fix" ]; then
    sudo chown -R "$UID_ME:$GID_ME" "$DATA" && ok "владелец исправлен"
else
    echo "        ${Y}-> ./setup_dirs.sh fix${N}"
fi

# =========================================================================
head1 "13. Окружение"
fstype="$(stat -f -c '%T' "$DATA" 2>/dev/null)"
case "$fstype" in
    *9p*|*drvfs*|*v9fs*) bad "данные на диске Windows ($fstype) — медленно" ;;
    *) ok "файловая система: $fstype" ;;
esac

wsl_free=$(df -Pk "$DATA" | awk 'NR==2{printf "%d", $4/1048576}')
win_free=$(df -Pk /mnt/c 2>/dev/null | awk 'NR==2{printf "%d", $4/1048576}')
real=${win_free:-$wsl_free}
[ "${wsl_free:-0}" -lt "${real:-0}" ] && real=$wsl_free
if [ "${real:-0}" -lt 50 ]; then
    bad "свободно ${real} ГБ — мало"
else
    ok "свободно ${real} ГБ (ext4 ${wsl_free}, C: ${win_free:-н/д})"
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if docker image inspect aios/opm:2026.04 >/dev/null 2>&1; then
        ok "образ aios/opm:2026.04 готов"
    else
        warn "образа aios/opm:2026.04 нет"
    fi
else
    warn "docker недоступен"
fi

if [ -f "$ROOT/vendor/chdd/chdd_model.py" ]; then
    ok "эталон ЧДД на месте"
else
    warn "vendor/chdd/chdd_model.py не найден — положите туда CHDD_PYTHON"
fi

# =========================================================================
echo
echo "======================================================================"
printf "создано: %d, уже было: %d, предупреждений: %d, ошибок: %d\n" \
       "$CREATED" "$EXISTED" "$WARNINGS" "$ERRORS"
if [ "$ERRORS" -gt 0 ]; then
    echo "${R}Есть ошибки.${N} Права чинятся: ./setup_dirs.sh fix"
elif [ "$NEED_MIGRATE" -eq 1 ] && [ "$MODE" != "migrate" ]; then
    echo "${Y}Структура готова, но часть данных лежит по старым путям.${N}"
else
    echo "${G}Структура в порядке.${N}"
fi
echo "======================================================================"
exit $(( ERRORS > 0 ? 1 : 0 ))
