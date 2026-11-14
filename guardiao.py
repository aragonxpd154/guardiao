#!/usr/bin/env python3
"""
Guardião NetVX — vigia a configuração das SYS (system controller) do Harris NetVX.

Puxa por FTP a configuração gravada no cartão CF de cada SYS, confere se o
arquivo está íntegro, guarda um histórico (só quando muda), avisa quando a
config some/zera e restaura uma versão boa num slot de configuração.

Só usa a biblioteca padrão do Python 3 (testado em 3.8+).

  guardiao.py coletar                 # roda em cron/timer, ex. a cada 10 min
  guardiao.py listar SYS              # histórico de uma SYS
  guardiao.py logs SYS                # baixa e resume os logs /d/log/sys
  guardiao.py restaurar SYS [--snapshot ID] [--slot N] [--carregar]

Configuração: guardiao.ini (ver guardiao.ini.exemplo).
"""
import argparse, configparser, datetime, ftplib, hashlib, io, json, os, re
import shutil, subprocess, sys

VERSAO = '1.1.0'
CFG_FILE = 'etc/cfg/current'
CRC_SYS = {
    0x3016f665: '8.4.7 original Harris', 0x5f22cb81: '8.4.8 original Harris', 0xb5d1a962: '8.6.2-02 original Harris',
    0xc00f6bec: '8.4.7 + patch obel1', 0x193c7811: '8.4.8 + patch obel1', 0xd99fef0d: '8.6.2-02 + patch obel1',
    0x06c408f9: '8.4.7 + obel1 + chkdsk', 0x44384ab6: '8.4.8 + obel1 + chkdsk', 0x8f20c3f2: '8.6.2-02 + obel1 + chkdsk',
}
OID_UTIL_CMD_LOAD = '1.3.6.1.4.1.11268.2.1.1.3.2.5.0'
OID_UTIL_CMD_SAVE = '1.3.6.1.4.1.11268.2.1.1.3.2.7.0'
OK, ALERTA, ERRO = 'OK', 'ALERTA', 'ERRO'

# --------------------------------------------------------------------------
# validação do arquivo de configuração (mesmos critérios do patch obel1)
# --------------------------------------------------------------------------
def validar_cfg(dados):
    """Retorna (valido, motivo, info)."""
    info = {'bytes': len(dados), 'linhas': dados.count(b'\n'),
            'sha256': hashlib.sha256(dados).hexdigest()}
    m = re.match(rb'#cfg=([^\n]*)\n#ident=([^\n]*)\n', dados)
    if m:
        info['cfg'] = m.group(1).decode('latin-1')
        info['ident'] = m.group(2).decode('latin-1')
    if len(dados) == 0:
        return False, 'arquivo vazio (0 bytes)', info
    if not dados.startswith(b'#cfg='):
        return False, 'sem cabeçalho #cfg= (arquivo corrompido)', info
    if b'\0' in dados:
        return False, f'contém bytes nulos a partir do byte {dados.index(bytes(1))} (cluster perdido no CF)', info
    if len(dados) < 64:
        return False, f'truncado ({len(dados)} bytes)', info
    if not dados.endswith(b'\n'):
        return False, 'termina no meio de uma linha (arquivo truncado)', info
    return True, 'íntegro', info

def crc_bzip2(dados):
    tab = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04c11db7) & 0xffffffff if c & 0x80000000 else (c << 1) & 0xffffffff
        tab.append(c)
    c = 0xffffffff
    for b in dados:
        c = ((c << 8) & 0xffffffff) ^ tab[((c >> 24) ^ b) & 0xff]
    return c ^ 0xffffffff

