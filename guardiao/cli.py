"""Linha de comando do Guardião."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, atualizacao, config, monitor, tela

EXEMPLOS = """
exemplos:
  guardiao                      abre o assistente gráfico
  guardiao estado 192.168.101.20
  guardiao coletar              coleta e valida a config de todos os chassis
  guardiao listar 11-ceu        histórico de um chassi
  guardiao restaurar 11-ceu --slot 9
  guardiao patch ~/copia_do_cartao ~/cartao_corrigido   (offline)
  guardiao configurar           abre o guardiao.ini
  guardiao --atualizar          procura e instala uma versão nova
"""


def argumentos(argv):
    a = argparse.ArgumentParser(prog="guardiao", description="Guardião — protege a configuração das SYS do NetVX.",
                                epilog=EXEMPLOS, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("-V", "--versao", action="version", version=f"guardiao {__version__}")
    a.add_argument("--atualizar", action="store_true", help="procura e instala uma versão nova do Guardião")
    a.add_argument("-y", "--sim", action="store_true", help="com --atualizar: instala sem perguntar")
    a.add_argument("--verificar-atualizacao", action="store_true", help="só informa se há versão nova")
    a.add_argument("--notificar", action="store_true", help=argparse.SUPPRESS)
    a.add_argument("--pausar", action="store_true", help="espera Enter antes de fechar (atalhos do menu)")
    sub = a.add_subparsers(dest="cmd")
    sub.add_parser("assistente", help="abre o assistente gráfico")
    p = sub.add_parser("estado", help="diagnóstico de um chassi pelo terminal")
    p.add_argument("ip", nargs="?")
    p.add_argument("-u", "--usuario", default="admin")
    p = sub.add_parser("coletar", help="coleta e valida a config de todos os chassis")
    p.add_argument("--chassi")
    p.add_argument("--notificar", action="store_true", help="aviso na área de trabalho em caso de alerta")
    p = sub.add_parser("listar", help="histórico de coletas")
    p.add_argument("chassi", nargs="?")
    p = sub.add_parser("restaurar", help="envia uma config boa para um slot da SYS")
    p.add_argument("chassi")
    p.add_argument("--snapshot")
    p.add_argument("--slot", type=int, default=9)
    p.add_argument("--carregar", action="store_true", help="carrega o slot na hora (util load)")
    p = sub.add_parser("patch", help="aplica o patch obel1 numa cópia do cartão ou do pacote (offline)")
    p.add_argument("origem")
    p.add_argument("destino")
    p.add_argument("--versao")
    p.add_argument("--chkdsk", action="store_true")
    sub.add_parser("configurar", help="abre o arquivo de configuração")
    return a.parse_args(argv)


# ------------------------------------------------------------------ comandos

def _cred_de(nome_ou_ip: str | None, usuario="admin"):
    from .netvx import Credenciais
    for n, c in config.chassis():
        if nome_ou_ip in (n, c.host):
            return n, c
    if not nome_ou_ip:
        sys.exit("informe o IP da SYS ou o nome do chassi do guardiao.ini")
    senha = getpass.getpass(f"Senha de {usuario}@{nome_ou_ip}: ")
    return nome_ou_ip, Credenciais(nome_ou_ip, usuario, senha)


def cmd_estado(args) -> int:
    from .assistente import resumo_estado
    from .netvx import Chassi
    _n, cred = _cred_de(args.ip, args.usuario)
    e = Chassi(cred).estado()
    for l in resumo_estado(e):
        print(l)
    return 0


def cmd_coletar(args) -> int:
    lista = [x for x in config.chassis() if not args.chassi or x[0] == args.chassi]
    if not lista:
        if args.notificar:          # timer sem nada configurado: silêncio
            return 0
        p, criado = config.garantir()
        print(f"Nenhum chassi configurado. Rode o assistente ou edite {p}  (guardiao configurar)")
        return 1
    queda = config.carregar()["geral"].getfloat("queda_linhas_pct", 30.0)
    codigo, alertas = 0, []
    for nome, cred in lista:
        try:
            m = monitor.coletar(nome, cred, queda)
            st = "ALERTA" if m["problemas"] else "OK"
            cor = tela.AMARELO if m["problemas"] else tela.VERDE
            print(tela.c(f"[{st}]", cor) + f" {nome}: current {m['cfg_motivo']} ({m['cfg_linhas']} linhas); "
                  f".bak: {'sim' if m['bak_presente'] else 'não'}; firmware SYS: {m['firmware_sys']}; "
                  f"snapshot {m['snapshot']}{' (novo)' if m['novo'] else ''}")
            for p in m["problemas"]:
                print(tela.c(f"       ! {p}", tela.AMARELO))
                alertas.append(f"{nome}: {p}")
            if m["problemas"]:
                codigo = max(codigo, 2)
        except Exception as e:  # noqa: BLE001
            print(tela.c("[ERRO]", tela.VERMELHO) + f" {nome}: não consegui coletar: {e}")
            codigo = max(codigo, 1)
    if alertas and args.notificar:
        monitor.avisar("Guardião: configuração da SYS em alerta", "\n".join(alertas))
    return codigo


def cmd_listar(args) -> int:
    nomes = [args.chassi] if args.chassi else [n for n, _ in config.chassis()]
    for nome in nomes:
        hist = monitor.Historico(config.pasta_dados(), nome)
        print(tela.c(nome, tela.NEGRITO))
        snaps = hist.snapshots()
        if not snaps:
            print("  nenhuma coleta ainda")
        for sid, m in snaps:
            st = tela.c("ok  ", tela.VERDE) if m["cfg_valido"] else tela.c("RUIM", tela.VERMELHO)
            bak = "-" if not m["bak_presente"] else ("ok" if m["bak_valido"] else "RUIM")
            print(f"  {sid:17} {st} {m['cfg_linhas']:>6} linhas  .bak {bak:4} {m.get('firmware_sys', '?')}")
    return 0


def cmd_restaurar(args) -> int:
    from .validacao import validar_cfg
    nome, cred = _cred_de(args.chassi)
    hist = monitor.Historico(config.pasta_dados(), nome)
    snaps = dict(hist.snapshots())
    sid = args.snapshot or hist.ultimo(so_bom=True)[0]
    if not sid or sid not in snaps:
        sys.exit("snapshot não encontrado (veja: guardiao listar)")
    dados = (hist.dir / sid / "etc/cfg/current").read_bytes()
    r = validar_cfg(dados)
    if not r.valido:
        sys.exit(f"o current do snapshot {sid} não está íntegro ({r.motivo})")
    anterior = monitor.restaurar_em_slot(cred, dados, args.slot)
    if anterior is not None:
        bk = hist.dir.parent / f"slot{args.slot:02d}-antes-{time.strftime('%Y%m%d-%H%M%S')}"
        bk.write_bytes(anterior)
        print(f"conteúdo anterior do slot guardado em {bk}")
    print(tela.c(f"snapshot {sid} ({r.linhas} linhas) enviado ao slot {args.slot:02d} e conferido.", tela.VERDE))
    if args.carregar:
        from .netvx import Chassi
        Chassi(cred, tela.log).comando(f"util load {args.slot}", timeout=120)
    else:
        print(f"Para aplicar na master: util load {args.slot}")
    return 0


def cmd_patch(args) -> int:
    from . import patch
    try:
        patch.aplicar_pasta(Path(args.origem), Path(args.destino), args.versao, args.chkdsk)
    except patch.ErroPatch as e:
        print(tela.c(f"ERRO: {e}", tela.VERMELHO))
        return 1
    return 0


def cmd_configurar(_args) -> int:
    p, criado = config.garantir()
    print(("Criado " if criado else "Configuração: ") + str(p))
    editor = os.environ.get("EDITOR") or (shutil.which("nano") if sys.stdin.isatty() else None)
    if editor:
        return subprocess.call([editor, str(p)])
    subprocess.call(["xdg-open", str(p)])
    return 0


def tem_tela() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def comando_atualizar(args) -> int:
    print(f"Guardião {__version__} — procurando versão nova no GitHub...")
    nov = atualizacao.verificar(forcar=True, timeout=15)
    if nov is None:
        print(tela.c(f"Você já está na versão mais nova ({__version__}) ou sem acesso ao GitHub.", tela.VERDE))
        return 0
    print(tela.c(f"  ★ Nova versão: {nov.versao}", "1;33"))
    if not args.sim:
        r = input("  Atualizar agora? [S/n] ").strip().lower()
        if r not in ("", "s", "sim", "y"):
            atualizacao.adiar(nov.versao)
            return 0
    if not atualizacao.instalado_no_sistema():
        print("Este Guardião roda da pasta do código: use git pull (ou instale o .deb).")
        return 1
    if os.geteuid() != 0:
        os.execvp("sudo", ["sudo", sys.executable, "-m", "guardiao", "--atualizar", "-y"])
    try:
        deb = atualizacao.baixar(nov)
        return 0 if atualizacao.instalar(deb) else 1
    except atualizacao.ErroAtualizacao as e:
        print(tela.c(f"Atualização cancelada: {e}", tela.VERMELHO))
        return 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = argumentos(argv)
    try:
        if args.verificar_atualizacao:
            if args.notificar:
                return atualizacao.rodar_verificacao_diaria()
            nov = atualizacao.verificar(forcar=True, timeout=15)
            print(f"Nova versão: {nov.versao}" if nov else f"Guardião {__version__}: nenhuma versão nova.")
            return 0
        if args.atualizar:
            return comando_atualizar(args)
        cmd = args.cmd
        if cmd is None or cmd == "assistente":
            if not tem_tela():
                argumentos(["-h"])
            from . import gui
            return gui.main([])
        return {"estado": cmd_estado, "coletar": cmd_coletar, "listar": cmd_listar,
                "restaurar": cmd_restaurar, "patch": cmd_patch, "configurar": cmd_configurar}[cmd](args)
    finally:
        if args.pausar and sys.stdin.isatty():
            try:
                input("\nPressione Enter para fechar...")
            except (EOFError, KeyboardInterrupt):
                pass
