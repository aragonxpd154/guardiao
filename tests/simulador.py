"""Simulador de um chassi NetVX com duas SYS (telnet + FTP) para os testes.

Imita o que a SYS real respondeu no chassi do Morro do Céu: prompt "#11 CEU.1M> ",
'stat slot.N/gen', 'util firmware', 'util save', 'cfg slot.2/gen type=…' (protMode
é só leitura), 'util reset sys|slot.N', failover/failback (com as mensagens de
erro reais) e o IP do sistema acompanhando a SYS que está no comando.

O FTP usa pyftpdlib (só nos testes). Simplificação: um único cartão para as
duas SYS; a sincronização master->reserva é representada pela versão em uso.
"""

from __future__ import annotations

import re
import shutil
import socket
import socketserver
import tempfile
import threading
import time
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardiao.validacao import crc_netvx, eh_obel1  # noqa: E402

USUARIO, SENHA = "admin", "segredo"


def config_exemplo(tag="#11CEU_25Marc10", tipo2="none", linhas=8000) -> bytes:
    cab = f"#cfg={tag}\n#ident=i.g.c\n"
    corpo = ["slot.1/gen/type=sys-a22-std", "slot.1/gen/state=enable", "slot.1/gen/protMode=protectedBy-2",
             "slot.1/gen/protStatus=active", "slot.1/ip/ifc.1/ipAddr=127.0.0.1",
             f"slot.2/gen/type={tipo2}", "slot.2/gen/state=enable",
             f"slot.2/gen/protMode={'backup' if tipo2 != 'none' else 'unprotected'}",
             "slot.2/gen/protStatus=dormant"]
    corpo += [f"slot.{3 + i % 12}/enc.{i % 4 + 1}/vid/param{i}={i * 7}" for i in range(linhas)]
    return (cab + "\n".join(corpo) + "\n").encode()