# --------------------------------------------------------------------------
# FTP
# --------------------------------------------------------------------------
class SysFtp:
    """Conexão FTP com uma SYS. Descobre sozinha onde fica o disco (/d)."""
    BASES = ['/d', '', '/', 'd']

    def __init__(self, nome, sec):
        self.nome = nome
        self.host = sec.get('host')
        self.porta = sec.getint('porta', 21)
        self.usuario = sec.get('usuario', 'admin')
        self.senha = sec.get('senha', '')
        self.passivo = sec.getboolean('passivo', True)
        self.timeout = sec.getint('timeout', 20)
        self.ftp = None
        self.base = None

    def __enter__(self):
        self.ftp = ftplib.FTP(timeout=self.timeout)
        self.ftp.connect(self.host, self.porta)
        self.ftp.login(self.usuario, self.senha)
        self.ftp.set_pasv(self.passivo)
        self.ftp.voidcmd('TYPE I')
        for b in self.BASES:
            try:
                self.ftp.cwd(self.p('etc/cfg', b))
                self.base = b
                break
            except ftplib.error_perm:
                continue
        if self.base is None:
            raise RuntimeError('não achei a pasta etc/cfg no FTP da SYS')
        return self

    def __exit__(self, *a):
        try:
            self.ftp.quit()
        except Exception:
            try: self.ftp.close()
            except Exception: pass

    def p(self, rel, base=None):
        b = self.base if base is None else base
        if b in ('', None): return rel
        return b.rstrip('/') + '/' + rel

    def baixar(self, rel):
        buf = io.BytesIO()
        self.ftp.retrbinary('RETR ' + self.p(rel), buf.write)
        return buf.getvalue()

    def tentar_baixar(self, rel):
        try:
            return self.baixar(rel)
        except ftplib.error_perm:
            return None

    def listar(self, rel):
        try:
            nomes = self.ftp.nlst(self.p(rel))
        except ftplib.error_perm:
            return []
        out = []
        for n in nomes:
            n = n.replace('\\', '/').rstrip('/').split('/')[-1]
            if n and n not in ('.', '..'):
                out.append(n)
        return sorted(set(out))

    def enviar(self, rel, dados):
        self.ftp.storbinary('STOR ' + self.p(rel), io.BytesIO(dados))

# --------------------------------------------------------------------------
# armazenamento local
# --------------------------------------------------------------------------
class Arquivo:
    def __init__(self, pasta):
        self.pasta = os.path.expanduser(pasta)
        os.makedirs(self.pasta, exist_ok=True)

    def dir_sys(self, nome):
        d = os.path.join(self.pasta, nome)
        os.makedirs(d, exist_ok=True)
        return d

    def snapshots(self, nome):
        d = self.dir_sys(nome)
        out = []
        for s in sorted(os.listdir(d)):
            mf = os.path.join(d, s, 'meta.json')
            if os.path.isfile(mf):
                with open(mf) as f:
                    out.append((s, json.load(f)))
        return out

    def ultimo(self, nome, so_bom=False):
        for sid, meta in reversed(self.snapshots(nome)):
            if not so_bom or meta.get('cfg_valido'):
                return sid, meta
        return None, None

    def gravar(self, nome, arquivos, meta):
        sid = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        d = os.path.join(self.dir_sys(nome), sid)
        n = 1
        while os.path.exists(d):
            n += 1
            d = os.path.join(self.dir_sys(nome), f'{sid}-{n}')
        sid = os.path.basename(d)
        os.makedirs(d)
        for rel, dados in arquivos.items():
            alvo = os.path.join(d, *rel.split('/'))
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            with open(alvo, 'wb') as f:
                f.write(dados)
        with open(os.path.join(d, 'meta.json'), 'w') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        return sid

    def estado(self, nome, upd=None):
        f = os.path.join(self.dir_sys(nome), 'estado.json')
        est = {}
        if os.path.isfile(f):
            with open(f) as fh: est = json.load(fh)
        if upd:
            est.update(upd)
            with open(f, 'w') as fh: json.dump(est, fh, indent=2, ensure_ascii=False)
        return est

# --------------------------------------------------------------------------
# coleta
# --------------------------------------------------------------------------
def identificar_firmware(s):
    ver = s.tentar_baixar('boot/ver_curr')
    if not ver:
        return None, None
    ver = ver.decode('latin-1').strip().split()[0] if ver.strip() else ''
    man = s.tentar_baixar(f'boot/{ver}/manifest') if ver else None
    if not man:
        return ver, None
    for linha in man.decode('latin-1').splitlines():
        p = linha.split()
        if len(p) == 2 and p[1] == 'sysa12/vxworks':
            crc = int(p[0], 16)
            return ver, CRC_SYS.get(crc, f'desconhecido (CRC 0x{crc:08x})')
    return ver, None

