"""Cores no terminal."""
import sys

COR = sys.stdout.isatty()
NEGRITO, VERDE, AMARELO, CIANO, CINZA, VERMELHO = "1", "32", "33", "36", "90", "31"


def c(txt: str, cod: str) -> str:
    return f"\x1b[{cod}m{txt}\x1b[0m" if COR else txt


def titulo(txt: str):
    print(c(" GUARDIÃO ", "30;43;1") + " " + c(txt, CINZA))


NIVEL = {"ok": VERDE, "aviso": AMARELO, "erro": VERMELHO, "cmd": CIANO, "saida": CINZA, "info": ""}


def log(msg: str, nivel: str = "info"):
    cod = NIVEL.get(nivel, "")
    for linha in str(msg).splitlines() or [""]:
        print(c(linha, cod) if cod else linha, flush=True)
