"""
Testa o patch obel1 executando o codigo PowerPC real da imagem corrigida
num emulador (Unicorn), com as funcoes do VxWorks substituidas por stubs
em Python (sistema de arquivos falso em memoria).
"""
import sys, struct
from unicorn import *
from unicorn.ppc_const import *

IMG = sys.argv[1]
data = open(IMG, 'rb').read()
import subprocess,re as _re
_sec=subprocess.run(['readelf','-SW',IMG],capture_output=True,text=True).stdout
_s={m.group(1):(int(m.group(2),16),int(m.group(3),16),int(m.group(4),16)) for m in _re.finditer(r'\]\s+(\.\w+)\s+PROGBITS\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)',_sec)}
TEXT=_s['.text']; DATA=_s['.data']
SYM={}
for _l in subprocess.run(['nm',IMG],capture_output=True,text=True).stdout.splitlines():
    _p=_l.split()
    if len(_p)==3: SYM[_p[2]]=int(_p[0],16)
RET=0x0f000000; STACK=0x0e000000; HEAP=0x0d000000

mu = Uc(UC_ARCH_PPC, UC_MODE_PPC32 | UC_MODE_BIG_ENDIAN)
mu.mem_map(0x0, 0x01000000)                 # text+data+bss (ate 16 MB)
for a,o,s in (TEXT,DATA): mu.mem_write(a, data[o:o+s])
mu.mem_map(HEAP, 0x100000); mu.mem_map(STACK-0x100000, 0x100000); mu.mem_map(RET, 0x1000)

R = {i: globals()[f'UC_PPC_REG_{i}'] for i in range(32)}
def reg(i): return mu.reg_read(R[i])
def setreg(i,v): mu.reg_write(R[i], v & 0xffffffff)
def cstr(a):
    b=bytearray()
    while True:
        c=mu.mem_read(a,1)[0]
        if c==0: return b.decode()
        b.append(c); a+=1

# ---------- sistema de arquivos falso ----------
DISK_OK=[True]; LOAD_R5=[]; FS={}; fds={}; nextfd=[10]; calls=[]; read_fail=set()
def s32(v): return v-(1<<32) if v&0x80000000 else v
def st_open(path, flags):
    if path=='/d' and not DISK_OK[0]: return -1
    if path=='/d': fd=nextfd[0]; nextfd[0]+=1; fds[fd]=[path,0]; return fd
    if path not in FS: return -1
    fd=nextfd[0]; nextfd[0]+=1; fds[fd]=[path,0]; return fd
def stub(name, ret=None):
    def f():
        a=[reg(i) for i in range(3,11)]
        r=0
        if name=='open':
            p=cstr(a[0]); r=st_open(p,a[1]); calls.append(('open',p))
        elif name=='close': fds.pop(a[0],None); calls.append(('close',))
        elif name=='read':
            p,pos=fds[a[0]]
            if p in read_fail and pos>=64: r=-1
            else:
                chunk=FS[p][pos:pos+a[2]]; mu.mem_write(a[1],bytes(chunk)); fds[a[0]][1]+=len(chunk); r=len(chunk)
        elif name=='ioctl': calls.append(('ioctl',fds[a[0]][0],a[1])); r=0
        elif name=='strcmp': r=0 if cstr(a[0])==cstr(a[1]) else 1
        elif name=='OS_Rename':
            s,d=cstr(a[0]),cstr(a[1]); calls.append(('rename',s,d))
            if s in FS: FS[d]=FS.pop(s); r=0
            else: r=-1
        elif name=='Db_Sc_Cfg_Load': calls.append(('LOAD',cstr(a[1]))); LOAD_R5.append(a[2]); r=1
        elif name=='loggerMsg': calls.append(('log',cstr(a[0]).strip()))
        elif name=='taskDelay': calls.append(('taskDelay',a[0]))
        elif name=='Sc_CibResetSys': calls.append(('CIB_RESET',)); r=0
        elif name=='dosFsVolFormat': calls.append(('FORMAT!',)); r=0
        elif name in ('semTake','semGive','Db_Write_Lock','Db_Write_Unlock','Db_Sc_Prevent_File_Access',
                      'Db_Sc_Unprevent_File_Access','cprintf','iosDevFind','Db_Set_Error','write'):
            calls.append((name,)) if name in ('cprintf',) else None
            r = 0 if name!='write' else a[2]
        elif name=='usrAtaConfig': r=0
        elif name=='sprintf':  # so usado p/ nome do tmp no Save
            mu.mem_write(a[0], b'/d/etc/cfg/cfg001.tmp\0'); r=21
        elif name=='strlen': r=len(cstr(a[0]))
        elif name=='creat':
            p=cstr(a[0]); FS[p]=b''; fd=nextfd[0]; nextfd[0]+=1; fds[fd]=[p,0]; r=fd; calls.append(('creat',p))
        elif name=='Db_Sc_Copy_Fd':
            # grava conteudo novo no tmp
            p=fds[a[1]][0]; FS[p]=b'#cfg=8.4.7\n#ident=x\n'+b'cfg/sys/x=1\n'*20; r=1
        if ret is not None: r=ret
        setreg(3, r); mu.reg_write(UC_PPC_REG_PC, mu.reg_read(UC_PPC_REG_LR))
    return f