def coletar_sys(nome, sec, arq, geral):
    agora = datetime.datetime.now().isoformat(timespec='seconds')
    arquivos = {}
    with SysFtp(nome, sec) as s:
        for f in s.listar('etc/cfg'):
            d = s.tentar_baixar('etc/cfg/' + f)
            if d is not None:
                arquivos['etc/cfg/' + f] = d
        for rel in ('etc/umconfig.txt', 'etc/snmp/snmpd.cnf', 'etc/brand'):
            d = s.tentar_baixar(rel)
            if d is not None:
                arquivos[rel] = d
        for f in s.listar('etc/profile'):
            d = s.tentar_baixar('etc/profile/' + f)
            if d is not None:
                arquivos['etc/profile/' + f] = d
        ver, fw = identificar_firmware(s)

    cur = arquivos.get(CFG_FILE)
    if cur is None:
        valido, motivo, info = False, 'arquivo current não existe no cartão', {}
    else:
        valido, motivo, info = validar_cfg(cur)
    bak = arquivos.get(CFG_FILE + '.bak')
    bak_ok = validar_cfg(bak)[0] if bak is not None else None

    problemas = []
    if not valido:
        problemas.append(f'current: {motivo}')
    _, bom = arq.ultimo(nome, so_bom=True)
    pct = geral.getfloat('queda_linhas_pct', 30.0)
    if valido and bom and bom.get('cfg_linhas'):
        antes, agora_l = bom['cfg_linhas'], info['linhas']
        if agora_l < antes * (1 - pct / 100.0):
            problemas.append(f'config encolheu de {antes} para {agora_l} linhas '
                             f'(-{100 - 100 * agora_l / antes:.0f}%): possível zeramento')

    hash_total = hashlib.sha256(b''.join(
        k.encode() + b'\0' + v for k, v in sorted(arquivos.items()))).hexdigest()
    meta = {'sys': nome, 'host': sec.get('host'), 'coletado_em': agora,
            'cfg_valido': valido, 'cfg_motivo': motivo, 'cfg_linhas': info.get('linhas'),
            'cfg_bytes': info.get('bytes'), 'cfg_sha256': info.get('sha256'),
            'cfg_ident': info.get('ident'), 'bak_presente': bak is not None, 'bak_valido': bak_ok,
            'firmware_dir': ver, 'firmware_sys': fw, 'hash_total': hash_total,
            'arquivos': sorted(arquivos), 'problemas': problemas}

    sid_ult, meta_ult = arq.ultimo(nome)
    if meta_ult and meta_ult.get('hash_total') == hash_total:
        sid = sid_ult  # nada mudou: não duplica
        novo = False
    else:
        sid = arq.gravar(nome, arquivos, meta)
        novo = True
    arq.estado(nome, {'ultima_coleta': agora, 'ultimo_snapshot': sid,
                      'status': ALERTA if problemas else OK, 'problemas': problemas})
    return meta, sid, novo

def alertar(geral, msg):
    cmd = geral.get('alerta_cmd', '').strip()
    if not cmd:
        return
    env = dict(os.environ, GUARDIAO_MSG=msg)
    try:
        subprocess.run(cmd, shell=True, env=env, timeout=60)
    except Exception as e:
        print(f'  (falha ao chamar alerta_cmd: {e})', file=sys.stderr)

