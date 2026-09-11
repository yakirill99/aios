#!/usr/bin/env python3
"""
Вклеивает раздел про Ollama и модель в docs/INSTALL.md вместо
нынешнего «5b. Зависимости хоста».

    python3 merge_install_section.py INSTALL_раздел_модель.md docs/INSTALL.md

Заменяет всё от строки «## 5b.» до следующего «## 6.». Старую версию
кладёт рядом как INSTALL.md.bak.
"""
import re
import shutil
import sys
from pathlib import Path

if len(sys.argv) < 3:
    sys.exit(__doc__)

section_file, target = Path(sys.argv[1]), Path(sys.argv[2])
for p in (section_file, target):
    if not p.exists():
        sys.exit(f"нет файла {p}")

# У файла-раздела первые строки — служебная шапка «Вставить в ...».
# Берём с первого заголовка «## 5b».
sec = section_file.read_text(encoding="utf-8")
m = re.search(r"^## 5b\..*", sec, re.M)
if not m:
    sys.exit("в файле раздела не нашёл заголовок «## 5b.»")
sec = sec[m.start():].rstrip() + "\n\n---\n\n"

doc = target.read_text(encoding="utf-8")
start = re.search(r"^## 5b\..*", doc, re.M)
end = re.search(r"^## 6\..*", doc, re.M)

if start and end:
    new = doc[:start.start()] + sec + doc[end.start():]
    что = "раздел 5b заменён"
elif end:
    new = doc[:end.start()] + sec + doc[end.start():]
    что = "раздел 5b добавлен перед разделом 6"
else:
    sys.exit("не нашёл ни «## 5b.», ни «## 6.» — вклейте вручную")

shutil.copy2(target, target.with_suffix(".md.bak"))
target.write_text(new, encoding="utf-8")
print(f"{что}: {target}")
print(f"копия старой версии: {target.with_suffix('.md.bak')}")
print("\nзаголовки в файле:")
for line in new.splitlines():
    if line.startswith("## "):
        print("  " + line)
