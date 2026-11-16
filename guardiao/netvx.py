"""Operações num chassi NetVX: estado, backup, firmware, reset, proteção.

Tudo passa por duas portas da SYS master: FTP (arquivos do cartão) e telnet
(console: util/stat/cfg). O IP é o do sistema — ele acompanha a controladora
que está no comando.
"""

from __future__ import annotations

import ftplib
import re
import time
from dataclasses import dataclass, field

from . import patch
from .console import Console, ErroConsole, ler_firmware, ler_stat
from .sysftp import SysFtp
from .validacao import ResultadoCfg, crc_netvx, descrever_firmware, ler_parametros, validar_cfg

# Falhas de rede esperadas enquanto o IP do sistema passa de uma SYS para a outra
# (failover, failback, reset): conexão recusada, sessão muda, FTP que não responde.
FALHAS_DE_REDE = (OSError, EOFError, ErroConsole, ftplib.Error)

# Respostas da SYS que indicam que o failover/failback NÃO foi feito.
_RECUSA = re.compile(r"failed|no backup|not configured|not ready|has not|unknown command|"
                     r"invalid|error|cannot|not allowed", re.I)


class ErroTroca(Exception):
    """A SYS recusou o failover ou o failback (a mensagem é a resposta dela)."""


@dataclass
class Credenciais:
    host: str
    usuario: str = "admin"
    senha: str = ""
    passivo: bool = True
    porta_ftp: int = 21
    porta_telnet: int = 23


@dataclass
class Estado:
    chassi: str = ""
    prompt: str = ""
    slot_ativo: int | None = None
    slots: dict = field(default_factory=dict)          # {1: {...stat...}, 2: {...}}
    versoes: list = field(default_factory=list)        # [(nome, status)]
    ver_curr: str = ""
    ver_prev: str = ""
    crc_sys: int | None = None                         # CRC do vxworks da versão vinculada
    cfg: ResultadoCfg | None = None
    cfg_params: dict = field(default_factory=dict)
    cfg_arquivos: list = field(default_factory=list)
    bak: ResultadoCfg | None = None

    # -------------------------------------------------------------- leitura
    def versao_rodando(self, slot: int) -> str:
        return self.slots.get(slot, {}).get("version", "")

    def em_servico(self, slot: int) -> bool:
        s = self.slots.get(slot, {})
        return s.get("status") == "inService" and s.get("fault", "None") == "None"

    @property
    def tem_reserva_fisica(self) -> bool:
        s = self.slots.get(2, {})
        return s.get("type", "none") not in ("", "none") and s.get("status") == "inService"

    @property
    def reserva_presente(self) -> bool:
        return self.slots.get(2, {}).get("type", "none") not in ("", "none")

    @property
    def protecao_configurada(self) -> bool:
        return (self.cfg_params.get("slot.1/gen/protMode", "").startswith("protectedBy")
                and self.cfg_params.get("slot.2/gen/type", "none") not in ("", "none"))

    @property
    def protecao(self) -> str:
        """Estado da proteção 1+1 das SYS:
        'ativa'       slot 1 no comando e slot 2 pronto para assumir;
        'em-uso'      o slot 2 está no comando (houve failover) — falta o failback;
        'aguardando'  configurada, mas a reserva ainda não está disponível
                      (reiniciando, sincronizando);
        'incompleta'  só metade configurada (o caso do Morro do Céu: slot.2 type=none);
        'desligada'   | 'sem-reserva'."""
        modo = self.cfg_params.get("slot.1/gen/protMode", "")
        tipo2 = self.cfg_params.get("slot.2/gen/type", "none")
        disp = self.slots.get(1, {}).get("protAvail", "")
        if self.slot_ativo == 2 and modo.startswith("protectedBy"):
            return "em-uso"
        if not self.reserva_presente:
            return "sem-reserva"
        if self.protecao_configurada:
            if disp == "available" and self.slot_ativo == 1:
                return "ativa"
            return "aguardando"
        if modo.startswith("protectedBy") or tipo2 not in ("", "none"):
            return "incompleta"
        return "desligada"

    def resumo_slot(self, slot: int) -> str:
        s = self.slots.get(slot, {})
        return (f"slot {slot}: status {s.get('status', '?')}, falha {s.get('fault', '?')}, "
                f"proteção {s.get('protAvail', '?')}, LED {s.get('led1', '?')}")

    @property
    def firmware_sys(self) -> str:
        return descrever_firmware(self.crc_sys) if self.crc_sys is not None else "?"


