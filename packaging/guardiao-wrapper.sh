#!/bin/sh
# Lançador do Guardião instalado (código em /usr/lib/guardiao).
# Escolhe um Python do sistema que tenha o GTK (python3-gi do apt). Um python3
# do pyenv, do conda ou de /usr/local no PATH não enxerga o GTK; nesse caso o
# terminal ainda funciona, mas o assistente gráfico não abriria.
PY=
for c in /usr/bin/python3 /usr/bin/python3.12 /usr/bin/python3.13 /usr/bin/python3.11 /usr/bin/python3.10; do
    [ -x "$c" ] || continue
    if "$c" -c 'import gi' 2>/dev/null; then PY=$c; break; fi
done
[ -n "$PY" ] || PY=$(command -v /usr/bin/python3 || command -v python3)
exec "$PY" -c 'import sys; sys.path.insert(0, "/usr/lib/guardiao"); from guardiao.cli import main; sys.exit(main())' "$@"
