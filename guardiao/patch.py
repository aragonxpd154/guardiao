"""Patch obel1 da imagem VxWorks da SYS (sysa12/vxworks).

P1 Db_Sc_App_Reset ........ FIOSYNC no disco antes de pedir o reset à CIB
P2 Db_Sc_Cfg_Save ......... current -> current.bak (se íntegro) + FIOSYNC
P3 Db_Sc_Cfg_Load_Current . valida o current; se estiver ruim, carrega o .bak
P4 mountDisk .............. não formata o cartão CF sozinho
P5 usrAtaConfig (opcional)  verificação/reparo do FAT ao montar

O código novo (patch-obel1/cave.S) vai no lugar de ada_demangle, função que o
firmware nunca chama. Endereços de cada versão em dados/obel1/versoes/*.json,
gerados por patch-obel1/gen_versao.py a partir dos símbolos da própria imagem.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from .validacao import crc_netvx

DADOS = Path(__file__).resolve().parent / "dados" / "obel1"
TRAP = 0x7FE00008


class ErroPatch(Exception):
    pass


@dataclass
class Versao:
    nome: str
    crc_original: int
    dados: dict
    cave_bin: bytes
    cave_syms: dict


def versoes() -> dict[int, Versao]:
    out = {}
    for j in sorted((DADOS / "versoes").glob("*.json")):
        d = json.loads(j.read_text())
        nome = j.stem
        syms = {}
        for linha in (DADOS / "cave" / f"{nome}.syms").read_text().split("\n"):
            if linha.strip():
                a, n = linha.split()
                syms[n] = int(a, 16)
        v = Versao(nome, int(d["crc_original"], 16), d, (DADOS / "cave" / f"{nome}.bin").read_bytes(), syms)
        out[v.crc_original] = v
    return out


def versao_da_imagem(img: bytes) -> Versao | None:
    return versoes().get(crc_netvx(img))


def _secao_text(img: bytes):
    shoff, = struct.unpack(">I", img[32:36])
    shentsize, shnum, shstrndx = struct.unpack(">HHH", img[46:52])
    sh = [struct.unpack(">IIIIIIIIII", img[shoff + i * shentsize: shoff + i * shentsize + 40]) for i in range(shnum)]
    stroff = sh[shstrndx][4]
    for s in sh:
        nome = img[stroff + s[0]: img.index(b"\0", stroff + s[0])]
        if nome == b".text":
            return s[3], s[4], s[5]
    raise ErroPatch("imagem sem seção .text")


def _bl(src: int, dst: int) -> int:
    return 0x48000001 | ((dst - src) & 0x03FFFFFC)


def aplicar(img: bytes, chkdsk: bool = False) -> tuple[bytes, list[str]]:
    """Devolve (imagem corrigida, linhas de relatório). Recusa qualquer imagem
    que não seja uma das originais conhecidas."""
    V = versao_da_imagem(img)
    if V is None:
        raise ErroPatch(f"imagem com CRC 0x{crc_netvx(img):08x}: não é uma versão original suportada")
    img = bytearray(img)
    t0, toff, _ = _secao_text(bytes(img))

    def off(a): return a - t0 + toff
    def word(a): return struct.unpack(">I", img[off(a):off(a) + 4])[0]
    def put(a, w): img[off(a):off(a) + 4] = struct.pack(">I", w)

    d, cs = V.dados, V.cave_syms
    cave, tam = int(d["cave"], 16), d["cave_size"]
    sites = {k: (int(v["addr"], 16), int(v["orig"], 16)) for k, v in d["sites"].items()}
    novos = {
        "P1": ("Db_Sc_App_Reset: sync antes do reset", _bl(sites["P1"][0], cs["obel_sync_delay"])),
        "P2": ("Db_Sc_Cfg_Save: .bak + sync", _bl(sites["P2"][0], cs["obel_cfg_commit"])),
        "P3": ("Db_Sc_Cfg_Load_Current: validação", _bl(sites["P3"][0], cs["obel_cfg_load"])),
        "P4": ("mountDisk: sem formatação automática", 0x3860FFFF),
    }
    if chkdsk:
        novos["P5"] = ("usrAtaConfig: chkdsk com reparo", 0x38C00002)
    if len(V.cave_bin) > tam:
        raise ErroPatch("código novo maior que o espaço disponível")
    for k in novos:
        a, orig = sites[k]
        if word(a) != orig:
            raise ErroPatch(f"{k} @0x{a:x}: encontrado 0x{word(a):08x}, esperado 0x{orig:08x}")
    if word(cave) != int(d["cave_word0"], 16):
        raise ErroPatch("início da área do código novo não confere")
    for chave, texto in (("STR_DISK", b"/d\0"), ("STR_CURRENT", b"/d/etc/cfg/current\0")):
        a = int(d[chave], 16)
        if bytes(img[off(a):off(a) + len(texto)]) != texto:
            raise ErroPatch(f"string {texto!r} não está em 0x{a:x}")

    img[off(cave):off(cave) + tam] = V.cave_bin + struct.pack(">I", TRAP) * ((tam - len(V.cave_bin)) // 4)
    permitidos = {int(x, 16) for x in d["funcs"].values()} | set(cs.values())
    for a in range(cave, cave + len(V.cave_bin), 4):
        w = word(a)
        if (w & 0xFC000003) == 0x48000001:
            desl = w & 0x03FFFFFC
            if desl & 0x02000000:
                desl -= 0x04000000
            if (a + desl) & 0xFFFFFFFF not in permitidos:
                raise ErroPatch(f"desvio inválido no código novo @0x{a:x}")
    rel = []
    for k, (nome, novo) in novos.items():
        a, orig = sites[k]
        put(a, novo)
        rel.append(f"{k} {nome} @0x{a:06x}: {orig:08x} -> {novo:08x}")
    return bytes(img), rel


def atualizar_manifest(manifest: bytes, crc_antigo: int, crc_novo: int) -> bytes:
    """Troca o CRC da linha 'sysa12/vxworks' preservando o resto do arquivo."""
    import re
    pat = re.compile(rb"0x%08x(\s+sysa12/vxworks)" % crc_antigo)
    novo, n = pat.subn(b"0x%08x" % crc_novo + rb"\1", manifest)
    if n != 1:
        raise ErroPatch("linha do sysa12/vxworks não encontrada no manifest")
    return novo


def conferir_manifest(manifest: bytes, ler) -> list[str]:
    """ler(rel) -> bytes|None.  Devolve a lista de problemas (vazia = ok)."""
    erros = []
    for linha in manifest.decode("latin-1").splitlines():
        p = linha.split()
        if len(p) != 2:
            continue
        dados = ler(p[1])
        if dados is None:
            erros.append(f"falta {p[1]}")
        elif crc_netvx(dados) != int(p[0], 16):
            erros.append(f"CRC errado em {p[1]}")
    return erros


def nome_da_versao_corrigida(ver: str) -> str:
    """v8-6-2.02 -> v8-6-2.ob1 ; v8-4-8 -> v8-4-8.ob1 (nome curto 8.3 do FAT)."""
    base = ver.split(".")[0]
    if len(base) > 8:
        raise ErroPatch(f"nome de versão inesperado: {ver}")
    return f"{base}.ob1"


def aplicar_pasta(origem: Path, destino: Path, versao: str | None = None, chkdsk: bool = False,
                  log=print) -> list[str]:
    """Modo offline: corrige uma cópia do cartão (ou a pasta 'firmware' do pacote).
    Copia origem para destino e corrige boot/<versão>/sysa12/vxworks + manifest
    de toda versão original suportada (ou só da indicada)."""
    import shutil
    origem, destino = Path(origem), Path(destino)
    if destino.exists():
        raise ErroPatch(f"{destino} já existe")
    alvos = []
    for vx in sorted(origem.glob("boot/*/sysa12/vxworks")):
        dirv = vx.parent.parent.name
        if versao and dirv != versao:
            continue
        if versao_da_imagem(vx.read_bytes()):
            alvos.append(dirv)
        else:
            log(f"  -- {dirv}: imagem não é uma original suportada; ignorada")
    if not alvos:
        raise ErroPatch(f"nenhuma imagem original suportada em {origem / 'boot'}")
    shutil.copytree(origem, destino)
    for dirv in alvos:
        vx = destino / "boot" / dirv / "sysa12" / "vxworks"
        img0 = vx.read_bytes()
        img, rel = aplicar(img0, chkdsk)
        vx.write_bytes(img)
        man = destino / "boot" / dirv / "manifest"
        man.write_bytes(atualizar_manifest(man.read_bytes(), crc_netvx(img0), crc_netvx(img)))
        erros = conferir_manifest(man.read_bytes(),
                                  lambda r, b=destino / "boot" / dirv: (b / r).read_bytes() if (b / r).exists() else None)
        if erros:
            raise ErroPatch(f"{dirv}: manifest não confere: {erros}")
        log(f"{dirv}: 0x{crc_netvx(img0):08x} -> 0x{crc_netvx(img):08x}")
        for r in rel:
            log("  " + r)
    return alvos
