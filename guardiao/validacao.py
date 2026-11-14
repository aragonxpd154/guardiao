"""Validação do arquivo de configuração da SYS e identificação do firmware.

Os critérios são os mesmos do patch obel1 (código que roda dentro da SYS):
cabeçalho "#cfg=", nenhum byte nulo, pelo menos 64 bytes e término em '\\n'.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass, field

# CRC-32/BZIP2 do sysa12/vxworks -> descrição
FIRMWARE_SYS = {
    0x3016f665: "8.4.7 original Harris",
    0x5f22cb81: "8.4.8 original Harris",
    0xb5d1a962: "8.6.2-02 original Harris",
    0xc00f6bec: "8.4.7 + patch obel1",
    0x193c7811: "8.4.8 + patch obel1",
    0xd99fef0d: "8.6.2-02 + patch obel1",
    0x06c408f9: "8.4.7 + obel1 + chkdsk",
    0x44384ab6: "8.4.8 + obel1 + chkdsk",
    0x8f20c3f2: "8.6.2-02 + obel1 + chkdsk",
}


def _tabela():
    t = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        t.append(c)
    return t


_T = _tabela()


_REV = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def _crc_lento(dados: bytes) -> int:
    c = 0xFFFFFFFF
    t = _T
    for b in dados:
        c = ((c << 8) & 0xFFFFFFFF) ^ t[((c >> 24) ^ b) & 0xFF]
    return c ^ 0xFFFFFFFF


def crc_netvx(dados: bytes) -> int:
    """CRC usado nos manifests do NetVX (CRC-32/BZIP2).

    O CRC-32/BZIP2 é o CRC-32 comum com os bits espelhados; usar o zlib com
    os bytes espelhados dá o mesmo resultado ~100x mais rápido."""
    c = zlib.crc32(bytes(dados).translate(_REV))
    return int(f"{c:032b}"[::-1], 2)


def descrever_firmware(crc: int) -> str:
    return FIRMWARE_SYS.get(crc, f"desconhecido (CRC 0x{crc:08x})")


def eh_obel1(crc: int) -> bool:
    return "obel1" in FIRMWARE_SYS.get(crc, "")


def eh_original(crc: int) -> bool:
    return "original" in FIRMWARE_SYS.get(crc, "")


@dataclass
class ResultadoCfg:
    valido: bool
    motivo: str
    bytes: int = 0
    linhas: int = 0
    sha256: str = ""
    tag: str | None = None
    ident: str | None = None
    extras: dict = field(default_factory=dict)


def validar_cfg(dados: bytes | None) -> ResultadoCfg:
    if dados is None:
        return ResultadoCfg(False, "arquivo não existe no cartão")
    r = ResultadoCfg(False, "", len(dados), dados.count(b"\n"), hashlib.sha256(dados).hexdigest())
    m = re.match(rb"#cfg=([^\n]*)\n#ident=([^\n]*)\n", dados)
    if m:
        r.tag = m.group(1).decode("latin-1")
        r.ident = m.group(2).decode("latin-1")
    if not dados:
        r.motivo = "arquivo vazio (0 bytes)"
    elif not dados.startswith(b"#cfg="):
        r.motivo = "sem cabeçalho #cfg= (arquivo corrompido)"
    elif b"\0" in dados:
        r.motivo = f"contém bytes nulos a partir do byte {dados.index(bytes(1))} (cluster perdido no CF)"
    elif len(dados) < 64:
        r.motivo = f"truncado ({len(dados)} bytes)"
    elif not dados.endswith(b"\n"):
        r.motivo = "termina no meio de uma linha (arquivo truncado)"
    else:
        r.valido, r.motivo = True, "íntegro"
    return r


def ler_parametros(dados: bytes) -> dict[str, str]:
    """'slot.1/gen/type=sys-a22-std' -> {'slot.1/gen/type': 'sys-a22-std'}"""
    out = {}
    for linha in dados.decode("latin-1").splitlines():
        if "=" in linha and not linha.startswith("#"):
            k, v = linha.split("=", 1)
            out[k] = v
    return out
