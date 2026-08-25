#!/usr/bin/env bash
# =========================================================================
# ОБЩИЕ ФУНКЦИИ И ПУТИ. Подключается остальными скриптами:  source lib_common.sh
#
# Единственное место, где заданы пути. Меняете здесь — меняется везде.
# Любую переменную можно переопределить снаружи:
#     MODEL_DIR=/другой/путь ./benchmark.sh
# =========================================================================

# --- корни ---------------------------------------------------------------
AIOS_ROOT="${AIOS_ROOT:-$HOME/aios}"          # код, конфиги, деки
DATA_ROOT="${DATA_ROOT:-$HOME/aios-data}"     # результаты прогонов, вне git
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- материалы организаторов (эталон, не править) ------------------------
VENDOR_DIR="${VENDOR_DIR:-$AIOS_ROOT/vendor}"
VENDOR_MODEL="${VENDOR_MODEL:-$VENDOR_DIR/model/Model_Y}"
CHDD_PATH="${CHDD_PATH:-$VENDOR_DIR/chdd}"
export CHDD_PATH

# --- рабочие копии деки --------------------------------------------------
MODEL_ROOT="${MODEL_ROOT:-$AIOS_ROOT/model}"
MODEL_DIR="${MODEL_DIR:-$MODEL_ROOT/Model_Y_opm}"     # адаптирована под OPM
MODEL_HIST="${MODEL_HIST:-$MODEL_ROOT/Model_Y_hist}"  # история до отсечки
MODEL_BASE="${MODEL_BASE:-$MODEL_ROOT/Model_Y_base}"  # опорный сценарий

# --- каталоги результатов ------------------------------------------------
RUNS_DIR="${RUNS_DIR:-$DATA_ROOT/runs}"
PROBES_DIR="${PROBES_DIR:-$DATA_ROOT/probes}"
LOGS_DIR="${LOGS_DIR:-$DATA_ROOT/logs}"
CACHE_DIR="${CACHE_DIR:-$DATA_ROOT/cache}"

# --- параметры расчёта ---------------------------------------------------
IMAGE="${IMAGE:-aios/opm:2026.04}"
# ВАЖНО: дата передачи управления. Определяется концом истории в
# переданном организаторами .inc, а не выбирается нами. Для учебной
# Model_Y расписание идёт до 01.12.2015 — это и есть конец истории.
# В валидационном кейсе придёт своя дата: не зашивать в код.
# Подтверждено организаторами: для Model_Y дата начала управления
# 01.01.2014, расчёт завершается 01.12.2015. Горизонт ЧДД ограничен
# последним расчётным шагом модели — за 01.12.2015 не считаем.
# Итого 23 управляющих месяца. В валидационном кейсе даты придут свои.
CUT_DATE="${CUT_DATE:-2014-01-01}"      # начало управления
END_YEAR="${END_YEAR:-2015}"            # последний расчётный шаг модели
END_DATE="${END_DATE:-2015-12-01}"      # горизонт ЧДД ограничен декой
ARCHIVE_DIR="${ARCHIVE_DIR:-}"          # напр. /mnt/d/aios-archive

# --- пороги остановки ----------------------------------------------------
MIN_FREE_GB="${MIN_FREE_GB:-30}"        # диск
MIN_FREE_RAM_GB="${MIN_FREE_RAM_GB:-4}" # неприкосновенный запас памяти
RAM_PER_RUN_GB="${RAM_PER_RUN_GB:-1.5}" # для планирования параллельности
# Жёсткий лимит контейнера. ВАЖНО: должен быть заметно больше замеренного
# пика, иначе ядро убьёт процесс и прогон молча упадёт без time.log.
# Замер 0.92 ГБ был на горизонте до 2015; до 2022 шагов вдвое больше.
MEM_LIMIT_GB="${MEM_LIMIT_GB:-4}"

USERFLAG="--user $(id -u):$(id -g)"

