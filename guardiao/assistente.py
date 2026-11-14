"""Assistente: a sequência segura de atualização da SYS, etapa por etapa.

É o mesmo caminho validado no chassi do Morro do Céu (nov/2026):
diagnóstico -> backup -> preparar imagem a partir do firmware da PRÓPRIA SYS ->
enviar e conferir byte a byte -> bind -> ativar -> proteção da reserva ->
conferir o current.bak -> teste de failover/failback -> vigiar.

Este módulo não depende de interface: a GUI (gui.py) e os testes chamam as
mesmas etapas. Toda pergunta ao operador passa por ctx.perguntar().
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import patch
from .netvx import Chassi, Credenciais, Estado, _mesmo
from .validacao import eh_obel1, eh_original, validar_cfg

OK, AVISO, ERRO, PULADO = "ok", "aviso", "erro", "pulado"


@dataclass
class Resultado:
    status: str
    resumo: str


class Parar(Exception):
    """Interrompe a etapa com uma mensagem para o operador."""


@dataclass
class Contexto:
    chassi: Chassi
    pasta: Path
    log: callable = lambda msg, nivel="info": None
    progresso: callable = lambda frac, texto="": None
    perguntar: callable = lambda titulo, texto, opcoes: opcoes[0][0]
    cancelado: callable = lambda: False
    escala: float = 1.0                 # testes usam tempos menores
    estado: Estado | None = None
    dados: dict = field(default_factory=dict)

    def t(self, segundos: float) -> float:
        return segundos * self.escala

    def esperar(self, segundos: float):
        fim = time.time() + self.t(segundos)
        while time.time() < fim:
            if self.cancelado():
                raise InterruptedError("cancelado")
            time.sleep(min(0.5, max(0, fim - time.time())))

    def atualizar(self) -> Estado:
        self.estado = self.chassi.estado()
        return self.estado

    def aguardar(self, condicao, segundos: float, o_que: str) -> Estado:
        self.log(f"Aguardando {o_que}…", "info")

        def aviso(msg, resta):
            self.progresso(None, f"{o_que} — {msg} (até {int(resta)} s)")
        e = self.chassi.aguardar(condicao, self.t(segundos), intervalo=max(0.2, self.t(10)),
                                 cancelado=self.cancelado, aviso=aviso)
        self.estado = e
        return e


# ------------------------------------------------------------------ apoio

def _agora() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def resumo_estado(e: Estado) -> list[str]:
    linhas = [f"Chassi {e.chassi or '?'} — no comando: slot {e.slot_ativo or '?'}"]
    for s in (1, 2):
        st = e.slots.get(s, {})
        if st:
            linhas.append(f"  slot {s}: {st.get('type', '?')} · {st.get('version', '?')} · "
                          f"{st.get('status', '?')} · falha {st.get('fault', '?')} · proteção {st.get('protAvail', '?')}")
    linhas.append(f"  versão vinculada: {e.ver_curr or '?'} (anterior: {e.ver_prev or '—'})")
    linhas.append(f"  firmware da SYS: {e.firmware_sys}")
    if e.cfg:
        linhas.append(f"  configuração: {e.cfg.motivo}, {e.cfg.linhas} linhas, rótulo '{e.cfg.tag or ''}'")
    linhas.append(f"  current.bak: {'presente' if e.bak else 'ainda não existe'}")
    nomes = {"ativa": "ativa", "incompleta": "INCOMPLETA (reserva não configurada)",
             "desligada": "desligada", "sem-reserva": "sem reserva no chassi"}
    linhas.append(f"  proteção da SYS: {nomes.get(e.protecao, e.protecao)}")
    return linhas


def reconectar(ctx: Contexto, slot_esperado: int, segundos: float = 60) -> Estado:
    """Depois de failover/failback o IP do sistema passa para a outra SYS.
    Com o notebook ligado direto numa porta, é preciso mudar o cabo."""
    while True:
        fim = time.time() + ctx.t(segundos)
        while time.time() < fim:
            if ctx.cancelado():
                raise InterruptedError("cancelado")
            if ctx.chassi.responde():
                e = ctx.atualizar()
                if e.slot_ativo == slot_esperado:
                    ctx.log(f"Conectado de novo: {e.prompt}", "ok")
                    return e
                ctx.log(f"Respondeu {e.prompt}, aguardando o slot {slot_esperado} assumir…", "info")
            ctx.esperar(3)
        r = ctx.perguntar(
            f"Sem resposta do slot {slot_esperado}",
            f"O IP {ctx.chassi.cred.host} passou para a SYS do slot {slot_esperado}.\n\n"
            f"Se o notebook está ligado direto na porta de rede da SYS, passe o cabo para a "
            f"porta do slot {slot_esperado} e clique em Tentar de novo.",
            [("tentar", "Tentar de novo", "sugerido"), ("parar", "Parar aqui", "")])
        if r != "tentar":
            raise Parar(f"Sem acesso ao slot {slot_esperado}. O chassi segue funcionando com o slot "
                        f"{slot_esperado} no comando; continue quando tiver acesso.")


# ------------------------------------------------------------------ etapas

def etapa_diagnostico(ctx: Contexto) -> Resultado:
    e = ctx.atualizar()
    for l in resumo_estado(e):
        ctx.log(l, "info")
    if not e.prompt or e.slot_ativo is None:
        raise Parar("Não reconheci o console da SYS.")
    if e.slot_ativo != 1:
        raise Parar("O slot 2 está no comando. O procedimento começa com o slot 1 como master: "
                    "rode 'util failback slot.1' ou use o teste de failback no fim do assistente.")
    if not e.cfg or not e.cfg.valido:
        raise Parar(f"A configuração no cartão está ruim ({e.cfg.motivo if e.cfg else 'sem arquivo'}). "
                    "Não atualize assim: restaure a config primeiro (guardiao restaurar).")
    if e.crc_sys is None:
        raise Parar("Não consegui ler o manifest da versão vinculada.")
    if eh_obel1(e.crc_sys):
        ctx.dados["ja_corrigido"] = True
        return Resultado(OK, f"A versão vinculada ({e.ver_curr}) já tem o patch obel1.")
    if not eh_original(e.crc_sys) or patch.versoes().get(e.crc_sys) is None:
        raise Parar(f"Firmware da SYS não suportado: {e.firmware_sys}.")
    ctx.dados["ja_corrigido"] = False
    return Resultado(OK, f"{e.chassi}: {e.firmware_sys}, pronto para atualizar.")


def etapa_backup(ctx: Contexto) -> Resultado:
    e = ctx.estado or ctx.atualizar()
    arqs = ctx.chassi.baixar_config()
    cur = validar_cfg(arqs.get("current"))
    if not cur.valido:
        raise Parar(f"A config atual está ruim ({cur.motivo}); backup recusado.")
    destino = ctx.pasta / f"backup-{_agora()}"
    destino.mkdir(parents=True, exist_ok=True)
    for nome, dados in arqs.items():
        (destino / (nome + (".cfg" if nome in ("current", "current.bak") else ""))).write_bytes(dados)
    ctx.log(f"Config salva no notebook: {destino} ({cur.linhas} linhas)", "ok")
    n = ctx.chassi.slot_livre(e.cfg_arquivos)
    ctx.chassi.salvar_slot(n, cur.tag)
    with ctx.chassi.ftp() as f:
        no_cartao = validar_cfg(f.tentar_baixar(f"etc/cfg/{n:02d}") or f.tentar_baixar(f"etc/cfg/{n}"))
    if not no_cartao.valido:
        raise Parar(f"O 'util save {n}' não gerou um arquivo válido no cartão ({no_cartao.motivo}).")
    ctx.dados["slot_backup"] = n
    ctx.dados["pasta_backup"] = str(destino)
    return Resultado(OK, f"Backup no notebook e no slot {n} da SYS (recupera com 'util load {n}').")


def etapa_preparar(ctx: Contexto) -> Resultado:
    if ctx.dados.get("ja_corrigido"):
        return Resultado(PULADO, "Versão vinculada já corrigida.")
    e = ctx.estado or ctx.atualizar()
    nome = patch.nome_da_versao_corrigida(e.ver_curr)
    ctx.log(f"Baixando a versão {e.ver_curr} do próprio cartão (≈35 MB)…", "info")

    def prog(i, total, rel):
        ctx.progresso(i / total if total else None, f"baixando {rel}")
    boot, app = ctx.chassi.baixar_versao(e.ver_curr, prog)
    ctx.log(f"{len(boot) + len(app)} arquivos baixados, CRCs conferidos com o manifest.", "ok")
    img_original = boot["sysa12/vxworks"]
    img, rel = patch.aplicar(img_original)
    from .validacao import crc_netvx, descrever_firmware
    crc_old, crc_new = crc_netvx(img_original), crc_netvx(img)
    boot["sysa12/vxworks"] = img
    boot["manifest"] = patch.atualizar_manifest(boot["manifest"], crc_old, crc_new)
    erros = patch.conferir_manifest(boot["manifest"], boot.get)
    if erros:
        raise Parar("Manifest da versão corrigida não confere: " + "; ".join(erros))
    for r in rel:
        ctx.log("  " + r, "saida")
    local = ctx.pasta / "firmware" / nome
    for base, arqs in (("boot/" + nome, boot), (nome, app)):
        for relp, dados in arqs.items():
            p = local.parent / base / relp
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(dados)
    ctx.dados.update(nome_novo=nome, boot=boot, app=app, ver_original=e.ver_curr)
    return Resultado(OK, f"{nome}: {descrever_firmware(crc_new)} (cópia em {local.parent}).")


def etapa_enviar(ctx: Contexto) -> Resultado:
    if ctx.dados.get("ja_corrigido"):
        return Resultado(PULADO, "Nada a enviar.")
    nome, boot, app = ctx.dados["nome_novo"], ctx.dados["boot"], ctx.dados["app"]
    with ctx.chassi.ftp() as f:
        existente = f.tentar_baixar(f"boot/{nome}/sysa12/vxworks")
    if existente == boot["sysa12/vxworks"] and not ctx.chassi.conferir_enviada(nome, boot, app):
        return Resultado(OK, f"{nome} já está no cartão, idêntica. Envio dispensado.")
    total = sum(len(v) for v in boot.values()) + sum(len(v) for v in app.values())
    enviado = [0]

    def prog(feito, _tot, rel):
        ctx.progresso(min(1.0, (enviado[0] + feito) / total), f"enviando {rel}")
    ctx.log(f"Enviando {nome} para a master ({total // 1024} KB)…", "info")
    with ctx.chassi.ftp() as f:
        f.enviar_arvore(boot, f"boot/{nome}", prog)
        enviado[0] = sum(len(v) for v in boot.values())
        f.enviar_arvore(app, nome, prog)
    erros = ctx.chassi.conferir_enviada(nome, boot, app)
    if erros:
        raise Parar("Conferência do envio falhou: " + "; ".join(erros))
    return Resultado(OK, f"{nome} enviada e conferida byte a byte (sysa12/vxworks idêntico).")


def etapa_vincular(ctx: Contexto) -> Resultado:
    if ctx.dados.get("ja_corrigido"):
        return Resultado(PULADO, "Já vinculada.")
    e = ctx.atualizar()
    nome = ctx.dados["nome_novo"]
    sairao = [n for n, _s in e.versoes if not _mesmo(n, e.ver_curr) and not _mesmo(n, nome)]
    texto = (f"Vincular a {nome} como versão de boot. Não reinicia nada: a SYS continua "
             f"rodando a {e.ver_curr} até o próximo reset, e a {e.ver_curr} vira a versão anterior "
             f"(rollback).")
    if sairao:
        texto += f"\n\nO NetVX guarda só a atual e a anterior: {', '.join(sairao)} sai do cartão."
    if ctx.perguntar("Vincular a versão corrigida?", texto,
                     [("sim", "Vincular", "sugerido"), ("nao", "Agora não", "")]) != "sim":
        raise Parar("Bind não feito.")
    lista = ctx.chassi.vincular(nome)
    ctx.log("Versões: " + ", ".join(f"{n} ({s})" if s else n for n, s in lista), "info")
    return Resultado(OK, f"Bind feito: {nome} atual, {ctx.dados['ver_original']} anterior.")


def _rodando_em_todas(e: Estado, nome: str) -> bool:
    if not (e.em_servico(1) and _mesmo(e.versao_rodando(1), nome)):
        return False
    if e.tem_reserva_fisica and not _mesmo(e.versao_rodando(2), nome):
        return False
    return True


def etapa_ativar(ctx: Contexto) -> Resultado:
    e = ctx.atualizar()
    nome = e.ver_curr
    if _rodando_em_todas(e, nome):
        return Resultado(PULADO, f"As SYS já rodam a {nome}.")
    opcoes = [("reset", "Reiniciar o sistema (1–2 min fora do ar)", "destrutivo")]
    texto = (f"As SYS vão subir na {nome}.\n\n• Reiniciar o sistema: tudo sobe junto, com 1 a 2 "
             f"minutos fora do ar. É o caminho das release notes da 8.6.2.")
    if e.protecao == "ativa":
        opcoes.insert(0, ("sem_queda", "Sem queda: uma controladora de cada vez", "sugerido"))
        texto += ("\n• Sem queda: reinicia a reserva, troca o comando (failover), reinicia o slot 1 "
                  "e devolve (failback). Pode pedir para mudar o cabo do notebook de porta.")
    opcoes.append(("depois", "Agora não", ""))
    escolha = ctx.perguntar("Ativar a versão corrigida", texto, opcoes)
    if escolha == "depois":
        raise Parar("Ativação adiada. A versão está vinculada e sobe no próximo reset.")
    if escolha == "reset":
        ctx.chassi.reset_sistema()
        ctx.esperar(30)
        ctx.aguardar(lambda s: _rodando_em_todas(s, nome), 600, "as SYS voltarem na versão nova")
    else:
        ctx.chassi.reset_slot(2)
        ctx.esperar(20)
        ctx.aguardar(lambda s: s.em_servico(2) and _mesmo(s.versao_rodando(2), nome), 900,
                     "a reserva sincronizar e subir na versão nova")
        ctx.chassi.failover(1)
        reconectar(ctx, 2)
        ctx.chassi.reset_slot(1)
        ctx.esperar(20)
        ctx.aguardar(lambda s: s.em_servico(1) and _mesmo(s.versao_rodando(1), nome) and s.slot_ativo == 2,
                     900, "o slot 1 subir na versão nova")
        ctx.chassi.failback(1)
        reconectar(ctx, 1)
    e = ctx.atualizar()
    for l in resumo_estado(e):
        ctx.log(l, "info")
    if not _rodando_em_todas(e, nome):
        raise Parar("As SYS não ficaram todas na versão nova. Veja o estado acima.")
    return Resultado(OK, f"Rodando a {nome}: {e.firmware_sys}.")


def etapa_protecao(ctx: Contexto) -> Resultado:
    e = ctx.atualizar()
    if e.protecao == "sem-reserva":
        return Resultado(PULADO, "Não há SYS reserva em serviço neste chassi.")
    if e.protecao == "ativa":
        return Resultado(OK, "Proteção ativa: o slot 2 é reserva do slot 1.")
    tipo = e.slots[2].get("type", "sys-a22-std")
    texto = (f"A reserva está encaixada e em serviço, mas a configuração não a usa "
             f"(slot.1 protMode={e.cfg_params.get('slot.1/gen/protMode', '?')}, "
             f"slot.2 type={e.cfg_params.get('slot.2/gen/type', '?')}).\n\n"
             f"Vou configurar o slot 2 como {tipo}. Isso grava a config — e é o primeiro teste "
             f"real do current.bak.")
    if ctx.perguntar("Ativar a proteção da SYS?", texto,
                     [("sim", "Configurar a reserva", "sugerido"), ("nao", "Agora não", "")]) != "sim":
        return Resultado(AVISO, "Proteção continua incompleta.")
    if e.cfg_params.get("slot.2/gen/type", "none") in ("", "none"):
        ctx.chassi.configurar_reserva(tipo)
    if not e.cfg_params.get("slot.1/gen/protMode", "").startswith("protectedBy"):
        ctx.chassi.comando("cfg slot.1/gen protMode=protectedBy-2")
    e = ctx.aguardar(lambda s: s.protecao == "ativa", 120, "a proteção ficar disponível")
    return Resultado(OK, "Proteção ativa: slot 1 protegido pelo slot 2.")


def etapa_bak(ctx: Contexto) -> Resultado:
    e = ctx.atualizar()
    if not eh_obel1(e.crc_sys or 0):
        return Resultado(AVISO, "A versão em uso não tem o patch; o current.bak não é gerado.")
    try:
        e = ctx.aguardar(lambda s: s.bak is not None, 240, "o current.bak aparecer")
    except TimeoutError:
        return Resultado(AVISO, "O current.bak ainda não existe: ele aparece na próxima "
                                "alteração de configuração.")
    if not e.bak.valido:
        return Resultado(AVISO, f"current.bak presente, mas {e.bak.motivo}.")
    return Resultado(OK, f"current ({e.cfg.linhas} linhas) e current.bak ({e.bak.linhas} linhas) "
                         f"íntegros: a SYS guarda a config anterior a cada gravação.")


def etapa_teste_failover(ctx: Contexto) -> Resultado:
    e = ctx.atualizar()
    if e.protecao != "ativa":
        return Resultado(PULADO, "Sem proteção ativa, não há failover para testar.")
    texto = ("Vai passar o comando para o slot 2, conferir a configuração e devolver para o slot 1.\n\n"
             "O failover não deve derrubar o ar, mas faça com alguém olhando o sinal. Se o chassi "
             "usa Simulcrypt, o bug 13102 da Harris faz ele parar após o failover.\n\n"
             "Com o notebook ligado direto na porta da SYS, vou pedir para mudar o cabo duas vezes.")
    if ctx.perguntar("Testar failover e failback?", texto,
                     [("sim", "Testar agora", "sugerido"), ("nao", "Pular", "")]) != "sim":
        return Resultado(PULADO, "Teste não feito.")
    antes = e.cfg.linhas if e.cfg else 0
    ctx.chassi.failover(1)
    e2 = reconectar(ctx, 2)
    if not e2.cfg or not e2.cfg.valido or e2.cfg.linhas < antes * 0.9:
        raise Parar(f"Depois do failover a config ficou {e2.cfg.motivo if e2.cfg else '?'} "
                    f"({e2.cfg.linhas if e2.cfg else 0} linhas, antes {antes}). Não faça o failback; "
                    "restaure pelo slot de backup.")
    ctx.log(f"Config no slot 2: {e2.cfg.linhas} linhas, {e2.cfg.motivo}.", "ok")
    ctx.chassi.failback(1)
    e3 = reconectar(ctx, 1)
    e3 = ctx.aguardar(lambda s: s.protecao == "ativa", 300, "a reserva voltar a ficar disponível")
    return Resultado(OK, f"Failover e failback ok; config preservada ({e3.cfg.linhas} linhas).")


def etapa_vigiar(ctx: Contexto) -> Resultado:
    from . import config, monitor
    e = ctx.estado or ctx.atualizar()
    nome = config.adicionar_chassi(e.chassi or ctx.chassi.cred.host, ctx.chassi.cred)
    ativo = monitor.ativar_timer()
    return Resultado(OK, f"Chassi '{nome}' incluído no Guardião"
                         + (" (verificação a cada 10 min ativa)." if ativo else "."))


@dataclass
class Etapa:
    id: str
    titulo: str
    descricao: str
    func: callable
    icone: str = "emblem-ok-symbolic"


ETAPAS = [
    Etapa("diagnostico", "Diagnóstico", "Conecta na master, lê as SYS, versões, proteção e a config.", etapa_diagnostico, "network-server-symbolic"),
    Etapa("backup", "Backup da configuração", "Copia a config para o notebook e para um slot da própria SYS.", etapa_backup, "document-save-symbolic"),
    Etapa("preparar", "Preparar a versão corrigida", "Baixa o firmware do cartão e aplica o patch obel1 nele.", etapa_preparar, "applications-engineering-symbolic"),
    Etapa("enviar", "Enviar para a master", "FTP da versão corrigida, com conferência byte a byte.", etapa_enviar, "send-to-symbolic"),
    Etapa("vincular", "Vincular (bind)", "util firmware: a versão corrigida passa a ser a de boot.", etapa_vincular, "emblem-default-symbolic"),
    Etapa("ativar", "Ativar", "Sobe as SYS na versão nova: reset do sistema ou sem queda.", etapa_ativar, "view-refresh-symbolic"),
    Etapa("protecao", "Proteção da reserva", "Garante que o slot 2 é reserva configurada do slot 1.", etapa_protecao, "security-high-symbolic"),
    Etapa("bak", "Conferir o current.bak", "Confirma que a SYS guarda a config anterior ao gravar.", etapa_bak, "edit-copy-symbolic"),
    Etapa("failover", "Teste de failover", "Troca o comando para o slot 2 e devolve, conferindo a config.", etapa_teste_failover, "media-playlist-repeat-symbolic"),
    Etapa("vigiar", "Vigiar este chassi", "Inclui no Guardião: coleta a cada 10 min e aviso se zerar.", etapa_vigiar, "preferences-system-notifications-symbolic"),
]


def executar(etapa: Etapa, ctx: Contexto) -> Resultado:
    try:
        r = etapa.func(ctx)
    except Parar as p:
        r = Resultado(ERRO, str(p))
    except InterruptedError:
        r = Resultado(ERRO, "Cancelado pelo operador.")
    except TimeoutError as t:
        r = Resultado(ERRO, f"Tempo esgotado: {t}")
    except patch.ErroPatch as p:
        r = Resultado(ERRO, f"Patch: {p}")
    except Exception as ex:  # noqa: BLE001 — qualquer falha vira mensagem, nunca trava a GUI
        r = Resultado(ERRO, f"{ex.__class__.__name__}: {ex}")
    nivel = {OK: "ok", AVISO: "aviso", ERRO: "erro", PULADO: "info"}[r.status]
    ctx.log(r.resumo, nivel)
    return r


def reverter(ctx: Contexto) -> Resultado:
    """Rollback: volta para a versão anterior (original) e reinicia o sistema."""
    try:
        e = ctx.atualizar()
        alvo = e.ver_prev
        if not alvo:
            raise Parar("Não há versão anterior registrada.")
        if ctx.perguntar("Voltar para a versão anterior?",
                         f"Vincula a {alvo} e reinicia o sistema (1–2 min fora do ar).",
                         [("sim", "Voltar", "destrutivo"), ("nao", "Cancelar", "")]) != "sim":
            return Resultado(PULADO, "Rollback cancelado.")
        ctx.chassi.comando(f"util firmware {alvo}", timeout=300)
        ctx.chassi.reset_sistema()
        ctx.esperar(30)
        ctx.aguardar(lambda s: _rodando_em_todas(s, alvo), 600, "as SYS voltarem na versão anterior")
        return Resultado(OK, f"Voltou para a {alvo}.")
    except Exception as ex:  # noqa: BLE001
        return Resultado(ERRO, str(ex))
