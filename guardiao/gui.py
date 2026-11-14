"""Assistente gráfico do Guardião (GTK4 + libadwaita).

Uma janela com as etapas à esquerda e, à direita, o que a etapa faz, o botão
para executá-la, o progresso e o registro de tudo o que foi enviado à SYS.
As etapas rodam numa thread; perguntas ao operador viram diálogos.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import __version__, assistente as A, config  # noqa: E402
from .netvx import Chassi, Credenciais  # noqa: E402

APP_ID = "io.github.aragonxpd154.guardiao"

CSS = b"""
.registro { font-family: monospace; font-size: 10pt; }
.etapa-num { font-weight: 800; min-width: 22px; }
.cartao-estado { padding: 6px; }
.sucesso { color: @success_color; }
.alerta { color: @warning_color; }
.falha { color: @error_color; }
.titulo-etapa { font-size: 20pt; font-weight: 800; }
"""

ICONE_STATUS = {
    None: ("content-loading-symbolic", "dim-label"),
    "rodando": ("emblem-synchronizing-symbolic", "accent"),
    A.OK: ("object-select-symbolic", "sucesso"),
    A.AVISO: ("dialog-warning-symbolic", "alerta"),
    A.ERRO: ("dialog-error-symbolic", "falha"),
    A.PULADO: ("go-next-symbolic", "dim-label"),
}


class LinhaEtapa(Gtk.ListBoxRow):
    def __init__(self, n: int, etapa: A.Etapa):
        super().__init__()
        self.etapa = etapa
        caixa = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
        num = Gtk.Label(label=str(n), xalign=0.5)
        num.add_css_class("etapa-num")
        num.add_css_class("dim-label")
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        t = Gtk.Label(label=etapa.titulo, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.sub = Gtk.Label(label="", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.sub.add_css_class("caption")
        self.sub.add_css_class("dim-label")
        textos.append(t)
        textos.append(self.sub)
        self.icone = Gtk.Image()
        caixa.append(num)
        caixa.append(textos)
        caixa.append(self.icone)
        self.set_child(caixa)
        self.definir(None, "")

    def definir(self, status, texto):
        nome, classe = ICONE_STATUS[status]
        self.icone.set_from_icon_name(nome)
        for c in ("dim-label", "accent", "sucesso", "alerta", "falha"):
            self.icone.remove_css_class(c)
        self.icone.add_css_class(classe)
        self.sub.set_label(texto or {None: "pendente", A.PULADO: "não precisou"}.get(status, ""))


class Janela(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Guardião", default_width=1180, default_height=780)
        self.ctx: A.Contexto | None = None
        self.rodando = False
        self.cancelar = threading.Event()
        self.resultados: dict[str, A.Resultado] = {}

        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toast = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=300, max_sidebar_width=340)
        self.toast.set_child(self.split)
        self.set_content(self.toast)

        # ---------------- barra lateral
        lado = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle(title="Guardião", subtitle="SYS do NetVX"))
        menu = Gio.Menu()
        menu.append("Voltar para a versão anterior…", "win.reverter")
        menu.append("Abrir a pasta de backups", "win.pasta")
        menu.append("Editar configuração do Guardião", "win.configurar")
        menu.append("Sobre o Guardião", "win.sobre")
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))
        lado.add_top_bar(hb)
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("navigation-sidebar")
        self.linha_conexao = Gtk.ListBoxRow()
        lc = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
        lc.append(Gtk.Image(icon_name="network-wired-symbolic"))
        self.lbl_conexao = Gtk.Label(label="Conexão", xalign=0, hexpand=True)
        lc.append(self.lbl_conexao)
        self.linha_conexao.set_child(lc)
        self.lista.append(self.linha_conexao)
        self.linhas: list[LinhaEtapa] = []
        for i, et in enumerate(A.ETAPAS, 1):
            ln = LinhaEtapa(i, et)
            self.linhas.append(ln)
            self.lista.append(ln)
        self.lista.connect("row-selected", self._selecionou)
        rolagem = Gtk.ScrolledWindow(vexpand=True, child=self.lista)
        lado.set_content(rolagem)
        self.split.set_sidebar(Adw.NavigationPage(title="Etapas", child=lado))

        # ---------------- conteúdo
        self.pilha = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pilha.add_named(self._pagina_conexao(), "conexao")
        self.pilha.add_named(self._pagina_etapa(), "etapa")
        conteudo = Adw.ToolbarView()
        self.hb2 = Adw.HeaderBar(show_title=False)
        conteudo.add_top_bar(self.hb2)
        conteudo.set_content(self.pilha)
        self.split.set_content(Adw.NavigationPage(title="Guardião", child=conteudo))

        for nome, func in (("reverter", self._reverter), ("pasta", self._abrir_pasta),
                           ("configurar", self._configurar), ("sobre", self._sobre)):
            a = Gio.SimpleAction.new(nome, None)
            a.connect("activate", lambda *_a, f=func: f())
            self.add_action(a)
        self.lista.select_row(self.linha_conexao)

    # ================================================================ páginas
    def _pagina_conexao(self):
        pag = Adw.PreferencesPage()
        topo = Adw.PreferencesGroup()
        cab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=12)
        img = Gtk.Image(icon_name="guardiao", pixel_size=96)
        cab.append(img)
        t = Gtk.Label(label="Atualização segura da SYS")
        t.add_css_class("titulo-etapa")
        cab.append(t)
        d = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, label=(
            "Aplica o patch obel1 na controladora do NetVX, ativa a proteção da reserva e testa o "
            "failover — o mesmo caminho validado no chassi do Morro do Céu, com conferência em "
            "cada passo e volta para a versão original a qualquer momento."))
        d.add_css_class("dim-label")
        cab.append(d)
        topo.add(cab)
        pag.add(topo)

        g = Adw.PreferencesGroup(title="SYS master",
                                 description="IP do sistema (ele acompanha a SYS que está no comando). "
                                             "Usuário e senha do FTP/telnet do equipamento.")
        self.combo_salvos = None
        salvos = config.chassis()
        if salvos:
            nomes = Gtk.StringList()
            for n, c in salvos:
                nomes.append(f"{n} — {c.host}")
            self.combo_salvos = Adw.ComboRow(title="Chassis do Guardião", model=nomes)
            self.combo_salvos.connect("notify::selected", self._escolheu_salvo)
            self._salvos = salvos
            g.add(self.combo_salvos)
        self.e_ip = Adw.EntryRow(title="IP da SYS", text="192.168.101.20")
        self.e_usuario = Adw.EntryRow(title="Usuário", text="admin")
        self.e_senha = Adw.PasswordEntryRow(title="Senha")
        self.sw_passivo = Adw.SwitchRow(title="FTP passivo", subtitle="desligue se a listagem travar",
                                        active=True)
        for w in (self.e_ip, self.e_usuario, self.e_senha, self.sw_passivo):
            g.add(w)
        pag.add(g)
        if salvos:
            self._escolheu_salvo(self.combo_salvos)

        gb = Adw.PreferencesGroup()
        self.bt_conectar = Gtk.Button(label="Conectar e fazer o diagnóstico", halign=Gtk.Align.CENTER)
        self.bt_conectar.add_css_class("suggested-action")
        self.bt_conectar.add_css_class("pill")
        self.bt_conectar.connect("clicked", self._conectar)
        gb.add(self.bt_conectar)
        pag.add(gb)

        self.grupo_estado = Adw.PreferencesGroup(title="Estado do chassi", visible=False)
        self.linhas_estado = {}
        for chave, titulo in (("chassi", "Chassi"), ("slot1", "Slot 1"), ("slot2", "Slot 2"),
                              ("versao", "Versão de boot"), ("firmware", "Firmware da SYS"),
                              ("config", "Configuração"), ("bak", "current.bak"), ("protecao", "Proteção")):
            r = Adw.ActionRow(title=titulo, subtitle="—")
            r.add_css_class("property")
            self.linhas_estado[chave] = r
            self.grupo_estado.add(r)
        pag.add(self.grupo_estado)
        return pag

    def _pagina_etapa(self):
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=18,
                        margin_bottom=18, margin_start=28, margin_end=28)
        cab = Gtk.Box(spacing=16)
        self.img_etapa = Gtk.Image(pixel_size=48)
        cab.append(self.img_etapa)
        tt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.lbl_titulo = Gtk.Label(xalign=0)
        self.lbl_titulo.add_css_class("titulo-etapa")
        self.lbl_desc = Gtk.Label(xalign=0, wrap=True)
        self.lbl_desc.add_css_class("dim-label")
        tt.append(self.lbl_titulo)
        tt.append(self.lbl_desc)
        cab.append(tt)
        caixa.append(cab)

        self.banner = Adw.Banner(revealed=False)
        caixa.append(self.banner)

        botoes = Gtk.Box(spacing=10)
        self.bt_executar = Gtk.Button(label="Executar esta etapa")
        self.bt_executar.add_css_class("suggested-action")
        self.bt_executar.add_css_class("pill")
        self.bt_executar.connect("clicked", lambda *_: self._executar(self._etapa_atual(), seguir=False))
        self.bt_seguir = Gtk.Button(label="Executar daqui até o fim")
        self.bt_seguir.add_css_class("pill")
        self.bt_seguir.set_tooltip_text("Para em cada confirmação e no primeiro erro")
        self.bt_seguir.connect("clicked", lambda *_: self._executar(self._etapa_atual(), seguir=True))
        self.bt_cancelar = Gtk.Button(label="Cancelar", visible=False)
        self.bt_cancelar.add_css_class("destructive-action")
        self.bt_cancelar.add_css_class("pill")
        self.bt_cancelar.connect("clicked", lambda *_: self.cancelar.set())
        for b in (self.bt_executar, self.bt_seguir, self.bt_cancelar):
            botoes.append(b)
        caixa.append(botoes)

        self.prog = Gtk.ProgressBar(show_text=True, visible=False)
        caixa.append(self.prog)

        self.buf = Gtk.TextBuffer()
        for nome, props in (("cmd", {"foreground": "#7DD3FC", "weight": Pango.Weight.BOLD}),
                            ("saida", {"foreground": "#94A3B8"}),
                            ("ok", {"foreground": "#4ADE80", "weight": Pango.Weight.BOLD}),
                            ("aviso", {"foreground": "#FBBF24", "weight": Pango.Weight.BOLD}),
                            ("erro", {"foreground": "#F87171", "weight": Pango.Weight.BOLD}),
                            ("info", {})):
            self.buf.create_tag(nome, **props)
        tv = Gtk.TextView(buffer=self.buf, editable=False, cursor_visible=False, monospace=True,
                          wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                          left_margin=12, right_margin=12)
        tv.add_css_class("registro")
        self.tv = tv
        moldura = Gtk.Frame(child=Gtk.ScrolledWindow(vexpand=True, child=tv))
        caixa.append(moldura)
        return caixa

    # ================================================================ navegação
    def _etapa_atual(self) -> int:
        row = self.lista.get_selected_row()
        return self.linhas.index(row) if isinstance(row, LinhaEtapa) else 0

    def _selecionou(self, _lista, row):
        if row is None:
            return
        if row is self.linha_conexao:
            self.pilha.set_visible_child_name("conexao")
            return
        et = row.etapa
        self.pilha.set_visible_child_name("etapa")
        self.img_etapa.set_from_icon_name(et.icone)
        self.lbl_titulo.set_label(et.titulo)
        self.lbl_desc.set_label(et.descricao)
        r = self.resultados.get(et.id)
        if r:
            self.banner.set_title(r.resumo)
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        self._atualizar_botoes()

    def _atualizar_botoes(self):
        conectado = self.ctx is not None
        for b in (self.bt_executar, self.bt_seguir):
            b.set_sensitive(conectado and not self.rodando)
        self.bt_cancelar.set_visible(self.rodando)
        self.bt_conectar.set_sensitive(not self.rodando)

    def _escolheu_salvo(self, combo, *_):
        n, c = self._salvos[combo.get_selected()]
        self.e_ip.set_text(c.host)
        self.e_usuario.set_text(c.usuario)
        self.e_senha.set_text(c.senha)
        self.sw_passivo.set_active(c.passivo)

    # ================================================================ registro (thread-safe)
    def log(self, msg: str, nivel: str = "info"):
        def f():
            fim = self.buf.get_end_iter()
            self.buf.insert_with_tags_by_name(fim, msg.rstrip("\n") + "\n", nivel)
            self.tv.scroll_to_iter(self.buf.get_end_iter(), 0, False, 0, 1)
        GLib.idle_add(f)

    def progresso(self, frac, texto=""):
        def f():
            self.prog.set_visible(True)
            if frac is None:
                self.prog.pulse()
            else:
                self.prog.set_fraction(max(0.0, min(1.0, frac)))
            self.prog.set_text(texto)
        GLib.idle_add(f)

    def perguntar(self, titulo, texto, opcoes):
        """Chamado pela thread da etapa: mostra um diálogo e espera a resposta."""
        evento, resposta = threading.Event(), {}

        def mostrar():
            d = Adw.AlertDialog(heading=titulo, body=texto)
            for id_, rotulo, estilo in reversed(opcoes):
                d.add_response(id_, rotulo)
                if estilo == "sugerido":
                    d.set_response_appearance(id_, Adw.ResponseAppearance.SUGGESTED)
                elif estilo == "destrutivo":
                    d.set_response_appearance(id_, Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_default_response(opcoes[0][0])
            d.set_close_response(opcoes[-1][0])

            def respondeu(_d, r):
                resposta["r"] = r
                evento.set()
            d.connect("response", respondeu)
            self._dialogo = d
            d.present(self)
        GLib.idle_add(mostrar)
        while not evento.wait(0.2):
            if self.cancelar.is_set():
                return opcoes[-1][0]
        return resposta["r"]

    # ================================================================ ações
    def _credenciais(self) -> Credenciais:
        return Credenciais(self.e_ip.get_text().strip(), self.e_usuario.get_text().strip() or "admin",
                           self.e_senha.get_text(), self.sw_passivo.get_active(),
                           int(os.environ.get("GUARDIAO_PORTA_FTP", "21")),
                           int(os.environ.get("GUARDIAO_PORTA_TELNET", "23")))

    def _novo_contexto(self):
        cred = self._credenciais()
        pasta = config.pasta_dados() / config.secao_de(cred.host)
        pasta.mkdir(parents=True, exist_ok=True)
        escala = float(os.environ.get("GUARDIAO_ESCALA_TEMPO", "1"))
        return A.Contexto(Chassi(cred, self.log), pasta, log=self.log, progresso=self.progresso,
                          perguntar=self.perguntar, cancelado=self.cancelar.is_set, escala=escala)

    def _conectar(self, *_):
        if not self.e_ip.get_text().strip():
            self.toast.add_toast(Adw.Toast(title="Informe o IP da SYS"))
            return
        self.ctx = self._novo_contexto()
        self.resultados.clear()
        for ln in self.linhas:
            ln.definir(None, "")
        self.buf.set_text("")
        self.lista.select_row(self.linhas[0])
        self._executar(0, seguir=False)

    def _executar(self, indice: int, seguir: bool):
        if self.rodando or self.ctx is None:
            return
        self.rodando = True
        self.cancelar.clear()
        self._atualizar_botoes()

        def trabalho():
            i = indice
            while i < len(A.ETAPAS):
                et = A.ETAPAS[i]
                GLib.idle_add(self._marcar, i, "rodando", "executando…")
                GLib.idle_add(self.prog.set_visible, False)
                self.log(f"\n━━ {i + 1}. {et.titulo} ━━", "info")
                r = A.executar(et, self.ctx)
                GLib.idle_add(self._marcar, i, r.status, r.resumo)
                if r.status == A.ERRO or not seguir or self.cancelar.is_set():
                    break
                i += 1
            GLib.idle_add(self._fim)
        threading.Thread(target=trabalho, daemon=True).start()

    def _marcar(self, i, status, texto):
        et = A.ETAPAS[i]
        if status != "rodando":
            self.resultados[et.id] = A.Resultado(status, texto)
        self.linhas[i].definir(status, texto)
        if self.lista.get_selected_row() is not self.linhas[i]:
            self.lista.select_row(self.linhas[i])
        else:
            self._selecionou(self.lista, self.linhas[i])
        if et.id == "diagnostico" and status != "rodando":
            self._mostrar_estado()
        return False

    def _fim(self):
        self.rodando = False
        self.prog.set_visible(False)
        self._atualizar_botoes()
        if self.ctx and self.ctx.estado:
            self._mostrar_estado()
        return False

    def _mostrar_estado(self):
        e = self.ctx.estado if self.ctx else None
        if not e:
            return
        self.grupo_estado.set_visible(True)
        self.lbl_conexao.set_label(f"Conexão — {e.chassi}")
        L = self.linhas_estado
        L["chassi"].set_subtitle(f"{e.chassi} · {e.prompt}")
        for s in (1, 2):
            st = e.slots.get(s, {})
            L[f"slot{s}"].set_subtitle(f"{st.get('type', '?')} · {st.get('version', '?')} · "
                                       f"{st.get('status', '?')} · proteção {st.get('protAvail', '?')}")
        L["versao"].set_subtitle(f"{e.ver_curr} (anterior {e.ver_prev or '—'})")
        L["firmware"].set_subtitle(e.firmware_sys)
        L["config"].set_subtitle(f"{e.cfg.motivo}, {e.cfg.linhas} linhas" if e.cfg else "—")
        L["bak"].set_subtitle(("íntegro" if e.bak.valido else e.bak.motivo) if e.bak else "ainda não existe")
        L["protecao"].set_subtitle({"ativa": "ativa — slot 2 é reserva do slot 1",
                                    "incompleta": "incompleta — reserva encaixada mas não configurada",
                                    "desligada": "desligada", "sem-reserva": "sem reserva no chassi"}[e.protecao])

    def _reverter(self):
        if self.ctx is None:
            self.toast.add_toast(Adw.Toast(title="Conecte na SYS primeiro"))
            return
        self.rodando = True
        self._atualizar_botoes()
        self.pilha.set_visible_child_name("etapa")

        def t():
            r = A.reverter(self.ctx)
            self.log(r.resumo, {"ok": "ok", "erro": "erro"}.get(r.status, "info"))
            GLib.idle_add(self._fim)
        threading.Thread(target=t, daemon=True).start()

    def _abrir_pasta(self):
        Gio.AppInfo.launch_default_for_uri(config.pasta_dados().as_uri(), None)

    def _configurar(self):
        p, _ = config.garantir()
        Gio.AppInfo.launch_default_for_uri(p.as_uri(), None)

    def _sobre(self):
        d = Adw.AboutDialog(application_name="Guardião", application_icon="guardiao", version=__version__,
                            developer_name="Marcos Santos", license_type=Gtk.License.GPL_3_0,
                            website="https://github.com/aragonxpd154/guardiao",
                            issue_url="https://github.com/aragonxpd154/guardiao/issues",
                            comments="Vigia e protege a configuração das SYS do Harris NetVX, "
                                     "e aplica o patch obel1 com segurança.")
        d.present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self):
        tema = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        local = Path(__file__).resolve().parent.parent / "packaging" / "icones"
        if local.is_dir():
            tema.add_search_path(str(local / "256x256"))
        Janela(self).present()


def main(argv=None) -> int:
    return App().run(argv or [])
