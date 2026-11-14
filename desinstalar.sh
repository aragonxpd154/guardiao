#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then exec sudo "$0" "$@"; fi
apt-get remove -y guardiao
echo "Guardião removido. (Seus backups em ~/Guardiao/ e a configuração em ~/.config/guardiao/ foram mantidos.)"
