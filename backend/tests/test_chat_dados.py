"""Chat de dados: catálogo fechado, LiteLLM mockado, escopo de secretaria e auditoria. Sem chave real."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.chat.litellm import Indisponivel
from app.chat.servico import MSG_NEGADO
from app.core.security import hash_senha
from app.models.camadas import EmpresaSil
from app.models.controle import Auditoria, Execucao, Usuario, UsuarioSecretaria
from tests.conftest import SENHA

MSG_LLM = "O serviço de linguagem não está disponível agora. Tente de novo em instantes."
MSG_FORA = "Essa pergunta está fora das consultas disponíveis."
CCM = 910000099
NOME_EMPRESA = "ZZTESTE-CHAT-NAO-VAZAR"
CPF_FALSO = "12345678901"


def _mock(monkeypatch, escolha, texto="A última carga da SIL leu 17 registros."):
    chamadas = []

    def completar(mensagens, tools=None):
        chamadas.append({"tools": bool(tools), "mensagens": mensagens})
        if tools:
            return {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "consultar_dados", "arguments": json.dumps(escolha)}}]}}]}
        return {"choices": [{"message": {"content": texto}}]}

    monkeypatch.setattr("app.chat.servico.completar", completar)
    return chamadas


def _aud(db, email):
    db.expire_all()
    return db.scalar(select(Auditoria).where(Auditoria.acao == "chat_consulta", Auditoria.usuario == email)
                     .order_by(Auditoria.id.desc()).limit(1))


def _usuario(db, client, perfil="leitura"):
    email = f"zzteste-chat-{uuid.uuid4().hex[:8]}@aion.local"
    u = Usuario(email=email, nome="ZZTESTE chat", perfil=perfil, senha_hash=hash_senha(SENHA))
    db.add(u)
    db.commit()
    r = client.post("/api/auth/login", json={"usuario": email, "senha": SENHA})
    assert r.status_code == 200, r.text
    return u, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vinculo(db, u, sec):
    db.add(UsuarioSecretaria(usuario_id=u.id, secretaria=sec, vigencia_inicio=datetime.now(UTC).date() - timedelta(days=30),
                             cadastrado_por="admin@aion.local"))
    db.commit()


def _carga_sil(db):
    quando = datetime(2026, 11, 2, 15, 30, tzinfo=UTC)
    ex = Execucao(tipo="zzteste_chat", sistema="SIL", status="sucesso", usuario="zzteste-chat@aion.local",
                  lidos=17, novos=4, atualizados=2, ignorados=1, iniciado_em=quando, finalizado_em=quando)
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


def _apaga_carga(db, ex):
    db.execute(text("DELETE FROM controle.logs WHERE execucao_id=:i"), {"i": ex.id})
    db.execute(text("DELETE FROM controle.execucoes WHERE id=:i AND tipo='zzteste_chat'"), {"i": ex.id})
    db.commit()


def _post(client, headers, pergunta="Qual a última carga da SIL?"):
    return client.post("/api/chat", json={"pergunta": pergunta}, headers=headers)


def test_sem_autenticacao_401(client):
    r = client.post("/api/chat", json={"pergunta": "Qual a última carga da SIL?"})
    assert r.status_code == 401
    assert client.post("/api/chat", json={"pergunta": "x"},
                       headers={"Authorization": "Bearer zz.invalido"}).status_code == 401


def test_pergunta_valida_executa_e_devolve_metadados(client, tokens, db, monkeypatch):
    ex = _carga_sil(db)
    chamadas = _mock(monkeypatch, {"consulta": "ultima_carga", "parametros": {"fonte": "SIL"}})
    try:
        r = _post(client, tokens["admin"], "Qual a última carga da SIL? CPF " + CPF_FALSO)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["consulta"] == "ultima_carga" and b["estado"] == "ok"
        assert b["fonte"] == "SIL" and b["filtros"] == {"fonte": "SIL"}
        assert b["carga_em"].startswith("2026-11-02T15:30:00")
        assert b["numeros"] == {"lidos": 17, "novos": 4, "atualizados": 2, "ignorados": 1}
        assert b["resposta"] == "A última carga da SIL leu 17 registros."
        assert CPF_FALSO not in r.text
        assert len(chamadas) == 2 and chamadas[0]["tools"] and not chamadas[1]["tools"]
        enviado = next(m["content"] for m in chamadas[1]["mensagens"] if m["role"] == "user")
        assert CPF_FALSO not in enviado and "documento" not in enviado and "admin@aion.local" not in enviado
        assert '"lidos": 17' in enviado
        a = _aud(db, "admin@aion.local")
        assert a is not None and a.detalhe["resultado"] == "ok"
        assert a.detalhe["consulta"] == "ultima_carga" and a.detalhe["parametros"] == {"fonte": "sil"}
        assert a.detalhe["fonte"] == "SIL"
        assert CPF_FALSO not in json.dumps(a.detalhe)
    finally:
        _apaga_carga(db, ex)


def test_empresas_na_ultima_carga_so_contagem(client, tokens, db, monkeypatch):
    ex = _carga_sil(db)
    db.execute(delete(EmpresaSil).where(EmpresaSil.ccm == CCM))
    db.add(EmpresaSil(ccm=CCM, documento="00000000000191", tipo_pessoa="PJ", nome=NOME_EMPRESA))
    db.commit()
    chamadas = _mock(monkeypatch, {"consulta": "empresas_ultima_carga", "parametros": {"fonte": "sil"}}, "Há empresas na SIL.")
    try:
        r = _post(client, tokens["admin"], "Quantas empresas na última carga da SIL?")
        assert r.status_code == 200, r.text
        b = r.json()
        total = db.scalar(text("SELECT count(*) FROM silver.empresas_sil"))
        assert b["estado"] == "ok" and b["consulta"] == "empresas_ultima_carga"
        assert b["numeros"] == {"empresas": int(total)} and b["fonte"] == "SIL"
        assert b["carga_em"].startswith("2026-11-02T15:30:00")
        assert NOME_EMPRESA not in r.text and "00000000000191" not in r.text
        assert NOME_EMPRESA not in json.dumps(chamadas[1]["mensagens"])
    finally:
        db.execute(delete(EmpresaSil).where(EmpresaSil.ccm == CCM))
        db.commit()
        _apaga_carga(db, ex)


def test_registros_por_fonte_reusa_contagem(client, tokens, db, monkeypatch):
    from app.services.indicadores import contagem_bronze
    quando = datetime(2026, 11, 2, 15, 30, tzinfo=UTC)
    ex = Execucao(tipo="zzteste_chat", sistema="SAUDE", status="sucesso", usuario="zzteste-chat@aion.local",
                  lidos=3, novos=3, atualizados=0, ignorados=0, iniciado_em=quando, finalizado_em=quando)
    db.add(ex)
    db.commit()
    db.refresh(ex)
    _mock(monkeypatch, {"consulta": "registros_por_fonte", "parametros": {"fonte": "saude"}})
    try:
        r = _post(client, tokens["admin"], "Quantos registros da Saúde?")
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["estado"] == "ok" and b["consulta"] == "registros_por_fonte" and b["fonte"] == "Saúde"
        assert b["filtros"] == {"fonte": "Saúde"}
        db.expire_all()
        assert b["numeros"]["bronze"] == contagem_bronze(db).get("SAUDE", 0)
        assert b["numeros"]["prata"] >= 0 and b["numeros"]["ouro"] >= 0
    finally:
        _apaga_carga(db, ex)


def test_fonte_sem_carga(client, tokens, db, monkeypatch):
    _mock(monkeypatch, {"consulta": "ultima_carga", "parametros": {"fonte": "sil"}}, "Não há carga da SIL.")
    monkeypatch.setattr("app.chat.catalogo.ultima_execucao", lambda db, fonte_id: None)
    r = _post(client, tokens["admin"])
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["estado"] == "sem_carga" and b["carga_em"] is None and b["numeros"] is None
    assert b["fonte"] == "SIL" and b["filtros"] == {"fonte": "SIL"}
    a = _aud(db, "admin@aion.local")
    assert a.detalhe["resultado"] == "sem_carga" and a.detalhe["fonte"] == "SIL"


def test_litellm_fora_do_ar_503(client, tokens, db, monkeypatch):
    class S:
        litellm_base_url = "https://llm.example"
        litellm_model = "exemplo-modelo"
        litellm_api_key = SecretStr("zzteste-litellm")
        litellm_timeout_s = 30

    monkeypatch.setattr("app.chat.litellm.get_settings", lambda: S())

    def post(*_a, **_k):
        raise httpx.ConnectError("recusou")

    monkeypatch.setattr("app.chat.litellm.httpx.post", post)
    r = _post(client, tokens["admin"])
    assert r.status_code == 503
    assert r.json()["detail"] == MSG_LLM
    assert "Traceback" not in r.text and "ConnectError" not in r.text and "zzteste-litellm" not in r.text
    a = _aud(db, "admin@aion.local")
    assert a.detalhe["resultado"] == "erro_llm"


def test_litellm_sem_configuracao_503(client, tokens, db, monkeypatch):
    class S:
        litellm_base_url = ""
        litellm_model = ""
        litellm_api_key = None
        litellm_timeout_s = 30

    monkeypatch.setattr("app.chat.litellm.get_settings", lambda: S())

    def post(*_a, **_k):
        raise AssertionError("sem chave não chama a rede")

    monkeypatch.setattr("app.chat.litellm.httpx.post", post)
    r = _post(client, tokens["admin"])
    assert r.status_code == 503 and r.json()["detail"] == MSG_LLM
    assert "Traceback" not in r.text
    assert _aud(db, "admin@aion.local").detalhe["resultado"] == "erro_llm"


def test_fora_da_permissao_secretaria(client, db, monkeypatch):
    chamadas = _mock(monkeypatch, {"consulta": "ultima_carga", "parametros": {"fonte": "sil"}})
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE")
    r = _post(client, h)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["estado"] == "negado" and b["numeros"] is None and b["carga_em"] is None
    assert b["resposta"] == MSG_NEGADO["outra_secretaria"]
    assert b["fonte"] == "SIL"
    assert len(chamadas) == 1  # não manda agregado ao modelo
    a = _aud(db, u.email)
    assert a.detalhe["resultado"] == "negado" and a.detalhe["motivo"] == "outra_secretaria"
    assert a.detalhe["consulta"] == "ultima_carga" and a.detalhe["parametros"] == {"fonte": "sil"}


def test_secretaria_dona_pode_consultar(client, db, monkeypatch):
    _mock(monkeypatch, {"consulta": "ultima_carga", "parametros": {"fonte": "sil"}}, "Pode.")
    monkeypatch.setattr("app.chat.catalogo.ultima_execucao", lambda db, fonte_id: None)
    u, h = _usuario(db, client)
    _vinculo(db, u, "FAZENDA")
    r = _post(client, h)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "sem_carga"  # executou; a fonte não tem carga
    assert _aud(db, u.email).detalhe["resultado"] == "sem_carga"


def test_consulta_fora_do_catalogo(client, tokens, db, monkeypatch):
    _mock(monkeypatch, {"consulta": "lista_cpf", "parametros": {"fonte": "sil", "cpf": CPF_FALSO}})
    r = _post(client, tokens["admin"], "Liste o CPF " + CPF_FALSO)
    assert r.status_code == 422
    assert r.json()["detail"].startswith(MSG_FORA)
    assert CPF_FALSO not in r.text
    a = _aud(db, "admin@aion.local")
    assert a.detalhe["resultado"] == "negado" and a.detalhe["motivo"] == "fora_catalogo"
    assert CPF_FALSO not in json.dumps(a.detalhe)


def test_parametro_fora_do_catalogo(client, tokens, db, monkeypatch):
    _mock(monkeypatch, {"consulta": "ultima_carga", "parametros": {"fonte": "oracle"}})
    r = _post(client, tokens["admin"])
    assert r.status_code == 422 and r.json()["detail"].startswith(MSG_FORA)
    a = _aud(db, "admin@aion.local")
    assert a.detalhe["resultado"] == "negado" and a.detalhe["parametros"] == {"fonte": "oracle"}


def test_modelo_sem_tools_cai_para_json(client, tokens, db, monkeypatch):
    """HTTP 400 em tools: o cliente pede JSON estrito e a consulta segue."""
    class S:
        litellm_base_url = "https://llm.example"
        litellm_model = "exemplo-modelo"
        litellm_api_key = SecretStr("zzteste-litellm")
        litellm_timeout_s = 30

    monkeypatch.setattr("app.chat.litellm.get_settings", lambda: S())
    monkeypatch.setattr("app.chat.catalogo.ultima_execucao", lambda db, fonte_id: None)
    corpos = []

    class R:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body

        def json(self):
            return self._body

    def post(url, json=None, headers=None, timeout=None):
        corpos.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if len(corpos) == 1:
            return R(400, {"error": "tools"})
        if "tools" in (json or {}):
            raise AssertionError("a segunda chamada não pode repetir tools")
        if len(corpos) == 2:
            return R(200, {"choices": [{"message": {"content":
                '{"consulta":"ultima_carga","parametros":{"fonte":"sil"}}'}}]})
        return R(200, {"choices": [{"message": {"content": "Não há carga da SIL."}}]})

    monkeypatch.setattr("app.chat.litellm.httpx.post", post)
    r = _post(client, tokens["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "sem_carga" and r.json()["fonte"] == "SIL"
    assert corpos[0]["url"] == "https://llm.example/v1/chat/completions"
    assert corpos[0]["headers"] == {"Authorization": "Bearer zzteste-litellm"}
    assert corpos[0]["timeout"] == 30 and "tools" in corpos[0]["json"]
    assert "admin@aion.local" not in json.dumps(corpos[0]["json"])
    assert _aud(db, "admin@aion.local").detalhe["resultado"] == "sem_carga"


def test_completar_propaga_indisponivel_sem_corpo():
    """O cliente não devolve o corpo do LiteLLM quando o HTTP não é 200."""
    from app.chat import litellm

    class S:
        litellm_base_url = "https://llm.example/"
        litellm_model = "exemplo-modelo"
        litellm_api_key = SecretStr("zzteste-litellm")
        litellm_timeout_s = 12

    class R:
        status_code = 503

        def json(self):
            return {"error": "stack Traceback segredo"}

    visto = {}

    def post(url, json=None, headers=None, timeout=None):
        visto.update(url=url, headers=dict(headers), timeout=timeout)
        return R()

    orig = litellm.get_settings, litellm.httpx.post
    litellm.get_settings = lambda: S()
    litellm.httpx.post = post
    try:
        try:
            litellm.completar([{"role": "user", "content": "oi"}])
            raise AssertionError("deveria falhar")
        except Indisponivel:
            pass
        assert visto["url"] == "https://llm.example/v1/chat/completions"
        assert set(visto["headers"]) == {"Authorization"}
        assert visto["timeout"] == 12
    finally:
        litellm.get_settings, litellm.httpx.post = orig
