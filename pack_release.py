#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dist_build/TribalWarsBot -> dist_build/TribalWarsBot.zip"""
from __future__ import annotations

import os
import shutil
import sys
import zipfile


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(root, "dist_build", "TribalWarsBot")
    out_zip = os.path.join(root, "dist_build", "TribalWarsBot.zip")
    lic_src = os.path.join(root, "license_config.json")
    lic_dst = os.path.join(dist_dir, "license_config.json")

    exe = os.path.join(dist_dir, "TribalWarsBot.exe")
    if not os.path.isfile(exe):
        print("HATA: Once build alin: build_exe.bat")
        return 1

    if os.path.isfile(lic_src):
        shutil.copy2(lic_src, lic_dst)
        print("license_config.json kopyalandi.")

    if os.path.isfile(out_zip):
        os.remove(out_zip)

    print("ZIP olusturuluyor (bot kapali olmali)...")
    errors: list[str] = []
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for dirpath, _dirnames, filenames in os.walk(dist_dir):
            for name in filenames:
                full = os.path.join(dirpath, name)
                arc = os.path.relpath(full, dist_dir)
                try:
                    zf.write(full, arc)
                except OSError as e:
                    errors.append(f"{arc}: {e}")

    if errors:
        print("\nHATA: Bazi dosyalar kilitli (bot acik mi?):")
        for err in errors[:5]:
            print(" ", err)
        if len(errors) > 5:
            print(f"  ... ve {len(errors) - 5} dosya daha")
        if os.path.isfile(out_zip):
            os.remove(out_zip)
        return 1

    mb = os.path.getsize(out_zip) / (1024 * 1024)
    print(f"\nTamam: {out_zip} ({mb:.1f} MB)")
    print("\nSunucuya yukleme:")
    print("  1) ZIP dosyasini VDS e kopyala")
    print("  2) VDS: .\\vds\\publish-release.ps1 -ZipPath ...\\TribalWarsBot.zip")
    print("  3) http://31.57.77.190:8080/static/TribalWarsBot.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