def cmd_coletar(cfg, arq, args):
    geral = cfg['geral']
    resumo, codigo, metas = [], 0, {}
    for nome in sistemas(cfg, args.sys):
        try:
            meta, sid, novo = coletar_sys(nome, cfg[nome], arq, geral)
            metas[nome] = meta
            st = ALERTA if meta['problemas'] else OK
            fw = meta['firmware_sys'] or '?'
            linha = (f'[{st}] {nome}: current {meta["cfg_motivo"]}'
                     f' ({meta["cfg_linhas"] or 0} linhas)'
                     f'; .bak: {"sim" if meta["bak_presente"] else "não"}'
                     f'; firmware SYS: {fw}; snapshot {sid}{" (novo)" if novo else ""}')
            for p in meta['problemas']:
                linha += f'\n       ! {p}'
            if meta['problemas']:
                codigo = max(codigo, 2)
        except Exception as e:
            linha = f'[{ERRO}] {nome}: não consegui coletar: {e}'
            arq.estado(nome, {'status': ERRO, 'problemas': [str(e)]})
            codigo = max(codigo, 1)
        print(linha)
        resumo.append(linha)

    # master x reserva: as duas devem ter o mesmo current
    validas = {n: m for n, m in metas.items() if m['cfg_valido']}
    if len(validas) >= 2 and len({m['cfg_sha256'] for m in validas.values()}) > 1:
        detalhe = ', '.join(f'{n}={m["cfg_linhas"]} linhas' for n, m in validas.items())
        linha = f'[AVISO] o current das SYS não é igual ({detalhe}); normal por alguns minutos após mudanças'
        print(linha)
        resumo.append(linha)
    if codigo:
        alertar(geral, '\n'.join(resumo))
    return codigo

# --------------------------------------------------------------------------
# listar / logs / restaurar
# --------------------------------------------------------------------------
def cmd_listar(cfg, arq, args):
    snaps = arq.snapshots(args.sys)
    if not snaps:
        print('nenhum snapshot ainda'); return 0
    print(f'{"snapshot":17} {"current":9} {"linhas":>7}  {".bak":5} firmware SYS')
    for sid, m in snaps:
        st = 'ok' if m['cfg_valido'] else 'RUIM'
        bak = '-' if not m['bak_presente'] else ('ok' if m['bak_valido'] else 'RUIM')
        print(f'{sid:17} {st:9} {m["cfg_linhas"] or 0:>7}  {bak:5} {m.get("firmware_sys") or "?"}')
    return 0

PADROES_LOG = re.compile(
    r'OBEL:|Db_Sc_Cfg_Load|Failed (loading|saving) configuration|System restarted|'
    r'has become (master|slave)|taking over|giving up|master dead|slave dead|'
    r'Shutting down|Rebooting|Factory load|Slave copy of current cfg|sync_manifest|'
    r'Could not mount|Disk erased|CRC error|FATAL|\bata[A-Z]', re.I)

