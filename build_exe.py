#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyInstaller derlemesi — gecici dosyalar Windows TEMP altinda (OneDrive disinda).
--clean kullanilmaz; kilitli klasor silme (PermissionError) riski azalir.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    spec = os.path.join(root, "tribal_wars_bot.spec")
    dist = os.path.join(root, "dist_build")

    if not os.path.isfile(spec):
        print("HATA: tribal_wars_bot.spec bulunamadi:\n ", spec)
        return 1

    exe_path = os.path.join(dist, "TribalWarsBot", "TribalWarsBot.exe")
    if os.path.isfile(exe_path):
        print("UYARI: Eski EXE mevcut. Derleme yazarken hata alirsaniz:")
        print(" ", exe_path)
        print("  dosyasini kullanan TribalWarsBot.exe / antivirus / OneDrive kapali olsun.\n")

    work = tempfile.mkdtemp(prefix="twb_pyi_")
    print("Gecici work:", work)
    print("Cikti dist: ", dist)
    print("Kaynak:     ", root)
    print()

    try:
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            spec,
            "--noconfirm",
            "--workpath",
            work,
            "--distpath",
            dist,
        ]
        proc = subprocess.run(cmd, cwd=root)
        code = proc.returncode
        if code != 0:
            return code

        readme_src = os.path.join(root, "README_KURULUM.txt")
        readme_dst = os.path.join(dist, "TribalWarsBot", "README_KURULUM.txt")
        if os.path.isfile(readme_src):
            os.makedirs(os.path.dirname(readme_dst), exist_ok=True)
            shutil.copy2(readme_src, readme_dst)
        gunc_src = os.path.join(root, "guncelle.bat")
        gunc_dst = os.path.join(dist, "TribalWarsBot", "guncelle.bat")
        if os.path.isfile(gunc_src):
            shutil.copy2(gunc_src, gunc_dst)
        print("\nTamam. Cikti:", os.path.join(dist, "TribalWarsBot"))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
