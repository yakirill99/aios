#!/usr/bin/env bash
# =========================================================================
# Подготовка репозитория к первому коммиту.
# Убирает дубли, результаты прогонов и мусор окружения.
#
#   ./git_prepare.sh          показать, что будет сделано
#   ./git_prepare.sh --yes    выполнить
# =========================================================================
set -uo pipefail
ROOT="${AIOS_ROOT:-$HOME/aios}"
cd "$ROOT" || exit 1
MODE="${1:---dry}"

if [ -t 1 ]; then G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
else G=""; Y=""; R=""; B=""; N=""; fi

# что удалить: дубли и результаты
JUNK=(
  "opm-setup"                    # дубль scripts/
  "scripts/To Ub"                # дубль scripts/
  "scripts/baseline.json"        # результат прогона
  "scripts/__pycache__"
  "vendor/model/Model_Y/USER"    # настройки tNavigator, не нужны
)

echo "${B}Подготовка репозитория${N}  $ROOT"
echo
echo "${B}Будет удалено (дубли и мусор):${N}"
for j in "${JUNK[@]}"; do
    [ -e "$j" ] || continue
    sz=$(du -sh "$j" 2>/dev/null | cut -f1)
    printf "  %-34s %8s\n" "$j" "$sz"
done

echo
echo "${B}Останется в репозитории:${N}"
cat <<'EOT'
  scripts/          рабочие скрипты (единственная копия)
  vendor/chdd/      эталонный расчёт ЧДД организаторов
  vendor/docs/      ТЗ, методика, регламент
  model/CHANGES_FOR_OPM.md    журнал правок деки
  docs/             архитектура, трактовки методики
  configs/          ограничения, экономика
  .gitignore
EOT

if [ "$MODE" != "--yes" ]; then
    echo
    echo "  ${Y}это предпросмотр. Выполнить: ./git_prepare.sh --yes${N}"
    exit 0
fi

echo
for j in "${JUNK[@]}"; do
    [ -e "$j" ] || continue
    rm -rf "$j" && echo "  удалено: $j"
done

# --- дополнить .gitignore ----------------------------------------------
add_ignore() {
    grep -qxF "$1" .gitignore 2>/dev/null || echo "$1" >> .gitignore
}
{
  echo ""
  echo "# результаты и дубли"
} >> .gitignore
for pat in "*.json" "!package.json" "scripts/To Ub/" "opm-setup/" \
           "vendor/model/*/USER/" "*.xlsx" "*.csv" ".lock" "*.tar.gz"; do
    add_ignore "$pat"
done
echo "  .gitignore дополнен"

# --- нормализация окончаний строк --------------------------------------
cat > .gitattributes <<'EOT'
# Дека пришла из Windows с CRLF. В репозитории держим LF, чтобы diff
# показывал реальные изменения, а не невидимые символы.
* text=auto eol=lf
*.DATA text eol=lf
*.inc  text eol=lf
*.sh   text eol=lf
*.py   text eol=lf
EOT
echo "  .gitattributes создан"

echo
echo "${G}Готово.${N} Дальше:"
echo "  git rm -r --cached . -q && git add -A"
echo "  git status --short | head -30"
echo "  git diff --cached --stat | tail -3"