def cmd_logs(cfg, arq, args):
    destino = os.path.join(arq.dir_sys(args.sys), 'logs',
                           datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    os.makedirs(destino)
    with SysFtp(args.sys, cfg[args.sys]) as s:
        nomes = s.listar('log/sys')
        nomes = nomes[-args.ultimos:]
        for n in nomes:
            d = s.tentar_baixar('log/sys/' + n)
            if d is not None:
                with open(os.path.join(destino, n), 'wb') as f:
                    f.write(d)
    print(f'{len(nomes)} arquivo(s) de log salvos em {destino}\n')
    print('Eventos relevantes (configuração, redundância, disco):')
    for n in sorted(os.listdir(destino)):
        with open(os.path.join(destino, n), 'rb') as f:
            for linha in f.read().decode('latin-1').splitlines():
                if PADROES_LOG.search(linha):
                    print(f'  {n}: {linha.strip()}')
    return 0

def cmd_restaurar(cfg, arq, args):
    nome = args.sys
    if args.snapshot:
        snaps = dict(arq.snapshots(nome))
        if args.snapshot not in snaps:
            print(f'snapshot {args.snapshot} não existe (veja: guardiao.py listar {nome})'); return 1
        sid, meta = args.snapshot, snaps[args.snapshot]
    else:
        sid, meta = arq.ultimo(nome, so_bom=True)
        if not sid:
            print('não há nenhum snapshot com current íntegro'); return 1
    caminho = os.path.join(arq.dir_sys(nome), sid, *CFG_FILE.split('/'))
    with open(caminho, 'rb') as f:
        dados = f.read()
    ok, motivo, info = validar_cfg(dados)
    if not ok:
        print(f'o current do snapshot {sid} não está íntegro ({motivo}); escolha outro'); return 1
    slot = args.slot
    if not 1 <= slot <= 99:
        print('slot deve estar entre 1 e 99'); return 1
    alvo = f'etc/cfg/{slot:02d}'
    print(f'Restaurando {nome}: snapshot {sid} ({info["linhas"]} linhas) -> slot {slot:02d} ({alvo})')
    with SysFtp(nome, cfg[nome]) as s:
        anterior = s.tentar_baixar(alvo)
        if anterior is not None:
            bk = os.path.join(arq.dir_sys(nome), f'slot{slot:02d}-antes-{datetime.datetime.now():%Y%m%d-%H%M%S}')
            with open(bk, 'wb') as f:
                f.write(anterior)
            print(f'  conteúdo anterior do slot guardado em {bk}')
        s.enviar(alvo, dados)
        volta = s.baixar(alvo)
    if volta != dados:
        print('  ERRO: o arquivo lido de volta não confere com o enviado'); return 1
    print('  enviado e conferido (leitura de volta idêntica)')
    if args.carregar:
        com = cfg[nome].get('comunidade_escrita', '')
        if not com:
            print('  defina comunidade_escrita na seção da SYS para usar --carregar'); return 1
        cmd = ['snmpset', '-v2c', '-c', com, cfg[nome]['host'], OID_UTIL_CMD_LOAD, 'i', str(slot)]
        print('  executando: snmpset ... utilCmdLoad.0 =', slot)
        r = subprocess.run(cmd, capture_output=True, text=True)
        print('  ' + (r.stdout or r.stderr).strip())
        return 0 if r.returncode == 0 else 1
    print(f'\nPara aplicar, na SYS master:\n'
          f'  web:    tela de carga de configuração (Load), arquivo {slot:02d}\n'
          f'  console/telnet: util load {slot}\n'
          f'  SNMP:   snmpset -v2c -c <comunidade> {cfg[nome]["host"]} {OID_UTIL_CMD_LOAD} i {slot}')
    return 0

# --------------------------------------------------------------------------
def sistemas(cfg, filtro=None):
    nomes = [s for s in cfg.sections() if s != 'geral']
    if filtro:
        if filtro not in nomes:
            sys.exit(f'SYS "{filtro}" não está no guardiao.ini ({", ".join(nomes)})')
        return [filtro]
    return nomes

def main():
    ap = argparse.ArgumentParser(description='Guardião NetVX — backup e vigilância da config das SYS')
    ap.add_argument('-c', '--config', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'guardiao.ini'))
    ap.add_argument('--versao', action='version', version=f'Guardião NetVX {VERSAO}')
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('coletar', help='coleta e valida a config de todas as SYS (padrão)')
    p.add_argument('--sys')
    p = sub.add_parser('listar', help='histórico de snapshots de uma SYS'); p.add_argument('sys')
    p = sub.add_parser('logs', help='baixa e resume os logs de sistema de uma SYS')
    p.add_argument('sys'); p.add_argument('--ultimos', type=int, default=5)
    p = sub.add_parser('restaurar', help='envia uma config boa para um slot da SYS')
    p.add_argument('sys'); p.add_argument('--snapshot'); p.add_argument('--slot', type=int, default=9)
    p.add_argument('--carregar', action='store_true', help='manda carregar o slot via SNMP (utilCmdLoad)')
    args = ap.parse_args()
    if not os.path.isfile(args.config):
        sys.exit(f'não achei {args.config} (copie guardiao.ini.exemplo para guardiao.ini e preencha)')
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(args.config, encoding='utf-8')
    if 'geral' not in cfg:
        cfg['geral'] = {}
    arq = Arquivo(cfg['geral'].get('pasta', '~/guardiao-netvx/backups'))
    if args.cmd in (None, 'coletar'):
        args.sys = getattr(args, 'sys', None)
        return cmd_coletar(cfg, arq, args)
    if args.cmd == 'listar':
        sistemas(cfg, args.sys); return cmd_listar(cfg, arq, args)
    if args.cmd == 'logs':
        sistemas(cfg, args.sys); return cmd_logs(cfg, arq, args)
    if args.cmd == 'restaurar':
        sistemas(cfg, args.sys); return cmd_restaurar(cfg, arq, args)

if __name__ == '__main__':
    sys.exit(main())
