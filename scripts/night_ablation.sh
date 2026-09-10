#!/usr/bin/env bash
# Абляция агентного слоя: три режима подряд на одинаковой конфигурации.
# Заодно прогон live наполняет кэш, а replay проверяет воспроизводимость
# без обращений к модели — ровно то, что будут делать организаторы.
set -u
cd "$(dirname "$(readlink -f "$0")")"          # каталог scripts, а не /tmp
source lib_common.sh

export AIOS_LLM_MODEL="${AIOS_LLM_MODEL:-qwen3:14b}"
D="$DATA_ROOT"
mkdir -p "$D/logs"

echo "код: $(git -C "$AIOS_ROOT" rev-parse --short HEAD)"
echo "модель: $AIOS_LLM_MODEL"
ollama list 2>/dev/null | grep -q "${AIOS_LLM_MODEL%%:*}" \
    || { echo "модели ${AIOS_LLM_MODEL} нет в ollama — прогон live будет пустым"; }

for mode in off live replay; do
    echo
    echo "=== режим $mode  $(date '+%F %T')"
    W="$D/abl_${mode}8"
    rm -rf "$W"
    if ! python3 -u rolling_horizon.py run --llm "$mode" --candidates 8 \
            --workdir "$W" > "$D/logs/abl_${mode}8.log" 2>&1; then
        echo "  ПРОГОН УПАЛ, последние строки лога:"
        tail -5 "$D/logs/abl_${mode}8.log"
        continue
    fi
    python3 - "$W/result.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"  ЧДД {r['npv']:,.1f} | планка {r['baseline']:,.1f} | "
      f"прирост {r['npv']-r['baseline']:+,.1f}")
print(f"  воздействий {r['n_actions']} | режим {r.get('llm_mode_effective')} | "
      f"шагов моделью {r.get('llm_steps')}, правилами {r.get('rule_steps')}")
PY
done

echo
echo "=== готово $(date '+%F %T')"
echo "сводка:"
for mode in off live replay; do
    f="$D/abl_${mode}8/result.json"
    [ -f "$f" ] && python3 -c "
import json;r=json.load(open('$f'))
print(f\"  {'$mode':>7}: {r['npv']:>10,.1f} млн, воздействий {r['n_actions']}, \"
      f\"шагов моделью {r.get('llm_steps')}\")" \
      || echo "  $mode: нет результата"
done