class Sim:
    def __init__(self, raiz_firmware: Path | None = None, versao="v8-6-2.02", reserva=True,
                 protecao_completa=False, exigir_cabo=False, queda=1.0,
                 silencioso=False, transicao=0.0, ftp_atraso=0.0):
        """silencioso/transicao/ftp_atraso reproduzem a bancada com switch: depois do
        failover a sessão antiga fica muda (sem RST), o IP some por `transicao` s e o
        FTP volta `ftp_atraso` s depois do telnet."""
        self.dir = Path(tempfile.mkdtemp(prefix="sim-netvx-"))
        self.card = self.dir / "d"
        (self.card / "etc" / "cfg").mkdir(parents=True)
        (self.card / "boot").mkdir()
        (self.card / "log" / "sys").mkdir(parents=True)
        if raiz_firmware:
            shutil.copytree(raiz_firmware / "boot" / versao, self.card / "boot" / versao)
            shutil.copytree(raiz_firmware / versao, self.card / versao)
        (self.card / "boot" / "ver_curr").write_text(versao + "\n")
        (self.card / "boot" / "ver_prev").write_text("v8-4-8\n")
        (self.card / "boot" / "v8-4-8").mkdir(exist_ok=True)
        (self.card / "etc" / "cfg" / "current").write_bytes(
            config_exemplo(tipo2="sys-a22-std" if protecao_completa else "none"))
        self.rodando = {1: versao, 2: versao if reserva else None}
        self.no_ar = {1: True, 2: reserva}
        self.ativo = 1
        self.exigir_cabo = exigir_cabo
        self.cabo = 1
        self.queda = queda
        self.silencioso, self.transicao, self.ftp_atraso = silencioso, transicao, ftp_atraso
        self.mudo_ate = 0.0
        self.lock = threading.RLock()
        self.conexoes: list[socket.socket] = []
        self.historico: list[str] = []
        self._iniciar()

    # ------------------------------------------------------------ estado
    def acessivel(self, ftp: bool = False) -> bool:
        if not self.no_ar[self.ativo]:
            return False
        if time.time() < self.mudo_ate + (self.ftp_atraso if ftp else 0):
            return False
        return not self.exigir_cabo or self.cabo == self.ativo

    def cfg(self) -> bytes:
        return (self.card / "etc" / "cfg" / "current").read_bytes()

    def param(self, chave: str) -> str:
        m = re.search(rb"^" + re.escape(chave.encode()) + rb"=(.*)$", self.cfg(), re.M)
        return m.group(1).decode() if m else ""

    def ver(self, nome: str) -> str:
        return (self.card / "boot" / nome).read_text().strip()

    def _vx_crc(self, v: str) -> int | None:
        p = self.card / "boot" / v / "sysa12" / "vxworks"
        return crc_netvx(p.read_bytes()) if p.exists() else None

    def gravar_cfg(self, novo: bytes):
        """Como a SYS grava: com o patch, guarda o anterior em current.bak."""
        cur = self.card / "etc" / "cfg" / "current"
        if eh_obel1(self._vx_crc(self.rodando[self.ativo]) or 0) and cur.exists():
            shutil.copyfile(cur, self.card / "etc" / "cfg" / "current.bak")
        cur.write_bytes(novo)

    def protecao_ok(self) -> bool:
        return (self.param("slot.1/gen/protMode").startswith("protectedBy")
                and self.param("slot.2/gen/type") not in ("", "none") and self.no_ar[2] and self.no_ar[1])

    def derrubar_conexoes(self):
        for c in list(self.conexoes):
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self.conexoes.clear()

    def reiniciar(self, slots):
        for s in slots:
            self.no_ar[s] = False
        if self.ativo in slots:          # só cai a sessão se a SYS no comando reiniciar
            self.derrubar_conexoes()

        def volta():
            time.sleep(self.queda)
            with self.lock:
                for s in slots:
                    self.rodando[s] = self.ver("ver_curr")
                    self.no_ar[s] = True
        threading.Thread(target=volta, daemon=True).start()

    # ------------------------------------------------------------ comandos
    def stat_slot(self, s: int) -> str:
        presente = self.rodando.get(s) is not None
        if s == 1 and self.ativo == 2:
            prot = "inUse"
        elif s == 1:
            prot = "available" if self.protecao_ok() else (
                "unavailable" if self.param("slot.1/gen/protMode").startswith("protectedBy") else "unneeded")
        else:
            prot = "unneeded"
        campos = [("type", "sys-a22-std" if presente else "none"), ("version", self.rodando.get(s) or ""),
                  ("status", "inService" if presente and self.no_ar[s] else "outOfService"),
                  ("protAvail", prot), ("failSlot", "0"), ("fault", "None"), ("softwareFlt", "noFault"),
                  ("led1", "green" if presente and self.no_ar[s] else "off")]
        return f"/stat/slot.{s}/gen:\n" + "".join(f"{k:>20} = {v:<30}\n" for k, v in campos)

    def executar(self, cmd: str) -> tuple[str, bool]:
        """-> (saída, derrubar_conexão)"""
        self.historico.append(cmd)
        p = cmd.split()
        if not p:
            return "", False
        if p[0] == "stat" and len(p) == 2:
            m = re.fullmatch(r"slot\.(\d+)(/gen)?", p[1])
            if m:
                return (self.stat_slot(int(m.group(1))) if m.group(2)
                        else f"/stat/slot.{m.group(1)}:\n                 gen/\n"), False
        if p[:2] == ["util", "firmware"]:
            if len(p) == 2:
                cur, prev = self.ver("ver_curr"), self.ver("ver_prev")
                linhas = ["Available boot versions are:"]
                for d in sorted(x.name for x in (self.card / "boot").iterdir() if x.is_dir()):
                    st = " (current)" if d.lower() == cur.lower() else (" (previous)" if d.lower() == prev.lower() else "")
                    linhas.append(f"\t{d:<15}{st}")
                return "\n".join(linhas) + "\n", False
            alvo = p[2]
            if not (self.card / "boot" / alvo / "sysa12" / "vxworks").exists() or not (self.card / alvo / "title.txt").exists():
                return f"Firmware bind error - {alvo}\n", False
            atual = self.ver("ver_curr")
            if alvo.lower() != atual.lower():
                (self.card / "boot" / "ver_prev").write_text(atual + "\n")
                (self.card / "boot" / "ver_curr").write_text(alvo + "\n")
            for d in (self.card / "boot").iterdir():
                if d.is_dir() and d.name.lower() not in (alvo.lower(), atual.lower()):
                    shutil.rmtree(d)
            return "", False
        if p[:2] == ["util", "save"] and len(p) >= 3:
            n = int(p[2])
            dados = self.cfg()
            if len(p) >= 4:
                dados = re.sub(rb"^#cfg=[^\n]*", b"#cfg=" + p[3].encode(), dados, count=1)
                self.gravar_cfg(dados)
            (self.card / "etc" / "cfg" / f"{n:02d}").write_bytes(dados)
            return "", False
        if p[0] == "cfg" and len(p) >= 3 and p[1] == "slot.2/gen":
            for kv in p[2:]:
                k, v = kv.split("=", 1)
                if k == "protMode":
                    return ("Error setting /cfg/slot.2/gen/protMode = backup:\n"
                            "  The protMode parameter is read only.\n"), False
                dados = re.sub(rb"^slot\.2/gen/" + k.encode() + rb"=.*$",
                               f"slot.2/gen/{k}={v}".encode(), self.cfg(), flags=re.M)
                dados = dados.replace(b"slot.2/gen/protMode=unprotected", b"slot.2/gen/protMode=backup")
                self.gravar_cfg(dados + b"slot.2/sys/extra=1\n" * 500)
            return "", False
        if p[:3] == ["util", "reset", "sys"]:
            self.reiniciar([1, 2] if self.rodando[2] else [1])
            return "", True
        if p[:2] == ["util", "reset"] and len(p) == 3:
            s = int(p[2].split(".")[1])
            if s == self.ativo:
                return "Cannot reset the active controller\n", False
            self.reiniciar([s])
            return "", False
        if p[:2] == ["util", "failover"]:
            if p[2] != "slot.1" or not self.protecao_ok() or self.ativo != 1:
                return "Module failover failed: No backup available for slot 1\n", False
            self.ativo = 2
            self.gravar_cfg(self.cfg() + b"slot.2/gen/takeover=1\n" * 100)
            self.mudo_ate = time.time() + self.transicao
            self.reiniciar([1])               # o slot 1 sai e volta como reserva
            return "", True
        if p[:2] == ["util", "failback"]:
            if p[2] == "slot.2":
                return "Module failback failed: Failback failed: slot 2 is not configured for protection.\n", False
            if self.ativo != 2:
                return "Module failback failed: Failback failed: slot 1 has not failed over.\n", False
            if not self.no_ar[1]:
                return "Module failback failed: Failback failed: slot 1 is not ready.\n", False
            self.ativo = 1
            self.mudo_ate = time.time() + self.transicao
            return "", True
        return f"Unknown command: {cmd}\n", False

    # ------------------------------------------------------------ servidores
    def _iniciar(self):
        sim = self

        class Telnet(socketserver.BaseRequestHandler):
            def handle(self):
                s = self.request
                if not sim.acessivel():
                    return
                sim.conexoes.append(s)
                try:
                    s.sendall(bytes([255, 251, 1, 255, 251, 3]) + b"\r\nlogin: ")
                    f = s.makefile("rb")

                    def linha():
                        l = f.readline()
                        if not l:
                            raise ConnectionError
                        return re.sub(rb"\xff[\xfb-\xfe].", b"", l).strip().decode("latin-1")
                    u = linha()
                    s.sendall(b"password: ")
                    pw = linha()
                    if (u, pw) != (USUARIO, SENHA):
                        s.sendall(b"\r\nLogin incorrect\r\n")
                        return
                    s.sendall(f"\r\n#11 CEU.{sim.ativo}M> ".encode())
                    while True:
                        cmd = linha()
                        if cmd == "exit":
                            return
                        with sim.lock:
                            out, cair = sim.executar(cmd)
                        if cair:
                            if sim.silencioso:       # switch: a sessão antiga só fica muda
                                while f.readline():
                                    pass
                            return
                        s.sendall((cmd + "\r\n" + out.replace("\n", "\r\n")
                                   + f"#11 CEU.{sim.ativo}M> ").encode())
                except (ConnectionError, OSError):
                    pass
                finally:
                    if s in sim.conexoes:
                        sim.conexoes.remove(s)

        class TS(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.telnet = TS(("127.0.0.1", 0), Telnet)
        self.porta_telnet = self.telnet.server_address[1]
        threading.Thread(target=self.telnet.serve_forever, daemon=True).start()

        aut = DummyAuthorizer()
        aut.add_user(USUARIO, SENHA, str(self.dir), perm="elradfmwM")

        class H(FTPHandler):
            authorizer = aut

            def on_connect(self):
                if not sim.acessivel(ftp=True):
                    self.close()

        H.banner = "VxWorks FTP server ready"
        import logging
        logging.getLogger("pyftpdlib").setLevel(logging.ERROR)
        self.ftpd = ThreadedFTPServer(("127.0.0.1", 0), H)
        self.porta_ftp = self.ftpd.address[1]
        threading.Thread(target=self.ftpd.serve_forever, kwargs={"handle_exit": False}, daemon=True).start()

    def credenciais(self):
        from guardiao.netvx import Credenciais
        return Credenciais("127.0.0.1", USUARIO, SENHA, True, self.porta_ftp, self.porta_telnet)

    def fechar(self):
        self.telnet.shutdown()
        self.ftpd.close_all()
        shutil.rmtree(self.dir, ignore_errors=True)
