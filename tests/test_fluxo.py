"""Fluxo completo do assistente contra o simulador de NetVX.

- FluxoSintetico: roda em qualquer lugar (CI). Usa uma imagem falsa e um patch
  falso, registrados só durante o teste.
- FluxoFirmwareReal: usa o firmware de verdade se GUARDIAO_FIRMWARE apontar
  para uma pasta com boot/v8-6-2.02 e v8-6-2.02 (cópia do cartão da SYS).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from simulador import Sim
except ImportError:  # pyftpdlib ausente
    Sim = None

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardiao import assistente as A, patch, validacao  # noqa: E402
from guardiao.netvx import Chassi  # noqa: E402

VER = "v8-6-2.02"


def firmware_sintetico(raiz: Path) -> bytes:
    boot, app = raiz / "boot" / VER, raiz / VER
    arqs_boot = {"sysa12/vxworks": b"\x7fELF-falso-" + os.urandom(4000), "mux/app_main": os.urandom(3000)}
    arqs_app = {"title.txt": b"Harris NetVX (TM) | VideoRunner\n8.6.2\n", "tcl/init.tcl": b"# tcl\n"}
    for base, arqs in ((boot, arqs_boot), (app, arqs_app)):
        for rel, d in arqs.items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_bytes(d)
        man = "".join(f"0x{validacao.crc_netvx(d):08x}     {rel}\n" for rel, d in sorted(arqs.items()))
        (base / "manifest").write_text(man)
    return arqs_boot["sysa12/vxworks"]


class Operador:
    """Responde às perguntas do assistente como o operador faria."""

    def __init__(self, sim, ativar="reset", testar_failover=True):
        self.sim, self.ativar, self.testar = sim, ativar, testar_failover
        self.perguntas = []

    def __call__(self, titulo, texto, opcoes):
        self.perguntas.append(titulo)
        ids = [o[0] for o in opcoes]
        if titulo.startswith("Sem resposta do slot"):
            self.sim.cabo = int(titulo.split()[-1])
            return "tentar"
        if titulo.startswith("Ativar a versão"):
            return self.ativar if self.ativar in ids else ids[0]
        if titulo.startswith("Testar failover"):
            return "sim" if self.testar else "nao"
        return ids[0]


@unittest.skipIf(Sim is None, "pyftpdlib não instalado")
class Base(unittest.TestCase):
    def rodar(self, sim, operador, etapas=None):
        pasta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, pasta, True)
        logs = []
        ctx = A.Contexto(Chassi(sim.credenciais()), pasta, log=lambda m, n="info": logs.append((n, m)),
                         perguntar=operador, escala=0.05)
        res = {}
        for et in A.ETAPAS:
            if et.id == "vigiar" or (etapas and et.id not in etapas):
                continue
            r = A.executar(et, ctx)
            res[et.id] = r
            if r.status == A.ERRO:
                self.fail(f"etapa {et.id}: {r.resumo}\n" + "\n".join(m for _, m in logs[-25:]))
        return res, ctx, logs


class FluxoSintetico(Base):
    def setUp(self):
        raiz = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, raiz, True)
        img = firmware_sintetico(raiz)
        crc0 = validacao.crc_netvx(img)
        corrigida = img + b"OBEL1"
        crc1 = validacao.crc_netvx(corrigida)
        self._fw = dict(validacao.FIRMWARE_SYS)
        validacao.FIRMWARE_SYS.update({crc0: "8.6.2-02 original Harris", crc1: "8.6.2-02 + patch obel1"})
        self._aplicar, self._versoes = patch.aplicar, patch.versoes
        patch.versoes = lambda: {crc0: object()}
        patch.aplicar = lambda i, chkdsk=False: (i + b"OBEL1", ["P1..P4 (teste)"]) if i == img else (_ for _ in ()).throw(patch.ErroPatch("x"))
        self.raiz = raiz

    def tearDown(self):
        validacao.FIRMWARE_SYS.clear()
        validacao.FIRMWARE_SYS.update(self._fw)
        patch.aplicar, patch.versoes = self._aplicar, self._versoes

    def test_reset_do_sistema_com_protecao_incompleta(self):
        """O caso do Morro do Céu: reserva encaixada mas slot.2 type=none."""
        sim = Sim(self.raiz, VER, exigir_cabo=True, queda=0.3)
        self.addCleanup(sim.fechar)
        op = Operador(sim, ativar="reset")
        res, ctx, _ = self.rodar(sim, op)
        self.assertEqual(sim.ver("ver_curr"), "v8-6-2.ob1")
        self.assertEqual(sim.ver("ver_prev"), VER)
        self.assertEqual(sim.rodando, {1: "v8-6-2.ob1", 2: "v8-6-2.ob1"})
        self.assertNotIn("Sem queda: uma controladora de cada vez", op.perguntas)  # sem proteção: só reset
        self.assertIn("util reset sys", sim.historico)
        self.assertIn("cfg slot.2/gen type=sys-a22-std", sim.historico)
        self.assertTrue((sim.card / "etc/cfg/current.bak").exists())
        self.assertEqual(res["bak"].status, A.OK)
        self.assertEqual(res["failover"].status, A.OK)
        self.assertIn("util failback slot.1", sim.historico)          # e não slot.2
        self.assertEqual(sim.ativo, 1)
        # rótulo da config preservado no util save
        self.assertTrue(sim.cfg().startswith(b"#cfg=#11CEU_25Marc10\n"))
        self.assertTrue(any(c.startswith("util save 90 #11CEU_25Marc10") for c in sim.historico))

    def test_sem_queda_com_protecao_ativa(self):
        sim = Sim(self.raiz, VER, protecao_completa=True, exigir_cabo=True, queda=0.3)
        self.addCleanup(sim.fechar)
        res, ctx, _ = self.rodar(sim, Operador(sim, ativar="sem_queda", testar_failover=False))
        self.assertNotIn("util reset sys", sim.historico)
        self.assertEqual([c for c in sim.historico if c.startswith(("util reset", "util fail"))],
                         ["util reset slot.2", "util failover slot.1", "util reset slot.1", "util failback slot.1"])
        self.assertEqual(sim.rodando, {1: "v8-6-2.ob1", 2: "v8-6-2.ob1"})
        self.assertEqual(sim.ativo, 1)

    def test_segunda_execucao_nao_refaz_nada(self):
        sim = Sim(self.raiz, VER, queda=0.3)
        self.addCleanup(sim.fechar)
        self.rodar(sim, Operador(sim, testar_failover=False))
        n = len(sim.historico)
        res, _, _ = self.rodar(sim, Operador(sim, testar_failover=False))
        for et in ("preparar", "enviar", "vincular", "ativar"):
            self.assertEqual(res[et].status, A.PULADO, et)
        self.assertFalse(any(c.startswith(("util reset", "util firmware v")) for c in sim.historico[n:]))

    def test_config_ruim_bloqueia(self):
        sim = Sim(self.raiz, VER, queda=0.3)
        self.addCleanup(sim.fechar)
        (sim.card / "etc/cfg/current").write_bytes(b"#cfg=x\n#ident=y\n" + b"\0" * 500)
        ctx = A.Contexto(Chassi(sim.credenciais()), Path(tempfile.mkdtemp()), perguntar=Operador(sim), escala=0.05)
        r = A.executar(A.ETAPAS[0], ctx)
        self.assertEqual(r.status, A.ERRO)
        self.assertIn("bytes nulos", r.resumo)

    def test_rollback(self):
        sim = Sim(self.raiz, VER, queda=0.3)
        self.addCleanup(sim.fechar)
        _, ctx, _ = self.rodar(sim, Operador(sim, testar_failover=False))
        r = A.reverter(ctx)
        self.assertEqual(r.status, A.OK, r.resumo)
        self.assertEqual(sim.rodando[1], VER)


@unittest.skipUnless(os.environ.get("GUARDIAO_FIRMWARE"), "defina GUARDIAO_FIRMWARE para testar com o firmware real")
class FluxoFirmwareReal(Base):
    def test_fluxo_completo(self):
        raiz = Path(os.environ["GUARDIAO_FIRMWARE"])
        sim = Sim(raiz, VER, exigir_cabo=True, queda=0.5)
        self.addCleanup(sim.fechar)
        res, ctx, logs = self.rodar(sim, Operador(sim, ativar="reset"))
        vx = (sim.card / "boot/v8-6-2.ob1/sysa12/vxworks").read_bytes()
        self.assertEqual(validacao.crc_netvx(vx), 0xd99fef0d)
        self.assertEqual(sim.ver("ver_curr"), "v8-6-2.ob1")
        self.assertEqual(res["failover"].status, A.OK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