ADDR={n:SYM[n] for n in ['open', 'close', 'read', 'ioctl', 'strcmp', 'taskDelay', 'loggerMsg', 'OS_Rename', 'Db_Sc_Cfg_Load', 'Sc_CibResetSys', 'dosFsVolFormat', 'semTake', 'semGive', 'Db_Write_Lock', 'Db_Write_Unlock', 'Db_Sc_Prevent_File_Access', 'Db_Sc_Unprevent_File_Access', 'cprintf', 'iosDevFind', 'usrAtaConfig', 'sprintf', 'strlen', 'creat', 'write', 'Db_Set_Error', 'Db_Sc_Copy_Fd']}
STUBS={a:stub(n) for n,a in ADDR.items()}
STOP=[None]
def hook(uc, addr, size, ud):
    if addr in STUBS: STUBS[addr]()
    elif addr==STOP[0]: uc.emu_stop()
mu.hook_add(UC_HOOK_CODE, hook)

def call(fn, *args, stop=RET, start=None):
    for i,v in enumerate(args): setreg(3+i, v)
    setreg(1, STACK-0x1000); mu.reg_write(UC_PPC_REG_LR, RET)
    STOP[0]=stop
    mu.emu_start(start or fn, 0xffffffff, count=5_000_000)
    return s32(reg(3))
def put_str(s):
    mu.mem_write(HEAP, s.encode()+b'\0'); return HEAP

CUR='/d/etc/cfg/current'; BAK='/d/etc/cfg/current.bak'
GOOD=b'#cfg=8.4.7\n#ident=abc\n'+b'cfg/slot.3/enc/vid/rate=19000\n'*50
OLD=b'#cfg=8.4.7\n#ident=old\n'+b'cfg/slot.3/enc/vid/rate=15000\n'*50
NULS=GOOD[:200]+b'\0'*512+GOOD[200:]
fails=0
def check(name, cond):
    global fails
    print(('  PASSOU ' if cond else '  FALHOU ')+name)
    if not cond: fails+=1
def reset(fs):
    FS.clear(); FS.update(fs); calls.clear(); fds.clear()

LOAD_CURRENT=SYM['Db_Sc_Cfg_Load_Current']   # funcao original (com o bl corrigido para obel_cfg_load)
def load_case(fs):
    reset(fs); setreg(29,0); call(LOAD_CURRENT, 0x12345678)
    return [c for c in calls if c[0] in ('LOAD','log')]

