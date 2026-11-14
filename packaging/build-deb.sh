#!/usr/bin/env bash
# Gera o pacote .deb do Guardião em dist/.   uso: packaging/build-deb.sh
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
VERSAO="$(python3 -c "import sys; sys.path.insert(0, '$RAIZ'); import guardiao; print(guardiao.__version__)")"
PKG="$(mktemp -d)/guardiao_${VERSAO}_all"

install -d "$PKG/DEBIAN" "$PKG/usr/lib/guardiao/guardiao" "$PKG/usr/bin"
install -m 644 "$RAIZ"/guardiao/*.py "$PKG/usr/lib/guardiao/guardiao/"
( cd "$RAIZ/guardiao" && find dados -type f ) | while read -r f; do
  install -Dm 644 "$RAIZ/guardiao/$f" "$PKG/usr/lib/guardiao/guardiao/$f"
done
install -m 755 "$RAIZ/packaging/guardiao-wrapper.sh" "$PKG/usr/bin/guardiao"
install -m 755 "$RAIZ/packaging/atalho-desktop.sh" "$PKG/usr/lib/guardiao/atalho-desktop.sh"
install -Dm 644 "$RAIZ/assets/logo.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/guardiao.svg"
for d in "$RAIZ"/packaging/icones/*/; do
  install -Dm 644 "$d/guardiao.png" "$PKG/usr/share/icons/hicolor/$(basename "$d")/apps/guardiao.png"
done
install -Dm 644 "$RAIZ/assets/logo-512.png" "$PKG/usr/share/pixmaps/guardiao.png"
install -Dm 644 "$RAIZ/packaging/io.github.aragonxpd154.guardiao.metainfo.xml" \
  "$PKG/usr/share/metainfo/io.github.aragonxpd154.guardiao.metainfo.xml"
for f in guardiao.desktop guardiao-atualizar.desktop; do
  install -Dm 644 "$RAIZ/packaging/$f" "$PKG/usr/share/applications/$f"
done
for f in guardiao-coletar.service guardiao-coletar.timer guardiao-atualizar.service guardiao-atualizar.timer; do
  install -Dm 644 "$RAIZ/packaging/$f" "$PKG/usr/lib/systemd/user/$f"
done
install -Dm 644 "$RAIZ/README.md" "$PKG/usr/share/doc/guardiao/README.md"
install -Dm 644 "$RAIZ/docs/DIAGNOSTICO-E-BANCADA.md" "$PKG/usr/share/doc/guardiao/DIAGNOSTICO-E-BANCADA.md"
install -Dm 644 "$RAIZ/LICENSE" "$PKG/usr/share/doc/guardiao/copyright"

TAM=$(du -sk "$PKG/usr" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CTL
Package: guardiao
Version: $VERSAO
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5)
Recommends: libnotify-bin
Installed-Size: $TAM
Maintainer: Marcos Santos <https://github.com/aragonxpd154>
Homepage: https://github.com/aragonxpd154/guardiao
Description: protege a configuração das SYS do Harris NetVX
 Assistente passo a passo que faz backup da configuração, monta a imagem
 corrigida (patch obel1) a partir do firmware que já está no cartão da SYS,
 envia, confere, faz o bind, ativa, completa a proteção da reserva e testa
 o failover e o failback, com volta para a versão original a qualquer momento.
 Depois, confere a configuração a cada 10 minutos e avisa se ela se perder.
CTL
cat > "$PKG/DEBIAN/postinst" <<'POS'
#!/bin/sh
set -e
rm -rf /usr/lib/guardiao/guardiao/__pycache__
python3 -m compileall -q /usr/lib/guardiao/guardiao >/dev/null 2>&1 || true
gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/share/applications 2>/dev/null || true
/usr/lib/guardiao/atalho-desktop.sh criar || true
if command -v systemctl >/dev/null 2>&1; then
  systemctl --global enable guardiao-atualizar.timer guardiao-coletar.timer >/dev/null 2>&1 || true
  for u in $(loginctl list-users --no-legend 2>/dev/null | awk '$1 >= 1000 {print $2}'); do
    systemctl --user -M "$u@" daemon-reload >/dev/null 2>&1 || true
    systemctl --user -M "$u@" start guardiao-atualizar.timer guardiao-coletar.timer >/dev/null 2>&1 || true
  done
fi
exit 0
POS
cat > "$PKG/DEBIAN/prerm" <<'POS'
#!/bin/sh
rm -rf /usr/lib/guardiao/guardiao/__pycache__
if [ "$1" = remove ]; then
  [ -x /usr/lib/guardiao/atalho-desktop.sh ] && /usr/lib/guardiao/atalho-desktop.sh remover
  if command -v systemctl >/dev/null 2>&1; then
    for u in $(loginctl list-users --no-legend 2>/dev/null | awk '$1 >= 1000 {print $2}'); do
      systemctl --user -M "$u@" stop guardiao-atualizar.timer guardiao-coletar.timer >/dev/null 2>&1 || true
    done
    systemctl --global disable guardiao-atualizar.timer guardiao-coletar.timer >/dev/null 2>&1 || true
  fi
fi
exit 0
POS
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

mkdir -p "$RAIZ/dist"
dpkg-deb --root-owner-group --build "$PKG" "$RAIZ/dist/guardiao_${VERSAO}_all.deb" >/dev/null
( cd "$RAIZ/dist" && sha256sum "guardiao_${VERSAO}_all.deb" > "guardiao_${VERSAO}_all.deb.sha256" )
echo "$RAIZ/dist/guardiao_${VERSAO}_all.deb"
