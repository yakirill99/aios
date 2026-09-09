# -*- coding: utf-8 -*-
"""
Монтирование configs в контейнер.

Валидатор и мост работают внутри контейнера, куда монтируются только
каталог прогона и scripts. Каталога configs там нет, поэтому пределы
из constraints.yaml не читались, и правка файла ни на что не влияла.

    python3 patch_lib_common.py
"""
import pathlib, shutil

p = pathlib.Path("lib_common.sh")
s = p.read_text(encoding="utf-8")

if "/configs:ro" in s:
    print("правка уже применена"); raise SystemExit(0)

old = '''    docker run --rm -i $USERFLAG -e HOME=/tmp \\
        -v "$out_dir":/out -v "$SCRIPT_DIR":/scripts:ro \\
        "$IMAGE" python3 "/scripts/$script" "$@"'''
new = '''    docker run --rm -i $USERFLAG -e HOME=/tmp \\
        -v "$out_dir":/out -v "$SCRIPT_DIR":/scripts:ro \\
        -v "$AIOS_ROOT/configs":/configs:ro \\
        "$IMAGE" python3 "/scripts/$script" "$@"'''

assert s.count(old) == 1, "не найдена команда docker run в run_py — примените вручную"
shutil.copy(p, p.with_suffix(".sh.bak"))
p.write_text(s.replace(old, new), encoding="utf-8")
print("lib_common.sh: configs монтируется как /configs, копия в lib_common.sh.bak")
