"""SEI simulado (httpx.MockTransport) para os testes do conector SEI. Hosts .invalid, dados ZZTESTE sintéticos."""
import json

import httpx

from app.identity.cpf import digitos_verificadores

PROC = "https://sei-processos.zzteste.invalid"
DOCS = "https://sei-documentos.zzteste.invalid"
PARAM = "https://sei-parametros.zzteste.invalid"
IDP = "https://idp.zzteste.invalid"
HML = {"PROCESSOS": "https://sei-processos.api-hml.zzteste.invalid",
       "DOCUMENTOS": "https://sei-documentos.api-hml.zzteste.invalid",
       "PARAMETROS": "https://sei-parametros.api-hml.zzteste.invalid", "IDP": "https://idp-hml.zzteste.invalid"}
SEGREDO = "ZZTESTE-segredo-nao-logar"
SERVICO = "ZZTESTE-identificacao-servico"
CPF_OK = "123456789" + digitos_verificadores("123456789")


def env_oficial(monkeypatch, **extra):
    for k, v in {"BASE_PROCESSOS": PROC, "BASE_DOCUMENTOS": DOCS, "BASE_PARAMETROS": PARAM, "IDP_URL": IDP, "IDP_TOKEN_PATH": "/token",
                 "IDP_CLIENT_ID": "zzteste-client", "IDP_CLIENT_SECRET": SEGREDO, "SIGLA_SISTEMA": "ZZTESTE",
                 "IDENTIFICACAO_SERVICO": SERVICO, "ID_UNIDADE": "110000001", "REGIONAL": "ZZ", **extra}.items():
        if v is None:
            monkeypatch.delenv(f"SIGPMF_SRC_SEI_{k}", raising=False)
        else:
            monkeypatch.setenv(f"SIGPMF_SRC_SEI_{k}", v)


def env_hml(monkeypatch):
    env_oficial(monkeypatch)
    for k, v in HML.items():
        nome = "IDP_URL" if k == "IDP" else f"BASE_{k}"
        monkeypatch.setenv(f"SIGPMF_SRC_SEI_HML_{nome}", v)
    monkeypatch.setenv("SIGPMF_SRC_SEI_HML_IDP_TOKEN_PATH", "/token")
    monkeypatch.setenv("SIGPMF_SRC_SEI_USAR_HOMOLOGACAO", "true")


def processo(i: int, nivel: str = "0", interessados=None) -> dict:
    return {"idProcedimento": f"9{i:06d}", "procedimentoFormatado": f"99990.{i:06d}/2026-01", "dataAutuacao":
            "01/10/2026", "nivelAcessoLocal": nivel, "nivelAcessoGlobal": nivel, "linkAcesso": "https://x.invalid/l",
            "observacoes": [{"descricao": "ZZTESTE observacao livre"}],
            "TipoProcedimento": [{"idTipoProcedimento": "1", "nome": "ZZTESTE tipo"}],
            "assuntos": [{"codigoEstruturado": "00.01", "descricao": "ZZTESTE"}],
            "interessados": interessados if interessados is not None else [
                {"sigla": CPF_OK, "nome": "ZZTESTE FULANO DA SILVA"}, {"sigla": "zzorg", "nome": "ZZTESTE SEM CPF"}]}


class Sei:
    """Responde as rotas da allowlist; registra todas as chamadas."""

    def __init__(self, n_processos=3, niveis=None, andamentos=0, repetir_andamentos=False, token_401=0,
                 total_docs=2):
        self.n, self.niveis = n_processos, niveis or {}
        self.n_and, self.repetir = andamentos, repetir_andamentos
        self.token_401, self.total_docs = token_401, total_docs
        self.chamadas: list[httpx.Request] = []
        self.tokens = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.chamadas.append(req)
        if req.method == "POST" and req.url.host.startswith("idp"):
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.tokens}", "expires_in": 300})
        if req.headers.get("Authorization") == "Bearer tok-1" and self.token_401:
            self.token_401 -= 1
            return httpx.Response(401)
        p = req.url.path
        q = req.url.params
        if p == "/processos":
            st, lim = int(q.get("start", 0)), int(q.get("limit", 50))
            ids = list(range(1, self.n + 1))[st:st + lim]
            return httpx.Response(200, json={"total": str(self.n), "processos": [
                {"idProcedimento": f"9{i:06d}", "protocoloProcedimento": f"99990.{i:06d}/2026-01"} for i in ids]})
        if p.startswith("/processos/") and p.endswith("/documentos"):
            return httpx.Response(200, json={"total": "3", "documentos": [
                {"idDocumento": f"D{k}", "tipo": "G", "idSerie": "7", "procedimentoFormatado": "x",
                 "nome": "ZZTESTE arquivo.pdf", "titulo": "ZZTESTE titulo", "informacao": "ZZTESTE info",
                 "Unidade": {"idUnidade": "1", "sigla": "ZZ"}, "nivelAcesso": "0"} for k in range(self.total_docs)]
                + [{"idDocumento": "DSIG", "tipo": "G", "nivelAcesso": "2"}]})
        if p.startswith("/processos/"):
            i = int(p.split(".")[-1].split("%2F")[0].split("/")[0])
            return httpx.Response(200, json=processo(i, self.niveis.get(i, "0")))
        if p == "/andamentos/completo":
            st, lim = int(q.get("start", 0)), int(q.get("limit", 50))
            if self.repetir:
                st = 0
            ids = list(range(1, self.n_and + 1))[st:st + lim]
            return httpx.Response(200, json={"Andamentos": [
                {"idAndamento": f"A{i}", "idProcesso": "1", "protocoloProcedimento": "99990.000001/2026-01",
                 "idTarefa": "1", "descricao": "ZZTESTE texto livre", "dataHora": "01/10/2026 10:00",
                 "usuarios": {"nome": "ZZTESTE USUARIO"}, "nivelAcesso": "0", "unidade": {"idUnidade": "1", "sigla": "ZZ"}}
                for i in ids]})
        return httpx.Response(404, json={})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def gets(self) -> list[httpx.Request]:
        return [c for c in self.chamadas if c.method == "GET"]


def corpo(r: httpx.Request) -> str:
    return json.dumps(dict(r.headers)) + str(r.url) + (r.content or b"").decode(errors="ignore")
