"""Abre o assistente gráfico contra o simulador (para ver a interface e gerar
as capturas do README):  python3 tests/demo_gui.py [--firmware PASTA] [--auto ETAPAS]"""
import argparse, os, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simulador import Sim, SENHA  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--firmware")
ap.add_argument("--protecao", action="store_true")
ap.add_argument("--escala", default="0.05")
a = ap.parse_args()
sim = Sim(Path(a.firmware) if a.firmware else None, protecao_completa=a.protecao, exigir_cabo=False, queda=1.5)
os.environ.update(GUARDIAO_PORTA_FTP=str(sim.porta_ftp), GUARDIAO_PORTA_TELNET=str(sim.porta_telnet),
                  GUARDIAO_ESCALA_TEMPO=a.escala)
print(f"simulador: ftp {sim.porta_ftp} telnet {sim.porta_telnet}", flush=True)
from guardiao import gui  # noqa: E402
_orig = gui.Janela.__init__


def init(self, app):
    _orig(self, app)
    self.e_ip.set_text("127.0.0.1")
    self.e_senha.set_text(SENHA)
    gui.DEMO = self
gui.Janela.__init__ = init
gui.main([])
