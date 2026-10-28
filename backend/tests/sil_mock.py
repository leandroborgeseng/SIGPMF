"""Servidor HTTP SINTÉTICO que imita a API da SIL (SIL7): só dados inventados (ZZTESTE), montados a partir dos nomes
de rota e de campo da doc. Nunca aponta para a SIL real."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "token-sintetico-zzteste"
ID_SINTETICO, SEGREDO_SINTETICO = "zzteste-client-id", "zzteste-client-secret-nao-real"


def _cpf(base9: int) -> str:
    from app.identity.cpf import digitos_verificadores
    b = f"{base9:09d}"
    return b + digitos_verificadores(b)


class EstadoMock:
    def __init__(self):
        self.req: list[tuple[str, str, dict]] = []
        self.auth_ok = True
        self.expires_in = 600
        self.respostas_forcadas: list[tuple[int, dict | None, dict]] = []  # (status, corpo, headers) consumidos em ordem
        self.atraso_s = 0.0
        self.tokens_validos = {TOKEN}
        self.empresas = {i: {"Ccm": str(i), "CNPJCPF": _cpf(900000000 + i), "PossuiDebito": "S" if i % 2 else "N",
                             "Nome": f"ZZTESTE EMPRESA {i}", "Email": f"zz{i}@zzteste.invalid", "Telefone": "0000-0000"}
                         for i in range(1, 2600)}
        self.tamanho_lote = 300
        self.ids_repetidos = False

    def conta(self, metodo, caminho):
        return sum(1 for m, c, _ in self.req if m == metodo and c == caminho)


def servidor(estado: EstadoMock):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencioso
            pass

        def _resp(self, status, corpo=None, headers=None):
            b = json.dumps(corpo if corpo is not None else {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _registra(self, metodo):
            u = urlparse(self.path)
            corpo = {}
            if metodo == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                corpo = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
            estado.req.append((metodo, u.path, {**{k: v[0] for k, v in parse_qs(u.query).items()}, **corpo,
                                                "_auth": self.headers.get("Authorization")}))
            return u.path, estado.req[-1][2]

        def do_POST(self):  # noqa: N802
            caminho, p = self._registra("POST")
            if caminho != "/api/authenticate":
                return self._resp(500, {"status": "Falha", "message": "mock: POST inesperado"})
            ok = (estado.auth_ok and p.get("grant_type") == "client_credentials" and p.get("client_id") == ID_SINTETICO
                  and p.get("client_secret") == SEGREDO_SINTETICO)
            if not ok:
                return self._resp(401, {"status": "Falha", "message": "credencial inválida"})
            self._resp(200, {"access_token": TOKEN, "token_type": "Bearer", "expires_in": estado.expires_in})

        def do_GET(self):  # noqa: N802
            import time
            caminho, p = self._registra("GET")
            if estado.atraso_s:
                time.sleep(estado.atraso_s)
            if estado.respostas_forcadas:
                st, corpo, h = estado.respostas_forcadas.pop(0)
                return self._resp(st, corpo, h)
            if (p.get("_auth") or "") != f"Bearer {TOKEN}" or TOKEN not in estado.tokens_validos:
                return self._resp(401, {"message": "Token inválido ou expirado.", "status": "Falha"})
            if caminho == "/franca/empresas":
                e = next((x for x in estado.empresas.values() if p.get("Documento") in (x["Ccm"], x["CNPJCPF"])), None)
                return self._resp(200, {"data": e, "status": "Sucesso"}) if e else self._resp(404, {"status": "Falha"})
            if caminho == "/franca/imoveis":
                if p.get("InscrFisico", "").startswith("ZZ404"):
                    return self._resp(404, {"status": "Falha", "message": "não localizado"})
                return self._resp(200, {"data": {"CNPJCPFResponsavel": _cpf(123456789), "PossuiDebito": "N",
                                                 "NomeResponsavel": "ZZTESTE RESP"}, "status": "Sucesso"})
            if caminho == "/franca/atividade":
                return self._resp(200, {"data": [], "status": "Sucesso"})
            if caminho == "/franca/empresa/consultarlote":
                if p.get("Status") != "Ativo":
                    return self._resp(400, {"status": "Falha"})
                seq = int(p.get("Sequencia", "0"))
                ids = sorted(i for i in estado.empresas if i > seq)[: estado.tamanho_lote]
                if estado.ids_repetidos and ids:
                    ids = [ids[0]] + ids  # um ID repetido dentro do lote
                if not ids:
                    return self._resp(404, {"status": "Falha", "message": "Nenhuma empresa"})
                return self._resp(200, {"data": {"empresas": [{"empresa": estado.empresas[i]} for i in ids]},
                                        "message": "Empresas localizadas com sucesso", "status": "Sucesso"})
            self._resp(500, {"status": "Falha", "message": "mock: rota inesperada"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