if [ -t 1 ]; then R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; C=$'\e[36m'; B=$'\e[1m'; N=$'\e[0m'
else R=""; G=""; Y=""; C=""; B=""; N=""; fi

mkdir -p "$LOGS_DIR" 2>/dev/null

# =========================================================================
# ПРОВЕРКИ ОКРУЖЕНИЯ
# =========================================================================
# Вызывать в начале скрипта: require_model "$MODEL_DIR"
require_model() {
    local d="$1"
    if [ ! -d "$d" ]; then
        echo "${R}нет деки: ${d/#$HOME/\~}${N}"
        echo "  доступные:"
        ls -d "$MODEL_ROOT"/*/ 2>/dev/null | sed "s|$HOME|~|;s/^/    /" \
            || echo "    (каталог $MODEL_ROOT пуст)"
        echo "  переопределить: MODEL_DIR=/путь $0"
        return 1
    fi
    if ! find "$d" -maxdepth 1 -iname '*.DATA' | grep -q .; then
        echo "${R}в ${d/#$HOME/\~} нет файла .DATA${N}"; return 1
    fi
    return 0
}

deck_name() { basename "$(find "$1" -maxdepth 1 -iname '*.DATA' | head -1)"; }
sch_file()  { find "$1/INCLUDE" -iname '*sch*.inc' 2>/dev/null | head -1; }

# =========================================================================
# ДИСК
# ВАЖНО: ext4 внутри WSL показывает размер ВИРТУАЛЬНОГО диска (~1 ТБ), но
# файл VHDX физически лежит на диске Windows. Реальный лимит — минимум из
# двух. Без этого страж не сработает и место кончится молча.
# =========================================================================
free_gb_wsl() { df -Pk "$DATA_ROOT" | awk 'NR==2{printf "%d", $4/1048576}'; }
free_gb_win() { df -Pk /mnt/c 2>/dev/null | awk 'NR==2{printf "%d", $4/1048576}'; }

free_gb() {
    local w c; w=$(free_gb_wsl); c=$(free_gb_win)
    [ -z "$c" ] && { echo "$w"; return; }
    [ "$c" -lt "$w" ] && echo "$c" || echo "$w"
}

disk_status() {
    local f u; f=$(free_gb); u=$(du -sh "$DATA_ROOT" 2>/dev/null | cut -f1)
    if [ "$f" -le $((MIN_FREE_GB + 20)) ]; then
        echo "  ${Y}диск: ${f} ГБ (ext4 $(free_gb_wsl), C: $(free_gb_win)), занято ${u:-0}${N}"
    else
        echo "  диск: ${f} ГБ (ext4 $(free_gb_wsl), C: $(free_gb_win)), занято ${u:-0}"
    fi
}

disk_guard() {
    local f; f=$(free_gb)
    [ "$f" -gt "$MIN_FREE_GB" ] && return 0
    {
        echo "=========================================================="
        echo "ОСТАНОВКА ПО МЕСТУ"
        echo "время:    $(date '+%F %T')"
        echo "доступно: ${f} ГБ (ext4 $(free_gb_wsl), C: $(free_gb_win))"
        echo "порог:    ${MIN_FREE_GB} ГБ"
        echo "занято:   $(du -sh "$DATA_ROOT" 2>/dev/null | cut -f1)"
        echo
        du -sh "$DATA_ROOT"/* 2>/dev/null | sort -rh | head -8
        echo
        echo "Что делать:"
        echo "  ./cleanup.sh              удалить пробные прогоны"
        echo "  ./cleanup.sh --snapshots  удалить снимки UNRST"
        echo "  ./cleanup.sh --archive    перенести на архивный диск"
        echo "=========================================================="
    } | tee -a "$LOGS_DIR/disk_stop.log"
    return 1
}

# =========================================================================
# ПАМЯТЬ
# MemAvailable показывает доступное ВНУТРИ WSL, а не физически свободное в
# Windows. WSL берёт страницы у хоста по мере надобности, и если браузер
# держит 20 ГБ, запрос ещё 20 уйдёт в своп — система встанет колом.
# Поэтому потолок ставим по ЯДРАМ, а память используем как ограничитель.
# =========================================================================
avail_ram_gb() { awk '/MemAvailable/{printf "%.1f", $2/1048576}' /proc/meminfo; }

safe_parallel() {
    local want="${1:-6}" avail n cores
    avail=$(avail_ram_gb)
    n=$(awk "BEGIN{v=int(($avail - $MIN_FREE_RAM_GB)/$RAM_PER_RUN_GB); print v<1?1:v}")
    cores=$(( $(nproc) / 2 ))          # два потока на процесс
    [ "$cores" -lt 1 ] && cores=1
    [ "$n" -gt "$cores" ] && n="$cores"
    [ "$n" -gt "$want" ] && n="$want"
    echo "$n"
}

mem_status() {
    local a t; a=$(avail_ram_gb)
    t=$(awk '/MemTotal/{printf "%.1f", $2/1048576}' /proc/meminfo)
    echo "  память: в WSL доступно ${a} из ${t} ГБ; ядер $(nproc)"
    echo "          -> параллельность: $(safe_parallel 99) процессов"
}

mem_guard() {
    local a; a=$(avail_ram_gb)
    if [ "$(awk "BEGIN{print ($a < $MIN_FREE_RAM_GB)}")" = "1" ]; then
        echo "${R}МАЛО ПАМЯТИ: ${a} ГБ при пороге ${MIN_FREE_RAM_GB}${N}" \
            | tee -a "$LOGS_DIR/mem_stop.log"
        return 1
    fi
    return 0
}

# =========================================================================
# ЗАПУСК СИМУЛЯТОРА
# --memory ограничивает контейнер: при нехватке ядро убьёт его, а не начнёт
# свопить всю систему в пол.
# =========================================================================
run_flow() {
    local deck_dir="$1" deck="$2" out="$3" threads="${4:-2}"; shift 4 || true
    mkdir -p "$out"
    docker run --rm -i $USERFLAG -w /out -e HOME=/tmp \
        --memory="${MEM_LIMIT_GB}g" --memory-swap="${MEM_LIMIT_GB}g" \
        -v "$deck_dir":/case:ro -v "$out":/out \
        -e OMP_NUM_THREADS="$threads" \
        "$IMAGE" \
        /usr/bin/time -f "ELAPSED %e MAXRSS %M" \
        flow "/case/$deck" --output-dir=/out "$@" \
        > "$out/flow.log" 2> "$out/time.log"
    local rc=$?
    grep -q "ELAPSED" "$out/time.log" || echo "ELAPSED 0 MAXRSS 0" >> "$out/time.log"
    return $rc
}

# Запуск python-скрипта из SCRIPT_DIR внутри контейнера (там стоит resdata)
run_py() {
    local script="$1" out_dir="$2"; shift 2
    docker run --rm -i $USERFLAG -e HOME=/tmp \
        -v "$out_dir":/out -v "$SCRIPT_DIR":/scripts:ro \
        "$IMAGE" python3 "/scripts/$script" "$@"
}

elapsed_of() { awk '/ELAPSED/{print $2}' "$1/time.log" 2>/dev/null | tail -1; }
maxrss_of()  { awk '/MAXRSS/{print $4}'  "$1/time.log" 2>/dev/null | tail -1; }
run_ok()     { grep -q "End of simulation" "$1/flow.log" 2>/dev/null; }
has_result() { compgen -G "$1/*.UNSMRY" > /dev/null 2>&1; }

# Убрать тяжёлую статику: она одинакова во всех прогонах (22 из 25 МБ)
strip_static() {
    rm -f "$1"/*.EGRID "$1"/*.INIT "$1"/*.DBG "$1"/*.PRT 2>/dev/null
}
