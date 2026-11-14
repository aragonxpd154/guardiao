"""Configuração do Guardião: ~/.config/guardiao/guardiao.ini (permissão 600)."""

from __future__ import annotations

import configparser
import os
import re
import shutil
from pathlib import Path

from .netvx import Credenciais

MODELO = Path(__file__).resolve().parent / "dados" / "guardiao.ini.exemplo"


def caminho() -> Path:
    p = os.environ.get("GUARDIAO_CONFIG")
    if p:
        return Path(p).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "guardiao" / "guardiao.ini"


def carregar() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    p = caminho()
    if p.exists():
        cfg.read(p, encoding="utf-8")
    if "geral" not in cfg:
        cfg["geral"] = {}
    return cfg


def salvar(cfg: configparser.ConfigParser):
    p = caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        cfg.write(f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def garantir() -> tuple[Path, bool]:
    """Cria o arquivo a partir do modelo se não existir. -> (caminho, criado)."""
    p = caminho()
    if p.exists():
        return p, False
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MODELO, p)
    os.chmod(p, 0o600)
    return p, True


def pasta_dados() -> Path:
    cfg = carregar()
    p = Path(cfg["geral"].get("pasta", "~/Guardiao")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def secao_de(nome: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "-", nome).strip("-").lower()
    return s or "chassi"


def configurado(sec: configparser.SectionProxy) -> bool:
    return bool(sec.get("host")) and sec.get("senha", "") != "TROCAR"


def chassis() -> list[tuple[str, Credenciais]]:
    cfg = carregar()
    out = []
    for s in cfg.sections():
        if s == "geral" or not configurado(cfg[s]):
            continue
        sec = cfg[s]
        out.append((s, Credenciais(sec.get("host"), sec.get("usuario", "admin"), sec.get("senha", ""),
                                   sec.getboolean("passivo", True), sec.getint("porta", 21),
                                   sec.getint("porta_telnet", 23))))
    return out


def adicionar_chassi(nome: str, cred: Credenciais) -> str:
    cfg = carregar()
    # tira as seções de exemplo que nunca foram preenchidas
    for s in list(cfg.sections()):
        if s != "geral" and cfg[s].get("senha") == "TROCAR":
            cfg.remove_section(s)
    sec = secao_de(nome)
    for s in cfg.sections():  # mesmo IP com outro nome: reaproveita
        if s != "geral" and cfg[s].get("host") == cred.host:
            sec = s
    cfg[sec] = {"host": cred.host, "usuario": cred.usuario, "senha": cred.senha,
                "passivo": "sim" if cred.passivo else "nao"}
    salvar(cfg)
    return sec