print('P3 - carga da configuracao (Db_Sc_Cfg_Load_Current)')
r=load_case({CUR:GOOD, BAK:OLD});    check('current integro -> carrega current', r==[('LOAD',CUR)])
r=load_case({CUR:NULS, BAK:OLD});    check('current com bytes NUL -> carrega .bak', r[-1]==('LOAD',BAK))
r=load_case({CUR:b'', BAK:OLD});     check('current vazio (0 bytes) -> carrega .bak', r[-1]==('LOAD',BAK))
r=load_case({CUR:GOOD[:40], BAK:OLD});check('current truncado (<64 B) -> carrega .bak', r[-1]==('LOAD',BAK))
r=load_case({CUR:b'xxxx'+GOOD, BAK:OLD});check('current sem cabecalho #cfg= -> carrega .bak', r[-1]==('LOAD',BAK))
r=load_case({CUR:GOOD[:len(GOOD)//2+7], BAK:OLD});check('current cortado no meio de uma linha -> carrega .bak', r[-1]==('LOAD',BAK))
r=load_case({BAK:OLD});              check('current ausente -> carrega .bak', r[-1]==('LOAD',BAK))
read_fail.add(CUR)
r=load_case({CUR:GOOD, BAK:OLD});    check('erro de leitura no current -> carrega .bak', r[-1]==('LOAD',BAK))
read_fail.clear()
r=load_case({CUR:NULS, BAK:NULS});   check('current e .bak ruins -> carrega current (igual ao original)', r[-1]==('LOAD',CUR))
r=load_case({CUR:NULS});             check('current ruim e sem .bak -> carrega current (igual ao original)', r[-1]==('LOAD',CUR))
r=load_case({});                     check('nada no cartao -> tenta current (igual ao original)', r[-1]==('LOAD',CUR))
check('arquivos fechados apos validar', not fds)
if any('merge' in k.lower() for k in SYM):   # 8.6.x: Db_Sc_Cfg_Load(user, path, merge)
    check('carga sempre com merge=0 (r5) - 8.6.x', all(x==0 for x in LOAD_R5))

print('P2 - gravacao (Db_Sc_Cfg_Save, rotina original inteira)')
SAVE=SYM['Db_Sc_Cfg_Save']
def save_case(fs, dest):
    reset(fs); FS['/db/cfg']=b''
    p=put_str(dest); r=call(SAVE, 0x1, p)
    return r
r=save_case({CUR:GOOD, BAK:OLD}, CUR)
check('retorna sucesso', r==1)
check('current antigo virou .bak', FS.get(BAK)==GOOD)
check('current novo gravado', FS.get(CUR,b'').startswith(b'#cfg=') and FS[CUR]!=GOOD)
check('FIOSYNC no volume depois do rename', ('ioctl','/d',21) in calls and calls.index(('ioctl','/d',21))>max(i for i,c in enumerate(calls) if c[0]=='rename'))
r=save_case({CUR:NULS, BAK:OLD}, CUR)
check('current corrompido NAO sobrescreve o .bak bom', FS.get(BAK)==OLD and FS[CUR].startswith(b'#cfg='))
r=save_case({}, CUR)
check('primeira gravacao (sem current) funciona', FS.get(CUR,b'').startswith(b'#cfg=') and BAK not in FS)
r=save_case({CUR:GOOD, '/d/etc/cfg/05':OLD}, '/d/etc/cfg/05')
check('util save em slot numerado nao mexe no current/.bak', FS.get(CUR)==GOOD and BAK not in FS and FS['/d/etc/cfg/05'].startswith(b'#cfg='))
check('nenhum tmp sobrando', not any(k.endswith('.tmp') for k in FS))

print('P1 - reset (trecho final de Db_Sc_App_Reset)')
reset({CUR:GOOD})
_ar=SYM['Db_Sc_App_Reset']; _cib=SYM['Sc_CibResetSys']
_bl=[a for a in range(_ar,_ar+0x200,4) if (lambda w:(w&0xfc000003)==0x48000001 and ((a+((w&0x03fffffc)-(0x04000000 if w&0x02000000 else 0)))&0xffffffff)==_cib)(struct.unpack('>I',bytes(mu.mem_read(a,4)))[0])][0]
call(0, start=_bl-8, stop=_bl+4)
seq=[c for c in calls if c[0] in ('ioctl','taskDelay','CIB_RESET')]
check('ordem: FIOSYNC -> taskDelay(10) -> reset CIB', seq==[('ioctl','/d',21),('taskDelay',10),('CIB_RESET',)])

print('P4 - montagem do disco (mountDisk)')
reset({}); DISK_OK[0]=False; r=call(SYM['mountDisk']); DISK_OK[0]=True
check('nao formata o cartao quando /d nao abre', not any(c[0]=='FORMAT!' for c in calls))
check('retorna erro (-1) e avisa no console', r==-1 and ('cprintf',) in calls)

print('\nRESULTADO:', 'TODOS OS TESTES PASSARAM' if fails==0 else f'{fails} FALHA(S)')
sys.exit(1 if fails else 0)