def _mesmo(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


class Chassi:
    def __init__(self, cred: Credenciais, log=lambda *a, **k: None):
        self.cred = cred
        self.log = log

    # -------------------------------------------------------------- portas
    def console(self, timeout: float = 20) -> Console:
        c = Console(self.cred.host, self.cred.usuario, self.cred.senha, self.cred.porta_telnet, timeout)
        return c.abrir()

    def ftp(self) -> SysFtp:
        f = SysFtp(self.cred.host, self.cred.usuario, self.cred.senha, self.cred.porta_ftp, self.cred.passivo)
        f.abrir()
        return f

    def comando(self, cmd: str, timeout: float = 60, espera_queda: bool = False) -> str:
        with self.console() as c:
            self.log(f"$ {cmd}", "cmd")
            out = c.comando(cmd, timeout, espera_queda)
            if out.strip():
                self.log(out, "saida")
            return out

    # -------------------------------------------------------------- estado
    def estado(self) -> Estado:
        e = Estado()
        with self.console() as c:
            e.prompt, e.chassi, e.slot_ativo = c.prompt, c.chassi, c.slot_ativo
            for s in (1, 2):
                e.slots[s] = ler_stat(c.comando(f"stat slot.{s}/gen"))
            e.versoes = ler_firmware(c.comando("util firmware"))
        with self.ftp() as f:
            e.ver_curr = f.ler_texto("boot/ver_curr") or ""
            e.ver_prev = f.ler_texto("boot/ver_prev") or ""
            man = f.tentar_baixar(f"boot/{e.ver_curr}/manifest") if e.ver_curr else None
            if man:
                for linha in man.decode("latin-1").splitlines():
                    p = linha.split()
                    if len(p) == 2 and p[1] == "sysa12/vxworks":
                        e.crc_sys = int(p[0], 16)
            e.cfg_arquivos = f.listar("etc/cfg")
            cur = f.tentar_baixar("etc/cfg/current")
            e.cfg = validar_cfg(cur)
            if cur:
                e.cfg_params = ler_parametros(cur)
            nomes = {n.lower() for n in e.cfg_arquivos}
            if "current.bak" in nomes:
                e.bak = validar_cfg(f.tentar_baixar("etc/cfg/current.bak"))
        return e

    # -------------------------------------------------------------- config
    def baixar_config(self) -> dict[str, bytes]:
        out = {}
        with self.ftp() as f:
            for n in f.listar("etc/cfg"):
                d = f.tentar_baixar("etc/cfg/" + n)
                if d is not None:
                    out[n.lower()] = d
        return out

    def slot_livre(self, arquivos: list[str], preferido: int = 90) -> int:
        usados = {int(n) for n in arquivos if n.isdigit()}
        for n in list(range(preferido, 100)) + list(range(80, preferido)):
            if n not in usados:
                return n
        return preferido

    def salvar_slot(self, n: int, tag: str | None) -> str:
        # o rótulo do util save vira o rótulo da config ativa: reusa o atual
        cmd = f"util save {n} {tag}" if tag else f"util save {n}"
        return self.comando(cmd, timeout=120)

    # -------------------------------------------------------------- firmware
    def baixar_versao(self, ver: str, progresso=None) -> tuple[dict, dict]:
        """Baixa boot/<ver> e <ver> inteiros (pelos manifests) e confere os CRCs."""
        boot, app = {}, {}
        with self.ftp() as f:
            for destino, base in ((boot, f"boot/{ver}"), (app, ver)):
                man = f.baixar(f"{base}/manifest")
                destino["manifest"] = man
                linhas = [l.split() for l in man.decode("latin-1").splitlines() if len(l.split()) == 2]
                for i, (crc_s, rel) in enumerate(linhas):
                    d = f.baixar(f"{base}/{rel}")
                    if crc_netvx(d) != int(crc_s, 16):
                        raise patch.ErroPatch(f"{base}/{rel} veio com CRC errado do cartão")
                    destino[rel] = d
                    if progresso:
                        progresso(i + 1, len(linhas), f"{base}/{rel}")
        return boot, app

    def enviar_versao(self, nome: str, boot: dict, app: dict, progresso=None):
        with self.ftp() as f:
            f.enviar_arvore(boot, f"boot/{nome}", progresso)
            f.enviar_arvore(app, nome, progresso)

    def conferir_enviada(self, nome: str, boot: dict, app: dict) -> list[str]:
        """Baixa de volta a imagem da SYS e o manifest e compara; confere nomes."""
        erros = []
        with self.ftp() as f:
            if f.tentar_baixar(f"boot/{nome}/sysa12/vxworks") != boot["sysa12/vxworks"]:
                erros.append("sysa12/vxworks no cartão difere do enviado")
            if f.tentar_baixar(f"boot/{nome}/manifest") != boot["manifest"]:
                erros.append("boot manifest no cartão difere do enviado")
            if f.tentar_baixar(f"{nome}/title.txt") != app.get("title.txt"):
                erros.append("title.txt no cartão difere do enviado")
            for base, arqs in ((f"boot/{nome}", boot), (nome, app)):
                pastas = {}
                for rel in arqs:
                    d = rel.rsplit("/", 1)[0] if "/" in rel else ""
                    pastas.setdefault(d, set()).add(rel.rsplit("/", 1)[-1].lower())
                for d, nomes in pastas.items():
                    no_cartao = {n.lower() for n in f.listar(f"{base}/{d}" if d else base)}
                    faltam = nomes - no_cartao
                    if faltam:
                        erros.append(f"faltam em {base}/{d}: {', '.join(sorted(faltam))}")
        return erros

    def vincular(self, nome: str) -> list[tuple[str, str]]:
        self.comando(f"util firmware {nome}", timeout=300)
        lista = ler_firmware(self.comando("util firmware"))
        atual = [n for n, s in lista if s == "current"]
        if not atual or not _mesmo(atual[0], nome):
            raise patch.ErroPatch(f"o bind não foi aceito (versões: {lista})")
        return lista

    # -------------------------------------------------------------- reset/troca
    def reset_sistema(self):
        self.comando("util reset sys", timeout=15, espera_queda=True)

    def reset_slot(self, slot: int):
        return self.comando(f"util reset slot.{slot}", timeout=30)

    def _troca(self, cmd: str) -> str:
        """failover/failback. A sessão pode cair, ou ficar muda (com switch, o IP vai
        para a outra SYS e a conexão antiga não recebe nem resposta nem RST): as duas
        coisas são normais. Só é erro se a SYS responder recusando."""
        try:
            out = self.comando(cmd, timeout=15, espera_queda=True)
        except FALHAS_DE_REDE as ex:
            self.log(f"(sessão encerrada pela troca: {ex.__class__.__name__})", "info")
            return ""
        if _RECUSA.search(out or ""):
            raise ErroTroca(" ".join(out.split()))
        return out

    def failover(self, slot: int = 1) -> str:
        return self._troca(f"util failover slot.{slot}")

    def failback(self, slot: int = 1) -> str:
        """O argumento é o slot PRIMÁRIO (slot.1), não a reserva."""
        return self._troca(f"util failback slot.{slot}")

    def configurar_reserva(self, tipo: str) -> str:
        return self.comando(f"cfg slot.2/gen type={tipo}", timeout=60)

    # -------------------------------------------------------------- espera
    def aguardar(self, condicao, timeout: float, intervalo: float = 10, cancelado=lambda: False,
                 aviso=None) -> Estado:
        """Repete estado() até condicao(estado) ser verdadeira."""
        fim = time.time() + timeout
        ultimo = None
        while time.time() < fim:
            if cancelado():
                raise InterruptedError("cancelado")
            try:
                e = self.estado()
                if condicao(e):
                    return e
                ultimo = "a SYS respondeu, mas ainda não está no estado esperado"
            except FALHAS_DE_REDE as ex:
                ultimo = f"sem resposta ainda ({ex.__class__.__name__})"
            if aviso:
                aviso(ultimo, max(0, fim - time.time()))
            time.sleep(intervalo)
        raise TimeoutError(ultimo or "tempo esgotado")

    def responde(self) -> bool:
        try:
            with self.console(timeout=5):
                return True
        except FALHAS_DE_REDE:
            return False
