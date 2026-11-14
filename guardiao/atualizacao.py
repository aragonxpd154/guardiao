"""Atualização automática pelo GitHub Releases.

Uma vez por dia o Guardião pergunta ao GitHub qual é a última versão publicada.
Se houver uma nova, pergunta ao usuário se quer atualizar; só então baixa o
.deb, confere o SHA-256 e o nome/versão do pacote, e instala.

Variáveis de ambiente (para testes e redes com proxy):
  GUARDIAO_URL_ATUALIZACAO   URL alternativa do JSON de "release" (padrão: API do GitHub)
  GUARDIAO_SEM_ATUALIZACAO=1 desliga qualquer verificação
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from . import __version__

REPO = "aragonxpd154/guardiao"
URL_PADRAO = f"https://api.github.com/repos/{REPO}/releases/latest"
INTERVALO = 24 * 3600
AQUI = Path(__file__).resolve().parent
LANCADOR_ATUALIZAR = "/usr/share/applications/guardiao-atualizar.desktop"


@dataclass
class Novidade:
    versao: str
    url_deb: str
    sha256: str
    tamanho: int
    notas: str
    pagina: str


# ------------------------------------------------------------------ versões

def tupla(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def mais_nova(remota: str, local: str = __version__) -> bool:
    return tupla(remota) > tupla(local)


def instalado_no_sistema() -> bool:
    """True quando roda da instalação (/usr/lib/guardiao), não da pasta do código."""
    return str(AQUI).startswith("/usr/lib/guardiao")


def desligada() -> bool:
    return os.environ.get("GUARDIAO_SEM_ATUALIZACAO", "") not in ("", "0")


# ------------------------------------------------------------------- estado

def _usuario_real() -> tuple[Path, int, int]:
    """Pasta do usuário de verdade, mesmo rodando via sudo/pkexec."""
    for var in ("SUDO_USER", "PKEXEC_UID"):
        v = os.environ.get(var)
        if not v or v == "root":
            continue
        try:
            pw = pwd.getpwuid(int(v)) if v.isdigit() else pwd.getpwnam(v)
            return Path(pw.pw_dir), pw.pw_uid, pw.pw_gid
        except (KeyError, ValueError):
            pass
    return Path.home(), os.getuid(), os.getgid()


def pasta_estado() -> Path:
    casa, uid, gid = _usuario_real()
    p = casa / ".cache" / "guardiao"
    try:
        p.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            for d in (casa / ".cache", p):
                if d.stat().st_uid == 0 and d != casa:
                    os.chown(d, uid, gid)
    except OSError:
        p = Path(tempfile.gettempdir()) / f"guardiao-{os.getuid()}"
        p.mkdir(exist_ok=True)
    return p


def ler_estado() -> dict:
    try:
        return json.loads((pasta_estado() / "atualizacao.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def salvar_estado(estado: dict):
    arq = pasta_estado() / "atualizacao.json"
    try:
        arq.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.geteuid() == 0:
            _c, uid, gid = _usuario_real()
            os.chown(arq, uid, gid)
    except OSError:
        pass


def adiar(versao: str, horas: int = 24):
    e = ler_estado()
    e["adiado_ate"] = time.time() + horas * 3600
    e["adiado_versao"] = versao
    salvar_estado(e)


def ignorar(versao: str):
    e = ler_estado()
    e["ignorar_versao"] = versao
    salvar_estado(e)


def deve_perguntar(nov: Novidade) -> bool:
    e = ler_estado()
    if e.get("ignorar_versao") == nov.versao:
        return False
    if e.get("adiado_versao") == nov.versao and time.time() < e.get("adiado_ate", 0):
        return False
    return True


# --------------------------------------------------------------- consulta

def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"guardiao/{__version__} (atualizador)",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def consultar(timeout: float = 6.0) -> Novidade | None:
    """Pergunta ao GitHub pela última release. Devolve None se não achar .deb."""
    url = os.environ.get("GUARDIAO_URL_ATUALIZACAO") or URL_PADRAO
    dados = json.loads(_get(url, timeout))
    versao = (dados.get("tag_name") or "").lstrip("vV")
    deb = sha = None
    for a in dados.get("assets", []):
        nome = a.get("name", "")
        if re.fullmatch(r"guardiao_[\d.]+_all\.deb", nome):
            deb = a
        elif nome.endswith(".deb.sha256"):
            sha = a
    if not versao or not deb:
        return None
    sha256 = (deb.get("digest") or "").removeprefix("sha256:")
    if not sha256 and sha:
        try:
            sha256 = _get(sha["browser_download_url"], timeout).decode().split()[0]
        except (OSError, IndexError, UnicodeDecodeError):
            sha256 = ""
    notas = (dados.get("body") or "").strip()
    return Novidade(versao=versao, url_deb=deb["browser_download_url"], sha256=sha256.lower(),
                    tamanho=int(deb.get("size") or 0), notas=notas[:1500],
                    pagina=dados.get("html_url") or f"https://github.com/{REPO}/releases")


def verificar(forcar: bool = False, timeout: float = 6.0) -> Novidade | None:
    """Novidade se houver versão mais nova. Consulta a rede no máximo 1x por dia."""
    if desligada():
        return None
    e = ler_estado()
    agora = time.time()
    if not forcar and agora - e.get("verificado_em", 0) < INTERVALO:
        nov = e.get("novidade")
        if nov and mais_nova(nov.get("versao", "")):
            return Novidade(**nov)
        return None
    try:
        nov = consultar(timeout)
    except (OSError, ValueError, KeyError):
        return None  # sem internet ou GitHub fora: tenta de novo na próxima
    e = ler_estado()
    e["verificado_em"] = agora
    e["novidade"] = asdict(nov) if nov else None
    salvar_estado(e)
    if nov and mais_nova(nov.versao):
        return nov
    return None


# ---------------------------------------------------------- baixar/instalar

class ErroAtualizacao(Exception):
    pass


def baixar(nov: Novidade, progresso=None) -> Path:
    if not nov.sha256 or not re.fullmatch(r"[0-9a-f]{64}", nov.sha256):
        raise ErroAtualizacao("a release não informa o SHA-256 do pacote; atualização recusada por segurança")
    destino = pasta_estado() / f"guardiao_{nov.versao}_all.deb"
    tmp = destino.with_suffix(".parcial")
    h = hashlib.sha256()
    req = urllib.request.Request(nov.url_deb, headers={"User-Agent": f"guardiao/{__version__} (atualizador)"})
    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or nov.tamanho or 0)
        lido = 0
        while True:
            bloco = r.read(65536)
            if not bloco:
                break
            f.write(bloco)
            h.update(bloco)
            lido += len(bloco)
            if progresso:
                progresso(lido, total)
    if h.hexdigest() != nov.sha256:
        tmp.unlink(missing_ok=True)
        raise ErroAtualizacao("o arquivo baixado não confere com o SHA-256 publicado; nada foi instalado")
    campos = dict(re.findall(r"^(\w+):\s*(\S+)", "\n".join(_campos(tmp)), re.M))
    if campos.get("Package") != "guardiao" or campos.get("Version") != nov.versao:
        tmp.unlink(missing_ok=True)
        raise ErroAtualizacao("o pacote baixado não é o Guardião esperado; nada foi instalado")
    os.replace(tmp, destino)
    if os.geteuid() == 0:
        _c, uid, gid = _usuario_real()
        try:
            os.chown(destino, uid, gid)
        except OSError:
            pass
    return destino


def _campos(deb: Path) -> list[str]:
    try:
        return subprocess.run(["dpkg-deb", "-f", str(deb), "Package", "Version"],
                              capture_output=True, text=True, timeout=20).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def instalar(deb: Path) -> bool:
    """Instala o .deb (precisa ser root). Mostra a saída do apt no terminal."""
    if os.geteuid() != 0:
        raise ErroAtualizacao("instalar a atualização precisa de sudo")
    cmd = ["apt-get", "install", "-y", "--no-install-recommends", str(deb.resolve())]
    if shutil.which("apt-get") is None:
        cmd = ["dpkg", "-i", str(deb.resolve())]
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    r = subprocess.run(cmd, env=env)
    return r.returncode == 0


# ------------------------------------------------------ aviso na área de trabalho

def _resumo_notas(notas: str, limite: int = 220) -> str:
    linhas = [ln.strip(" -*#") for ln in notas.splitlines() if ln.strip(" -*#")]
    texto = " · ".join(linhas)
    return texto if len(texto) <= limite else texto[:limite - 1] + "…"


def abrir_atualizador():
    """Abre um terminal rodando `guardiao --atualizar` (pede a senha do sudo ali)."""
    if Path(LANCADOR_ATUALIZAR).exists() and shutil.which("gio"):
        if subprocess.run(["gio", "launch", LANCADOR_ATUALIZAR]).returncode == 0:
            return True
    cmd = ["guardiao", "--atualizar", "--pausar"]
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["kgx", "--"],
                 ["ptyxis", "--"], ["xfce4-terminal", "-x"], ["konsole", "-e"], ["xterm", "-e"]):
        if shutil.which(term[0]):
            subprocess.Popen(term + cmd, start_new_session=True)
            return True
    return False


def notificar(nov: Novidade) -> str:
    """Mostra o aviso e devolve 'atualizar', 'depois', 'ignorar' ou ''."""
    titulo = f"Guardião {nov.versao} disponível"
    corpo = f"Você tem a versão {__version__}."
    if nov.notas:
        corpo += "\n" + _resumo_notas(nov.notas)
    if shutil.which("notify-send"):
        try:
            r = subprocess.run(
                ["notify-send", "--app-name=Guardião", "--icon=guardiao", "--urgency=normal", "--wait",
                 "--action=atualizar=Atualizar agora", "--action=depois=Lembrar amanhã",
                 "--action=ignorar=Pular esta versão", titulo, corpo],
                capture_output=True, text=True, timeout=6 * 3600)
            if r.returncode == 0:
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which("zenity"):
        try:
            r = subprocess.run(
                ["zenity", "--question", "--title=Atualização do Guardião", "--icon-name=guardiao",
                 f"--text={titulo}\n\n{corpo}\n\nDeseja atualizar agora?",
                 "--ok-label=Atualizar agora", "--cancel-label=Depois"],
                timeout=6 * 3600)
            return "atualizar" if r.returncode == 0 else "depois"
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def rodar_verificacao_diaria() -> int:
    """Chamado pelo timer do systemd (como usuário comum, sem sudo)."""
    if desligada():
        return 0
    nov = verificar()
    if not nov or not deve_perguntar(nov):
        return 0
    e = ler_estado()
    if e.get("avisado_versao") == nov.versao and time.time() - e.get("avisado_em", 0) < INTERVALO:
        return 0
    e["avisado_versao"], e["avisado_em"] = nov.versao, time.time()
    salvar_estado(e)
    escolha = notificar(nov)
    if escolha == "atualizar":
        abrir_atualizador()
    elif escolha == "ignorar":
        ignorar(nov.versao)
    else:
        adiar(nov.versao)
    return 0
