#!/usr/bin/env bash
# Roda o Guardião direto da pasta do projeto, sem instalar.
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
export PYTHONPATH="$AQUI${PYTHONPATH:+:$PYTHONPATH}"
# Prefere um Python do sistema que tenha o GTK (python3-gi).
PY=
for c in /usr/bin/python3 /usr/bin/python3.12 /usr/bin/python3.13 /usr/bin/python3.11 /usr/bin/python3.10; do
    [ -x "$c" ] && "$c" -c 'import gi' 2>/dev/null && { PY=$c; break; }
done
exec "${PY:-python3}" -m guardiao "$@"
