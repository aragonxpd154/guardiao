"""FTP com a SYS do NetVX.

A raiz do FTP da SYS é o próprio cartão, e o caminho "/d/..." também responde.
O servidor é o ftpd do VxWorks: não aceita SITE CHMOD nem MFMT (inofensivo) e
lista nomes curtos do FAT em maiúsculas (o FAT não diferencia maiúsculas).
"""

from __future__ import annotations

import ftplib
import io
import posixpath


class ErroFtp(Exception):
    pass


class SysFtp:
    BASES = ["/d", "", "/", "d"]

    def __init__(self, host: str, usuario: str = "admin", senha: str = "", porta: int = 21,
                 passivo: bool = True, timeout: float = 30):
        self.host, self.porta, self.usuario, self.senha = host, porta, usuario, senha
        self.passivo, self.timeout = passivo, timeout
        self.ftp: ftplib.FTP | None = None
        self.base: str | None = None

    # ---------------------------------------------------------------- conexão
    def __enter__(self):
        if self.ftp is None:
            self.abrir()
        return self

    def __exit__(self, *a):
        self.fechar()

    def abrir(self):
        f = ftplib.FTP(timeout=self.timeout, encoding="latin-1")
        try:
            f.connect(self.host, self.porta)
            f.login(self.usuario, self.senha)
            f.set_pasv(self.passivo)
            f.voidcmd("TYPE I")
        except (OSError, EOFError, ftplib.Error):
            f.close()
            raise
        self.ftp = f
        for b in self.BASES:
            try:
                f.cwd(self._p("etc", b))
                self.base = b
                break
            except ftplib.error_perm:
                continue
        if self.base is None:
            self.fechar()
            raise ErroFtp("não achei a pasta etc no FTP da SYS")
        f.cwd("/") if self.base.startswith("/") else None

    def fechar(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.ftp = None

    def _p(self, rel: str, base: str | None = None) -> str:
        b = self.base if base is None else base
        rel = rel.lstrip("/")
        if b in ("", None):
            return rel
        return b.rstrip("/") + "/" + rel

    # ---------------------------------------------------------------- leitura
    def baixar(self, rel: str) -> bytes:
        buf = io.BytesIO()
        self.ftp.retrbinary("RETR " + self._p(rel), buf.write)
        return buf.getvalue()

    def tentar_baixar(self, rel: str) -> bytes | None:
        try:
            return self.baixar(rel)
        except ftplib.error_perm:
            return None

    def listar(self, rel: str) -> list[str]:
        """Nomes dentro de rel (sem '.' e '..'). Lista vazia se não existir."""
        try:
            nomes = self.ftp.nlst(self._p(rel))
        except ftplib.error_perm:
            return []
        out = []
        for n in nomes:
            n = n.replace("\\", "/").rstrip("/").split("/")[-1]
            if n and n not in (".", ".."):
                out.append(n)
        return sorted(set(out))

    def existe_dir(self, rel: str) -> bool:
        atual = self.ftp.pwd()
        try:
            self.ftp.cwd(self._p(rel))
            return True
        except ftplib.error_perm:
            return False
        finally:
            try:
                self.ftp.cwd(atual)
            except ftplib.error_perm:
                pass

    def ler_texto(self, rel: str) -> str | None:
        d = self.tentar_baixar(rel)
        return None if d is None else d.decode("latin-1").strip()

    # ---------------------------------------------------------------- escrita
    def criar_dir(self, rel: str):
        try:
            self.ftp.mkd(self._p(rel))
        except ftplib.error_perm as e:
            if not self.existe_dir(rel):
                raise ErroFtp(f"não consegui criar {rel}: {e}") from e

    def enviar(self, rel: str, dados: bytes):
        self.ftp.storbinary("STOR " + self._p(rel), io.BytesIO(dados))

    def enviar_arvore(self, arquivos: dict[str, bytes], destino: str, progresso=None):
        """arquivos: {'sysa12/vxworks': b'...'} enviados para destino/<rel>."""
        self.criar_dir(destino)
        criadas = {destino}
        total = sum(len(v) for v in arquivos.values())
        feito = 0
        for rel in sorted(arquivos):
            d = destino
            for parte in posixpath.dirname(rel).split("/"):
                if parte:
                    d = d + "/" + parte
                    if d not in criadas:
                        self.criar_dir(d)
                        criadas.add(d)
            self.enviar(destino + "/" + rel, arquivos[rel])
            feito += len(arquivos[rel])
            if progresso:
                progresso(feito, total, rel)
