#!/usr/bin/env python3
"""
gen_versao.py <vxworks> -> imprime JSON com tudo que o patch obel1 precisa para
aquela imagem: enderecos das funcoes usadas pelo codigo novo, das strings, da
caverna (ada_demangle) e dos 5 pontos de patch com a palavra original.
Tudo e localizado pelos simbolos (a imagem nao e stripped) e por padrao de
instrucoes, e cada achado e conferido.
"""
import json, re, struct, subprocess, sys
sys.path.insert(0, __import__('os').path.dirname(__file__))
from netvx_crc import crc

img = sys.argv[1]
data = open(img, 'rb').read()
sec = subprocess.run(['readelf', '-SW', img], capture_output=True, text=True).stdout
m = re.search(r'\]\s+\.text\s+PROGBITS\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)', sec)
T0, TOFF, TSZ = (int(x, 16) for x in m.groups())
S = {}; SZ = {}; FUNCS = []
for l in subprocess.run(['nm', '-S', img], capture_output=True, text=True).stdout.splitlines():
    p = l.split()
    if len(p) == 4:
        S[p[3]] = int(p[0], 16); SZ[p[3]] = int(p[1], 16)
        if p[2] in 'tT': FUNCS.append((int(p[0], 16), int(p[1], 16)))
    elif len(p) == 3: S.setdefault(p[2], int(p[0], 16))

def w(a): o = a - T0 + TOFF; return struct.unpack('>I', data[o:o+4])[0]
def cstr(a):
    o = a - T0 + TOFF; return data[o:data.index(b'\0', o)].decode()
def bl_target(a):
    x = w(a)
    if (x & 0xfc000003) != 0x48000001: return None
    d = x & 0x03fffffc
    if d & 0x02000000: d -= 0x04000000
    return (a + d) & 0xffffffff
def find_bl(func, target, nth=0):
    hits = [a for a in range(S[func], S[func] + SZ[func], 4) if bl_target(a) == S[target]]
    assert len(hits) > nth, f'{func}: bl {target} nao encontrado'
    return hits[nth] if nth >= 0 else hits[-1]
def addr_of_string(func, text):
    """procura lis/addi dentro da funcao que formam o endereco de 'text'"""
    hi = {}
    for a in range(S[func], S[func] + SZ[func], 4):
        x = w(a); op = x >> 26; rt = (x >> 21) & 31; ra = (x >> 16) & 31; imm = x & 0xffff
        if op == 15 and ra == 0: hi[rt] = imm << 16                      # lis
        elif op == 14 and ra in hi:                                       # addi
            v = (hi[ra] + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xffffffff
            try:
                if cstr(v) == text: return v
            except Exception: pass
    raise SystemExit(f'string {text!r} nao achada em {func}')

out = {'crc_original': f'0x{crc(data):08x}'}
out['funcs'] = {n: f'0x{S[n]:x}' for n in
                ['open', 'close', 'read', 'ioctl', 'strcmp', 'taskDelay', 'loggerMsg', 'OS_Rename', 'Db_Sc_Cfg_Load']}
out['STR_DISK'] = f'0x{addr_of_string("mountDisk", "/d"):x}'
out['STR_CURRENT'] = f'0x{addr_of_string("Db_Sc_Cfg_Load_Current", "/d/etc/cfg/current"):x}'
assert SZ['ada_demangle'] >= 764
out['cave'] = f'0x{S["ada_demangle"]:x}'; out['cave_size'] = SZ['ada_demangle']
out['cave_word0'] = f'0x{w(S["ada_demangle"]):08x}'

# P1: o taskDelay imediatamente antes do bl Sc_CibResetSys em Db_Sc_App_Reset
cib = find_bl('Db_Sc_App_Reset', 'Sc_CibResetSys')
assert bl_target(cib - 4) == S['taskDelay'] and w(cib - 8) == 0x3860000a, 'P1: padrao li r3,10 / bl taskDelay nao confere'
p1 = cib - 4
# P2: o bl OS_Rename em Db_Sc_Cfg_Save
p2 = find_bl('Db_Sc_Cfg_Save', 'OS_Rename')
# P3: o bl Db_Sc_Cfg_Load em Db_Sc_Cfg_Load_Current
p3 = find_bl('Db_Sc_Cfg_Load_Current', 'Db_Sc_Cfg_Load')
# P4: o bl dosFsVolFormat em mountDisk
p4 = find_bl('mountDisk', 'dosFsVolFormat')
# P5: li r6,-1 imediatamente antes do bl dosFsDevCreate em usrAtaConfig
dc = find_bl('usrAtaConfig', 'dosFsDevCreate')
assert w(dc - 4) == 0x38c0ffff, 'P5: li r6,-1 nao confere'
p5 = dc - 4
out['sites'] = {k: {'addr': f'0x{a:x}', 'orig': f'0x{w(a):08x}'}
                for k, a in [('P1', p1), ('P2', p2), ('P3', p3), ('P4', p4), ('P5', p5)]}

# a caverna nao pode ser alvo de nenhum desvio vindo de fora dela
c0, c1 = S['ada_demangle'], S['ada_demangle'] + SZ['ada_demangle']
for f0, fsz in FUNCS:
  if c0 <= f0 < c1: continue
  for a in range(f0, f0 + fsz, 4):
    x = w(a); op = x >> 26
    if op == 18:
        d = x & 0x03fffffc
        if d & 0x02000000: d -= 0x04000000
        t = (a + d) & 0xffffffff if not (x & 2) else d & 0xffffffff
        assert not (c0 <= t < c1), f'desvio externo para a caverna em 0x{a:x}'
    elif op == 16:  # desvio condicional
        d = x & 0xfffc
        if d & 0x8000: d -= 0x10000
        t = (a + d) & 0xffffffff if not (x & 2) else d & 0xffffffff
        assert not (c0 <= t < c1), f'desvio condicional externo para a caverna em 0x{a:x}'
print(json.dumps(out, indent=2))
