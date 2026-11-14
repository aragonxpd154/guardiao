#!/usr/bin/env python3
"""Atalho para o modo offline do Guardião (o mesmo que 'guardiao patch').

  python3 netvx_sys_patch.py <cópia_do_cartão_ou_pasta_firmware> <saída> [--chkdsk] [--versao v8-6-2.02]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardiao.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["patch", *sys.argv[1:]]))
