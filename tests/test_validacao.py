import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardiao import validacao as V  # noqa: E402
from guardiao.console import ler_firmware, ler_stat  # noqa: E402

BOM = b"#cfg=#11CEU_25Marc10\n#ident=i.g.c\n" + b"slot.1/gen/type=sys-a22-std\n" * 50


class Crc(unittest.TestCase):
    def test_rapido_igual_ao_lento(self):
        for d in (b"", b"a", b"123456789", os.urandom(5000)):
            self.assertEqual(V.crc_netvx(d), V._crc_lento(d))

    def test_valor_de_referencia_bzip2(self):
        self.assertEqual(V.crc_netvx(b"123456789"), 0xFC891918)   # CRC-32/BZIP2 check

    def test_descricao(self):
        self.assertEqual(V.descrever_firmware(0xb5d1a962), "8.6.2-02 original Harris")
        self.assertTrue(V.eh_obel1(0xd99fef0d))
        self.assertIn("desconhecido", V.descrever_firmware(1))


class Config(unittest.TestCase):
    def test_bom(self):
        r = V.validar_cfg(BOM)
        self.assertTrue(r.valido)
        self.assertEqual((r.tag, r.ident, r.linhas), ("#11CEU_25Marc10", "i.g.c", 52))

    def test_ruins(self):
        casos = {
            None: "não existe", b"": "vazio", b"xx" + BOM: "cabeçalho", BOM[:40]: "truncado",
            BOM[:200] + b"\0" * 64 + BOM[200:]: "nulos", BOM[:-5]: "meio de uma linha",
        }
        for dados, trecho in casos.items():
            r = V.validar_cfg(dados)
            self.assertFalse(r.valido, trecho)
            self.assertIn(trecho, r.motivo)

    def test_parametros(self):
        p = V.ler_parametros(BOM + b"slot.2/gen/type=none\n")
        self.assertEqual(p["slot.2/gen/type"], "none")


class Console(unittest.TestCase):
    def test_stat_real(self):
        texto = """/stat/slot.2/gen:
                type = sys-a22-std
             version = v8-6-2.ob1
              status = inService
           protAvail = unneeded"""
        s = ler_stat(texto)
        self.assertEqual((s["version"], s["protAvail"]), ("v8-6-2.ob1", "unneeded"))

    def test_firmware_real(self):
        texto = "Available boot versions are:\n\tV8-6-2.02       (previous)\n\tv8-6-2.ob1      (current)\n"
        self.assertEqual(ler_firmware(texto), [("V8-6-2.02", "previous"), ("v8-6-2.ob1", "current")])


if __name__ == "__main__":
    unittest.main()
