"""Console da SYS por telnet (o mesmo do craft port): login, comandos e leitura.

Escrito com socket puro porque o telnetlib saiu do Python 3.13.
Prompt típico: "#11 CEU.1M> "  (nome do chassi, slot em uso e M = master).
"""

from __future__ import annotations

import re
import socket
import time

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
PROMPT = re.compile(rb"([^\r\n]*?)(\d+)([MS]?)> ?$")


class ErroConsole(Exception):
    pass


class Console:
    def __init__(self, host: str, usuario: str = "admin", senha: str = "", porta: int = 23,
                 timeout: float = 20):
        self.host, self.porta, self.usuario, self.senha, self.timeout = host, porta, usuario, senha, timeout
        self.sock: socket.socket | None = None
        self.buf = b""
        self.prompt = ""

    # ------------------------------------------------------------ protocolo
    def _limpar_iac(self, dados: bytes) -> bytes:
        out = bytearray()
        i = 0
        while i < len(dados):
            b = dados[i]
            if b == IAC and i + 1 < len(dados):
                cmd = dados[i + 1]
                if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(dados):
                    opt = dados[i + 2]
                    if cmd == DO:
                        self.sock.sendall(bytes([IAC, WONT, opt]))
                    elif cmd == WILL:
                        # aceita ECHO (1) e SUPPRESS-GO-AHEAD (3) do servidor
                        self.sock.sendall(bytes([IAC, DO if opt in (1, 3) else DONT, opt]))
                    i += 3
                    continue
                if cmd == SB:
                    fim = dados.find(bytes([IAC, SE]), i)
                    i = len(dados) if fim < 0 else fim + 2
                    continue
                if cmd == IAC:
                    out.append(IAC)
                i += 2
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def _ler_ate(self, padrao: re.Pattern, timeout: float) -> bytes:
        fim = time.time() + timeout
        while True:
            m = padrao.search(self.buf)
            if m:
                dados = self.buf[:m.end()]
                self.buf = self.buf[m.end():]
                return dados
            resta = fim - time.time()
            if resta <= 0:
                raise ErroConsole(f"tempo esgotado esperando resposta da SYS ({self.host})")
            self.sock.settimeout(min(resta, 1.0))
            try:
                pedaco = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as e:
                raise ErroConsole(f"conexão perdida: {e}") from e
            if not pedaco:
                raise ErroConsole("a SYS fechou a conexão")
            self.buf += self._limpar_iac(pedaco)

    def _enviar(self, texto: str):
        self.sock.sendall(texto.encode("latin-1") + b"\r\n")

    # ------------------------------------------------------------ uso
    def abrir(self):
        try:
            self.sock = socket.create_connection((self.host, self.porta), timeout=self.timeout)
        except OSError as e:
            raise ErroConsole(f"sem conexão telnet com {self.host}: {e}") from e
        try:
            self._ler_ate(re.compile(rb"login: ?$", re.I | re.M), self.timeout)
            self._enviar(self.usuario)
            self._ler_ate(re.compile(rb"password: ?$", re.I | re.M), self.timeout)
            self._enviar(self.senha)
            dados = self._ler_ate(re.compile(rb"(> ?$)|(incorrect|denied|invalid)", re.I | re.M), self.timeout)
            if re.search(rb"incorrect|denied|invalid", dados, re.I):
                raise ErroConsole("usuário ou senha recusados pela SYS")
        except (OSError, ErroConsole):
            self.sock.close()
            self.sock = None
            raise
        self._guardar_prompt(dados)
        return self

    def _guardar_prompt(self, dados: bytes):
        ultima = dados.decode("latin-1").rstrip("\r\n").splitlines()[-1] if dados else ""
        self.prompt = ultima.strip()

    def fechar(self):
        if self.sock:
            try:
                self._enviar("exit")
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    def __enter__(self):
        return self if self.sock else self.abrir()

    def __exit__(self, *a):
        self.fechar()

    def comando(self, cmd: str, timeout: float = 60, espera_queda: bool = False) -> str:
        """Executa e devolve a saída (sem o eco e sem o prompt).
        espera_queda=True: comandos que derrubam a sessão (reset, failover)."""
        self._enviar(cmd)
        try:
            dados = self._ler_ate(re.compile(rb"\n[^\r\n]*\d+[MS]?> ?$"), timeout)
        except ErroConsole:
            if espera_queda:
                return self.buf.decode("latin-1")
            raise
        self._guardar_prompt(dados)
        texto = dados.decode("latin-1").replace("\r", "")
        linhas = texto.split("\n")
        if linhas and cmd.strip() and linhas[0].strip().endswith(cmd.strip()):
            linhas = linhas[1:]
        return "\n".join(linhas[:-1]).strip("\n")

    # ------------------------------------------------------------ prompt
    @property
    def slot_ativo(self) -> int | None:
        m = re.search(r"\.(\d+)[MS]?>?$", self.prompt.rstrip("> ").rstrip() + ">")
        m = m or re.search(r"(\d+)[MS]>?", self.prompt)
        return int(m.group(1)) if m else None

    @property
    def eh_master(self) -> bool:
        return bool(re.search(r"\d+M>?\s*$", self.prompt))

    @property
    def chassi(self) -> str:
        m = re.match(r"(.*?)\.\d+[MS]?>", self.prompt)
        return m.group(1).strip() if m else self.prompt


# ------------------------------------------------------------------ leitura
def ler_stat(texto: str) -> dict[str, str]:
    """'  type = sys-a22-std' -> {'type': 'sys-a22-std'}"""
    out = {}
    for linha in texto.splitlines():
        if "=" in linha:
            k, v = linha.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def ler_firmware(texto: str) -> list[tuple[str, str]]:
    """Saída de 'util firmware' -> [('V8-6-2.02', 'previous'), ('v8-6-2.ob1', 'current')]."""
    out = []
    for linha in texto.splitlines():
        m = re.match(r"\s+(\S+)\s*(?:\((\w[\w ]*)\))?\s*$", linha)
        if m and not linha.strip().endswith(":"):
            out.append((m.group(1), (m.group(2) or "").strip()))
    return out
