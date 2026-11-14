"""Gera as capturas de tela do README rodando o assistente contra o simulador
num display virtual:  xvfb-run -s '-screen 0 1280x860x24' python3 tests/capturas.py SAIDA [FIRMWARE]"""
import os, subprocess, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simulador import Sim, SENHA  # noqa: E402

saida = Path(sys.argv[1]); saida.mkdir(parents=True, exist_ok=True)
fw = Path(sys.argv[2]) if len(sys.argv) > 2 else None
sim = Sim(fw, exigir_cabo=False, queda=1.0)
os.environ.update(GUARDIAO_PORTA_FTP=str(sim.porta_ftp), GUARDIAO_PORTA_TELNET=str(sim.porta_telnet),
                  GUARDIAO_ESCALA_TEMPO="0.03", GUARDIAO_CONFIG=str(sim.dir / "cfg.ini"))
from gi.repository import GLib  # noqa: E402
from guardiao import gui, assistente as A  # noqa: E402

J = {}
_orig = gui.Janela.__init__


def init(self, app):
    _orig(self, app)
    self.set_default_size(1180, int(os.environ.get("ALTURA", "780")))
    self.e_ip.set_text("127.0.0.1"); self.e_senha.set_text(SENHA)
    J["w"] = self
gui.Janela.__init__ = init

perguntas = []
_perg = gui.Janela.perguntar


def perguntar(self, titulo, texto, opcoes):
    perguntas.append(titulo)
    escolha = "reset" if titulo.startswith("Ativar a versão") else opcoes[0][0]

    def responder():
        time.sleep(1.5)
        if titulo.startswith("Ativar a versão"):
            foto("3-pergunta")
        d = self._dialogo
        GLib.idle_add(lambda: (d.emit("response", escolha), d.force_close()) and False)
    threading.Thread(target=responder, daemon=True).start()
    return _perg(self, titulo, texto, opcoes)
gui.Janela.perguntar = perguntar


def foto(nome):
    subprocess.run(["import", "-window", "root", str(saida / f"{nome}.png")], check=False)


def roteiro():
    time.sleep(2.5); foto("0-inicio")
    GLib.idle_add(J["w"]._conectar)
    while not J["w"].resultados.get("diagnostico"):
        time.sleep(0.2)
    time.sleep(0.8)
    GLib.idle_add(J["w"].lista.select_row, J["w"].linha_conexao); time.sleep(0.8); foto("1-diagnostico")
    GLib.idle_add(J["w"].lista.select_row, J["w"].linhas[1]); time.sleep(0.5)
    GLib.idle_add(J["w"]._executar, 1, True)
    while "preparar" not in J["w"].resultados or J["w"].rodando and "enviar" not in J["w"].resultados:
        time.sleep(0.3)
    while J["w"].rodando:
        time.sleep(0.3)
    time.sleep(1.0)
    GLib.idle_add(J["w"].lista.select_row, J["w"].linhas[5]); time.sleep(0.8); foto("4-ativar")
    GLib.idle_add(J["w"].lista.select_row, J["w"].linhas[8]); time.sleep(0.8); foto("5-failover")
    GLib.idle_add(J["w"].lista.select_row, J["w"].linha_conexao); time.sleep(0.8); foto("6-final")
    print("perguntas:", perguntas); print({k: (v.status, v.resumo) for k, v in J["w"].resultados.items()})
    GLib.idle_add(J["w"].get_application().quit)

threading.Thread(target=roteiro, daemon=True).start()
gui.main([])
sim.fechar()
