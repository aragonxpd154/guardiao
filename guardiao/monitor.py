"""Vigilância contínua: coleta a config de cada chassi por FTP, valida, guarda
histórico (só quando muda) e avisa na área de trabalho se zerar."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import config
from .netvx import Credenciais
from .sysftp import SysFtp
from .validacao import descrever_firmware, validar_cfg

OID_UTIL_CMD_LOAD = "1.3.6.1.4.1.11268.2.1.1.3.2.5.0"


def _agora():
    return datetime.datetime.now()


class Historico:
    def __init__(self, raiz: Path, nome: str):
        self.dir = raiz / nome / "coletas"
        self.dir.mkdir(parents=True, exist_ok=True)

    def snapshots(self) -> list[tuple[str, dict]]:
        out = []
        for d in sorted(self.dir.iterdir()):
            m = d / "meta.json"
            if m.is_file():
                out.append((d.name, json.loads(m.read_text())))
        return out

    def ultimo(self, so_bom=False):
        for sid, meta in reversed(self.snapshots()):
            if not so_bom or meta.get("cfg_valido"):
                return sid, meta
        return None, None

    def gravar(self, arquivos: dict[str, bytes], meta: dict) -> str:
        sid = _agora().strftime("%Y%m%d-%H%M%S")
        d = self.dir / sid
        n = 1
        while d.exists():
            n += 1
            d = self.dir / f"{sid}-{n}"
        d.mkdir(parents=True)
        for rel, dados in arquivos.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(dados)
        (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        return d.name


def coletar(nome: str, cred: Credenciais, queda_pct: float = 30.0) -> dict:
    arquivos = {}
    with SysFtp(cred.host, cred.usuario, cred.senha, cred.porta_ftp, cred.passivo) as f:
        for n in f.listar("etc/cfg"):
            d = f.tentar_baixar("etc/cfg/" + n)
            if d is not None:
                arquivos["etc/cfg/" + n.lower()] = d
        for rel in ("etc/umconfig.txt", "etc/snmp/snmpd.cnf", "etc/brand"):
            d = f.tentar_baixar(rel)
            if d is not None:
                arquivos[rel] = d
        ver = f.ler_texto("boot/ver_curr") or ""
        crc = None
        man = f.tentar_baixar(f"boot/{ver}/manifest") if ver else None
        for linha in (man or b"").decode("latin-1").splitlines():
            p = linha.split()
            if len(p) == 2 and p[1] == "sysa12/vxworks":
                crc = int(p[0], 16)
    cur = validar_cfg(arquivos.get("etc/cfg/current"))
    bak = arquivos.get("etc/cfg/current.bak")
    hist = Historico(config.pasta_dados(), nome)
    problemas = [] if cur.valido else [f"current: {cur.motivo}"]
    _, bom = hist.ultimo(so_bom=True)
    if cur.valido and bom and bom.get("cfg_linhas"):
        antes = bom["cfg_linhas"]
        if cur.linhas < antes * (1 - queda_pct / 100):
            problemas.append(f"config encolheu de {antes} para {cur.linhas} linhas: possível zeramento")
    h = hashlib.sha256(b"".join(k.encode() + b"\0" + v for k, v in sorted(arquivos.items()))).hexdigest()
    meta = {"chassi": nome, "host": cred.host, "coletado_em": _agora().isoformat(timespec="seconds"),
            "cfg_valido": cur.valido, "cfg_motivo": cur.motivo, "cfg_linhas": cur.linhas, "cfg_tag": cur.tag,
            "bak_presente": bak is not None, "bak_valido": validar_cfg(bak).valido if bak is not None else None,
            "firmware_dir": ver, "firmware_sys": descrever_firmware(crc) if crc is not None else "?",
            "hash": h, "problemas": problemas}
    sid_u, meta_u = hist.ultimo()
    if meta_u and meta_u.get("hash") == h:
        meta["snapshot"], meta["novo"] = sid_u, False
    else:
        meta["snapshot"], meta["novo"] = hist.gravar(arquivos, meta), True
    return meta


def avisar(titulo: str, corpo: str, urgente: bool = True):
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "--app-name=Guardião", "--icon=guardiao",
                            f"--urgency={'critical' if urgente else 'normal'}", titulo, corpo],
                           timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    cmd = config.carregar()["geral"].get("alerta_cmd", "").strip()
    if cmd:
        try:
            subprocess.run(cmd, shell=True, env=dict(os.environ, GUARDIAO_MSG=f"{titulo}: {corpo}"), timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def ativar_timer() -> bool:
    if not shutil.which("systemctl"):
        return False
    r = subprocess.run(["systemctl", "--user", "enable", "--now", "guardiao-coletar.timer"],
                       capture_output=True, text=True)
    return r.returncode == 0


def restaurar_em_slot(cred: Credenciais, dados: bytes, slot: int) -> bytes | None:
    """Envia uma config para etc/cfg/NN e devolve o conteúdo anterior do slot."""
    alvo = f"etc/cfg/{slot:02d}"
    with SysFtp(cred.host, cred.usuario, cred.senha, cred.porta_ftp, cred.passivo) as f:
        anterior = f.tentar_baixar(alvo)
        f.enviar(alvo, dados)
        if f.baixar(alvo) != dados:
            raise IOError("o arquivo lido de volta não confere com o enviado")
    return anterior


PADROES_LOG = re.compile(r"OBEL:|Cfg_Load|configuration|restarted|master|slave|taking over|giving up|"
                         r"Shutting down|Rebooting|mount|Disk erased|CRC|FATAL|failover|failback", re.I)
