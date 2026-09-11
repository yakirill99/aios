#!/usr/bin/env bash
# Загрузка публичной деки для проверки масштабируемости.
#
# Norne — реальное месторождение Statoil в Норвежском море, выложено
# под Open Database License в репозитории OPM. Это независимая от
# конкурса модель: если система отработает на ней, значит она не
# прибита гвоздями к Model_Y.
#
#   ./scripts/fetch_public_deck.sh norne
#   ./scripts/fetch_public_deck.sh spe9     маленькая, для быстрой проверки
#
# Деки кладутся в model/ и попадают под .gitignore — в репозиторий
# они не едут.

set -euo pipefail

DECK="${1:-norne}"
AIOS_ROOT="${AIOS_ROOT:-$HOME/aios}"
DEST="$AIOS_ROOT/model"
REPO="https://github.com/OPM/opm-tests.git"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

case "$DECK" in
  norne) SUBDIR="norne" ;;
  spe9)  SUBDIR="spe9"  ;;
  spe1)  SUBDIR="spe1"  ;;
  *) echo "Неизвестная дека: $DECK (доступны: norne, spe9, spe1)" >&2; exit 1 ;;
esac

echo "Загружаю $SUBDIR из opm-tests…"
# sparse checkout: весь репозиторий весит гигабайты, нужна одна папка
git clone --filter=blob:none --no-checkout --depth 1 "$REPO" "$TMP/repo"
git -C "$TMP/repo" sparse-checkout init --cone
git -C "$TMP/repo" sparse-checkout set "$SUBDIR"
git -C "$TMP/repo" checkout

mkdir -p "$DEST"
rm -rf "${DEST:?}/$SUBDIR"
cp -r "$TMP/repo/$SUBDIR" "$DEST/$SUBDIR"

echo
echo "Готово: $DEST/$SUBDIR"
echo "Файлы .DATA:"
find "$DEST/$SUBDIR" -maxdepth 1 -name "*.DATA" -printf "  %f\n"
echo
echo "Дальше:"
echo "  1. Проверьте, что дека читается:  flow --version && flow $DEST/$SUBDIR/<файл>.DATA --help"
echo "  2. Посчитайте планку:             ./scripts/prepare_baseline.sh"
echo "  3. Снимите ready: false в configs/decks.yaml для записи $DECK"
