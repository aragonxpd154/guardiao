import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardiao import patch  # noqa: E402
from guardiao.validacao import crc_netvx  # noqa: E402

ESPERADO = {0x3016f665: 0xc00f6bec, 0x5f22cb81: 0x193c7811, 0xb5d1a962: 0xd99fef0d}


class Basico(unittest.TestCase):
    def test_versoes_empacotadas(self):
        vs = patch.versoes()
        self.assertEqual(set(vs), set(ESPERADO))
        for v in vs.values():
            self.assertLessEqual(len(v.cave_bin), v.dados["cave_size"])
            self.assertTrue({"obel_sync_delay", "obel_cfg_commit", "obel_cfg_load", "obel_cfg_valid"} <= set(v.cave_syms))

    def test_nome_8_3(self):
        self.assertEqual(patch.nome_da_versao_corrigida("v8-6-2.02"), "v8-6-2.ob1")
        self.assertEqual(patch.nome_da_versao_corrigida("V8-4-8"), "V8-4-8.ob1")

    def test_manifest(self):
        man = b"0x11111111     tcl/x\n0x3016f665     sysa12/vxworks\n"
        novo = patch.atualizar_manifest(man, 0x3016f665, 0xc00f6bec)
        self.assertEqual(novo, b"0x11111111     tcl/x\n0xc00f6bec     sysa12/vxworks\n")
        with self.assertRaises(patch.ErroPatch):
            patch.atualizar_manifest(man, 0x1, 0x2)

    def test_recusa_imagem_desconhecida(self):
        with self.assertRaises(patch.ErroPatch):
            patch.aplicar(b"\x7fELF" + b"\0" * 100)


@unittest.skipUnless(os.environ.get("GUARDIAO_FIRMWARE"), "defina GUARDIAO_FIRMWARE (pasta com boot/<versão>)")
class FirmwareReal(unittest.TestCase):
    def test_crcs_esperados(self):
        raiz = Path(os.environ["GUARDIAO_FIRMWARE"])
        achou = 0
        for vx in raiz.glob("boot/*/sysa12/vxworks"):
            img = vx.read_bytes()
            if crc_netvx(img) in ESPERADO:
                novo, rel = patch.aplicar(img)
                self.assertEqual(crc_netvx(novo), ESPERADO[crc_netvx(img)])
                self.assertEqual(len(rel), 4)
                achou += 1
        self.assertGreater(achou, 0)


if __name__ == "__main__":
    unittest.main()
