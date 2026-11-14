"""Testes do atualizador com um servidor de Releases falso (não precisa de root nem internet)."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guardiao import atualizacao as A  # noqa: E402


def montar_deb(pasta: Path, versao: str, pacote: str = "guardiao") -> Path:
    raiz = pasta / f"pkg-{pacote}-{versao}"
    (raiz / "DEBIAN").mkdir(parents=True)
    (raiz / "DEBIAN" / "control").write_text(
        f"Package: {pacote}\nVersion: {versao}\nArchitecture: all\n"
        "Maintainer: teste <t@t>\nDescription: teste\n")
    deb = pasta / f"guardiao_{versao}_all.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(raiz), str(deb)],
                   check=True, capture_output=True)
    return deb


class Servidor:
    def __init__(self, pasta: Path):
        pasta_str = str(pasta)

        class H(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=pasta_str, **k)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def fechar(self):
        self.httpd.shutdown()


@unittest.skipUnless(shutil.which("dpkg-deb"), "precisa do dpkg-deb")
class TestAtualizador(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.www = self.tmp / "www"
        self.www.mkdir()
        self.casa = self.tmp / "casa"
        self.casa.mkdir()
        self.env_antigo = dict(os.environ)
        os.environ["HOME"] = str(self.casa)
        os.environ.pop("SUDO_USER", None)
        os.environ.pop("PKEXEC_UID", None)
        os.environ.pop("GUARDIAO_SEM_ATUALIZACAO", None)
        self.srv = Servidor(self.www)
        os.environ["GUARDIAO_URL_ATUALIZACAO"] = self.srv.url + "/latest.json"

    def tearDown(self):
        self.srv.fechar()
        os.environ.clear()
        os.environ.update(self.env_antigo)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def publicar(self, versao, sha=None, com_digest=True, pacote="guardiao"):
        deb = montar_deb(self.www, versao, pacote)
        real = hashlib.sha256(deb.read_bytes()).hexdigest()
        asset = {"name": deb.name, "size": deb.stat().st_size,
                 "browser_download_url": f"{self.srv.url}/{deb.name}"}
        if com_digest:
            asset["digest"] = "sha256:" + (sha or real)
        rel = {"tag_name": f"v{versao}", "html_url": "https://github.com/aragonxpd154/guardiao/releases",
               "body": "## Novidades\n- espera o cabo\n- atualização automática", "assets": [asset]}
        (self.www / "latest.json").write_text(json.dumps(rel))
        return deb

    # -------------------------------------------------------------
    def test_versoes(self):
        self.assertTrue(A.mais_nova("2.10.0", "2.9.9"))
        self.assertTrue(A.mais_nova("v2.1.1", "2.1.0"))
        self.assertFalse(A.mais_nova("2.1.0", "2.1.0"))
        self.assertFalse(A.mais_nova("2.0.9", "2.1.0"))

    def test_encontra_e_baixa_versao_nova(self):
        self.publicar("99.0.0")
        nov = A.verificar(forcar=True)
        self.assertIsNotNone(nov)
        self.assertEqual(nov.versao, "99.0.0")
        self.assertIn("espera o cabo", nov.notas)
        deb = A.baixar(nov)
        self.assertTrue(deb.exists())

    def test_sha_errado_recusa(self):
        self.publicar("99.0.0", sha="0" * 64)
        nov = A.verificar(forcar=True)
        with self.assertRaises(A.ErroAtualizacao):
            A.baixar(nov)
        self.assertFalse((A.pasta_estado() / "guardiao_99.0.0_all.deb").exists())

    def test_sem_sha_recusa(self):
        self.publicar("99.0.0", com_digest=False)
        nov = A.verificar(forcar=True)
        with self.assertRaises(A.ErroAtualizacao):
            A.baixar(nov)

    def test_pacote_impostor_recusa(self):
        self.publicar("99.0.0", pacote="outra-coisa")
        nov = A.verificar(forcar=True)
        with self.assertRaises(A.ErroAtualizacao):
            A.baixar(nov)

    def test_mesma_versao_nao_oferece(self):
        self.publicar(A.__version__)
        self.assertIsNone(A.verificar(forcar=True))

    def test_uma_consulta_por_dia(self):
        self.publicar("99.0.0")
        self.assertIsNotNone(A.verificar(forcar=True))
        (self.www / "latest.json").unlink()        # GitHub "fora do ar"
        self.assertIsNotNone(A.verificar())          # usa o resultado guardado hoje

    def test_sem_internet_nao_quebra(self):
        os.environ["GUARDIAO_URL_ATUALIZACAO"] = "http://127.0.0.1:9/nada.json"
        self.assertIsNone(A.verificar(forcar=True, timeout=1))

    def test_adiar_e_ignorar(self):
        self.publicar("99.0.0")
        nov = A.verificar(forcar=True)
        self.assertTrue(A.deve_perguntar(nov))
        A.adiar(nov.versao)
        self.assertFalse(A.deve_perguntar(nov))
        A.ignorar(nov.versao)
        e = A.ler_estado()
        e["adiado_ate"] = 0
        A.salvar_estado(e)
        self.assertFalse(A.deve_perguntar(nov))

    def test_desligada(self):
        self.publicar("99.0.0")
        os.environ["GUARDIAO_SEM_ATUALIZACAO"] = "1"
        self.assertIsNone(A.verificar(forcar=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
