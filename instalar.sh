#!/usr/bin/env bash
# Instala o Guardião no sistema (Zorin OS 18 / Ubuntu 24.04 e derivados).
#   sudo ./instalar.sh
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
if [[ $EUID -ne 0 ]]; then exec sudo "$0" "$@"; fi

echo "Conferindo dependências (GTK4 e libadwaita)..."
apt-get install -y python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libnotify-bin >/dev/null
python3 -c 'import gi; gi.require_version("Adw","1"); from gi.repository import Adw; import sys; sys.exit(0 if (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5) else 1)' \
  || { echo "O Guardião precisa da libadwaita 1.5 (Zorin OS 18 / Ubuntu 24.04)."; exit 1; }

echo "Gerando e instalando o pacote..."
DEB="$("$AQUI/packaging/build-deb.sh" | tail -1)"
apt-get install -y "$DEB"
echo
echo "Guardião instalado!"
echo "  · No menu:      procure por \"Guardião\" (clique direito: coletar, histórico, configuração)"
echo "  · Na Área de Trabalho: ícone do Guardião (se pedir, clique direito > Permitir execução)"
echo "  · No terminal:  guardiao --help"
echo "  · Backups e histórico em ~/Guardiao/"
