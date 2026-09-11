#!/usr/bin/env bash
# Проверка окружения дашборда перед запуском.
#
#   ./scripts/dashboard_preflight.sh          только проверить
#   ./scripts/dashboard_preflight.sh --fix    починить права
#
# Ловит грабли, на которых легко потерять время: каталоги под томами
# создаёт Docker, если их нет на хосте, — и создаёт от root. Контейнер
# работает под обычным пользователем и писать в них не может. Обычный
# mkdir -p это не лечит: каталог уже существует, команда молча ничего
# не делает.

set -uo pipefail

DATA_DIR="${AIOS_DATA_DIR:-$HOME/aios-data}"
FIX=0
[[ "${1:-}" == "--fix" ]] && FIX=1

problems=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mвнимание\033[0m %s\n' "$1"; problems=$((problems+1)); }
bad()  { printf '  \033[31mошибка\033[0m %s\n' "$1"; problems=$((problems+1)); }

echo "Каталог данных: $DATA_DIR"
echo

# --- каталоги и права --------------------------------------------------
for sub in queue rolling probes; do
    path="$DATA_DIR/$sub"

    if [[ ! -d "$path" ]]; then
        if (( FIX )); then
            mkdir -p "$path" && ok "создан $path"
        else
            warn "нет $path — Docker создаст его от root. Запустите с --fix"
        fi
        continue
    fi

    owner="$(stat -c '%U' "$path")"
    if [[ -w "$path" && "$owner" == "$USER" ]]; then
        ok "$path (владелец $owner)"
    elif (( FIX )); then
        if sudo chown -R "$USER:$USER" "$path"; then
            ok "права исправлены: $path"
        else
            bad "не удалось сменить владельца $path"
        fi
    else
        bad "$path принадлежит $owner, контейнер писать не сможет. Запустите с --fix"
    fi
done

# --- docker ------------------------------------------------------------
echo
if command -v docker >/dev/null 2>&1; then
    ok "docker найден: $(docker --version | head -1)"
    docker compose version >/dev/null 2>&1 \
        && ok "плагин compose на месте" \
        || bad "нет docker compose"
else
    bad "docker не найден"
fi

# --- живой сервис ------------------------------------------------------
echo
port="${PORT:-8000}"
if curl -sf "localhost:$port/health" -o /tmp/aios_health.json 2>/dev/null; then
    if grep -q '"queue_writable": *true' /tmp/aios_health.json; then
        ok "дашборд отвечает, очередь доступна на запись"
    else
        bad "дашборд отвечает, но очередь НЕ доступна на запись"
        echo "        $(cat /tmp/aios_health.json)"
    fi
else
    echo "  дашборд на порту $port не отвечает (это нормально, если он ещё не запущен)"
fi

echo
if (( problems == 0 )); then
    echo "Всё в порядке. Запуск: docker compose up --build"
    exit 0
fi

echo "Найдено проблем: $problems"
(( FIX )) || echo "Попробуйте: $0 --fix   (потребуется sudo для смены владельца)"
echo "После починки прав контейнер нужно перезапустить: docker compose restart"
exit 1
