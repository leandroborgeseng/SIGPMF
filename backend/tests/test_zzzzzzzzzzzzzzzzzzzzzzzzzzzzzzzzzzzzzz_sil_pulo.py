"""SIL: Sequencia com 200 inesperado (registro problemático na origem) é pulada para a quarentena (TL 30/10).

Aceite do Tester: (1) limite de pulos seguidos (SIL_MAX_PULOS_SEGUIDOS, padrão 3) -> ErroSil; um lote bom zera;
(2) cada pulo grava Sequencia, status, tipo_json e NOMES das chaves na quarentena (sem valores) + Log WARN, e o
contador da tela bate; (3) o checkpoint passa da Sequencia pulada na mesma transação; só o 404 conclui; 5xx/timeout/401
não pulam. Nenhuma rede real (httpx.MockTransport, host .invalid), dados ZZTESTE."""
import json
import logging

import httpx
import pytest
from sqlalchemy import delete, select

from app.connectors.sil.cliente import ErroSil
from app.connectors.sil.conector import ConfigSil, SilConnector, max_pulos_seguidos
from app.core import recursos
from app.core.config import get_settings
from app.models.camadas import RegistroBronze
from app.models.controle import CheckpointCarga, Execucao, Log
from app.services import carga_completa as ccomp

SECRET = "zz-segredo-sintetico-pulo"
HOST = "sandbox.zzpulo.invalid"
TOKEN = "ZZTOKEN-PULO"
VALOR_PII = "ZZTESTE-PII-00"
RUIM = (200, {"erro": VALOR_PII, "cnpj": VALOR_PII})
FIM = (404, {"e": "zz"})


def _cfg():
    return ConfigSil(base_url=f"https://{HOST}/v1", client_id="zz-id", client_secret=SECRET, tentativas=2,
                     backoff_s=0, backoff_max_s=0, req_por_s=1000, timeout_s=1, max_lotes_por_execucao=5)


class Api:
    def __init__(self, respostas):
        self.respostas, self.sequencias = list(respostas), []

    def __call__(self, req):
        if req.url.path.endswith("/api/authenticate"):
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 600})
        self.sequencias.append(int(req.url.params["Sequencia"]))
        r = self.respostas.pop(0) if self.respostas else FIM
        if isinstance(r, Exception):
            raise r
        return httpx.Response(r[0], json=r[1])


def _con(api):
    return SilConnector(_cfg(), transporte=httpx.MockTransport(api), esperar=lambda s: None, amostra_limite=1000,
                        max_lotes=5)


def _lote(ccms):
    return (200, {"empresas": [{"empresa": {"Ccm": str(c), "Nome": f"EMPRESA ZZTESTE {c}"}} for c in ccms]})


def _ruim():
    return [RUIM, RUIM]  # a #64 repete a mesma Sequencia uma vez antes de desistir


@pytest.fixture()
def ambiente(db, monkeypatch):
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "1000")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.delenv("SIL_MAX_PULOS_SEGUIDOS", raising=False)
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    get_settings.cache_clear()
    db.execute(delete(RegistroBronze).where(RegistroBronze.sistema_origem == "SIL"))
    db.execute(delete(CheckpointCarga).where(CheckpointCarga.sistema == "SIL"))
    db.commit()
    yield monkeypatch
    get_settings.cache_clear()


def _rodar(db, api):
    ex = Execucao(tipo="carga_completa", sistema="SIL", status="em_andamento", usuario="admin@aion.local")
    db.add(ex)
    db.commit()
    ccomp.rodar(ex.id, "sil", None, leitor=_con(api), db=db)
    db.expire_all()
    return db.get(Execucao, ex.id)


def _quar(db):
    return db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SIL",
                                                   RegistroBronze.quarentena.is_not(None))
                      .order_by(RegistroBronze.id)).all()


def _boas(db):
    return db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SIL",
                                                   RegistroBronze.quarentena.is_(None))).all()


def _cp(db):
    return db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SIL"))


# ------------------------------------------------------------------ (1) limite de pulos seguidos
@pytest.mark.parametrize("valor,esperado,aviso", [
    (None, 3, False), ("", 3, False), ("5", 5, False), ("1", 1, False), ("100", 100, False), (" 4 ", 4, False),
    ("4.0", 4, False), ("0", 3, True), ("-2", 3, True), ("101", 3, True), ("nan", 3, True), ("NaN", 3, True),
    ("inf", 3, True), ("-inf", 3, True), ("2.5", 3, True), ("abc", 3, True), ("1e9", 3, True)])
def test_env_max_pulos_seguidos_validado(monkeypatch, caplog, valor, esperado, aviso):
    if valor is None:
        monkeypatch.delenv("SIL_MAX_PULOS_SEGUIDOS", raising=False)
    else:
        monkeypatch.setenv("SIL_MAX_PULOS_SEGUIDOS", valor)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"):
        assert max_pulos_seguidos() == esperado
    avisos = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    if aviso:
        assert len(avisos) == 1 and "SIL_MAX_PULOS_SEGUIDOS" in avisos[0] and "padrão 3" in avisos[0]
        assert valor.strip() not in avisos[0].replace("padrão 3", "").replace("1 a 100", "")
    else:
        assert avisos == []


def test_padrao_3_pula_duas_e_para_na_terceira(db, ambiente):
    """B3 (TL 30/10): padrão 3 = 2 pulos seguidos; a 3ª Sequencia ruim seguida para a carga."""
    api = Api([_lote([1]), *_ruim(), *_ruim(), *_ruim()])
    ex = _rodar(db, api)
    assert ex.status == "falha" and api.sequencias == [1, 2, 2, 3, 3, 4, 4]


def test_tres_ruins_seguidas_param_com_erro_sil_sem_pular(db, ambiente):
    api = Api(_ruim() * 3)
    ex = _rodar(db, api)
    assert ex.status == "falha", ex.mensagem
    assert api.sequencias == [1, 1, 2, 2, 3, 3]
    logs = [lg.mensagem for lg in db.scalars(select(Log).where(Log.execucao_id == ex.id))]
    assert any("classe=ErroSil" in m and "sequencia=3" in m for m in logs)
    # os pulos 1 e 2 eram do mesmo lote, que não foi confirmado: nada entra e o checkpoint não anda
    assert _quar(db) == [] and _boas(db) == []
    cp = _cp(db)
    assert cp is None or (not cp.concluida and cp.ultima_chave is None)


def test_ruim_boa_ruim_pula_duas_e_conclui_no_404(db, ambiente, caplog):
    api = Api([*_ruim(), _lote([2, 3]), *_ruim(), FIM])
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"):
        ex = _rodar(db, api)
    assert ex.status == "sucesso", ex.mensagem
    assert api.sequencias == [1, 1, 2, 4, 4, 5]
    q = _quar(db)
    assert [r.dados["Sequencia"] for r in q] == [1, 4]
    assert sorted(r.dados["Ccm"] for r in _boas(db)) == [2, 3]
    assert _cp(db).concluida is True
    for m in [r.getMessage() for r in caplog.records]:
        assert VALOR_PII not in m


def test_limite_customizado_e_lote_bom_zera_contagem(db, ambiente):
    ambiente.setenv("SIL_MAX_PULOS_SEGUIDOS", "2")
    api = Api([*_ruim(), _lote([2]), *_ruim(), _lote([4]), *_ruim(), *_ruim()])
    ex = _rodar(db, api)
    assert ex.status == "falha" and api.sequencias == [1, 1, 2, 3, 3, 4, 5, 5, 6, 6]


# ------------------------------------------------------------------ (2) quarentena sem valores + contador
def test_pulo_grava_quarentena_sem_valores_log_warn_e_contador(db, ambiente):
    api = Api([_lote([1]), *_ruim(), _lote([3]), FIM])
    ex = _rodar(db, api)
    assert ex.status == "sucesso", ex.mensagem
    q = _quar(db)
    assert len(q) == 1
    r = q[0]
    assert r.quarentena == "sil_resposta_inesperada" and r.id_origem == "sil_resposta_inesperada:2"
    assert r.dados == {"Sequencia": 2, "status": 200, "tipo_json": "dict", "chaves": ["cnpj", "erro"]}
    assert r.quarentena_campos == ["cnpj", "erro"] and r.execucao_id == ex.id
    assert VALOR_PII not in json.dumps(r.dados)
    warns = [lg for lg in db.scalars(select(Log).where(Log.execucao_id == ex.id)) if "pulada para a quarentena" in lg.mensagem]
    assert len(warns) == 1 and warns[0].nivel == "WARN" and "Sequencia=2" in warns[0].mensagem
    assert VALOR_PII not in warns[0].mensagem
    # contador da tela (card da carga completa e "Ver detalhes da quarentena") == linhas da quarentena
    assert ccomp.contagens_card(db, "SIL")["quarentena"] == len(q) == 1
    det = ccomp.quarentena(db, "sil")
    assert len(det) == 1 and det[0]["motivo"].startswith("Sequencia pulada") and det[0]["campos_nulos"] == ["cnpj", "erro"]
    assert VALOR_PII not in json.dumps(det, default=str)


def test_reexecutar_mesmo_pulo_nao_duplica_quarentena(db, ambiente):
    _rodar(db, Api([*_ruim(), FIM]))
    db.execute(delete(CheckpointCarga).where(CheckpointCarga.sistema == "SIL"))
    db.commit()
    _rodar(db, Api([*_ruim(), FIM]))
    assert len(_quar(db)) == 1


def test_mesma_sequencia_com_outro_formato_continua_uma_linha(db, ambiente):
    """M1: a identidade da quarentena é só a Sequencia; outro formato de resposta atualiza a mesma linha."""
    _rodar(db, Api([*_ruim(), FIM]))
    db.execute(delete(CheckpointCarga).where(CheckpointCarga.sistema == "SIL"))
    db.commit()
    outro = (200, {"mensagem": VALOR_PII, "codigo": 7, "x": 1})
    ex2 = _rodar(db, Api([outro, outro, FIM]))
    assert ex2.status == "sucesso", ex2.mensagem
    q = _quar(db)
    assert len(q) == 1 and q[0].id_origem == "sil_resposta_inesperada:1"
    assert q[0].dados["chaves"] == ["codigo", "mensagem", "x"] and q[0].execucao_id == ex2.id
    assert ccomp.contagens_card(db, "SIL")["quarentena"] == 1 and len(ccomp.quarentena(db, "sil")) == 1


def test_mensagem_final_separa_pulos_de_chave_nula(db, ambiente):
    """Info TL 30/10: a mensagem final conta as Sequencias puladas separado da quarentena por chave nula."""
    ex = _rodar(db, Api([*_ruim(), _lote([2]), *_ruim(), FIM]))
    assert ex.status == "sucesso", ex.mensagem
    assert "2 Sequencia(s) pulada(s) em quarentena (resposta inesperada da API SIL)" in ex.mensagem
    assert "(chave nula)" not in ex.mensagem
    # com uma linha de chave nula também: as duas contagens aparecem, cada uma com o seu texto
    db.add(RegistroBronze(sistema_origem="SIL", tabela="Empresas", id_origem="chave_nula:zzteste:1",
                          dados={"Ccm": None}, hash="zzteste-nula", execucao_id=ex.id, quarentena="chave_nula",
                          quarentena_campos=["Ccm"]))
    db.execute(delete(CheckpointCarga).where(CheckpointCarga.sistema == "SIL"))
    db.commit()
    ex2 = _rodar(db, Api([*_ruim(), _lote([2]), *_ruim(), FIM]))
    assert "1 linha(s) em quarentena (chave nula)" in ex2.mensagem
    assert "2 Sequencia(s) pulada(s) em quarentena (resposta inesperada da API SIL)" in ex2.mensagem


# ------------------------------------------------------------------ (3) checkpoint, conclusão e retomada
def test_retoma_depois_do_pulo_a_partir_da_sequencia_seguinte(db, ambiente):
    ambiente.setenv("CARGA_COMPLETA_LOTE", "3")
    get_settings.cache_clear()
    # 1ª execução: [1,2] + pulo 3 fecham a página (3 linhas) -> commit; a Sequencia 4 dá 503 e a carga falha
    api1 = Api([_lote([1, 2]), *_ruim(), (503, {}), (503, {}), (503, {})])
    ex1 = _rodar(db, api1)
    assert ex1.status == "falha"
    cp = _cp(db)
    assert cp.ultima_chave == [3] and cp.concluida is False  # passou da Sequencia pulada, sem "concluir"
    assert [r.dados["Sequencia"] for r in _quar(db)] == [3]
    # 2ª execução: retoma em 4 (não repete a 3)
    api2 = Api([_lote([4]), FIM])
    ex2 = _rodar(db, api2)
    assert ex2.status == "sucesso", ex2.mensagem
    assert api2.sequencias[0] == 4 and 3 not in api2.sequencias
    assert sorted(r.dados["Ccm"] for r in _boas(db)) == [1, 2, 4]
    assert len(_quar(db)) == 1 and _cp(db).concluida is True


def test_pulo_nunca_conclui_so_o_404(db, ambiente):
    ambiente.setenv("CARGA_COMPLETA_LOTE", "1")
    get_settings.cache_clear()
    api = Api([*_ruim(), _lote([2]), FIM])
    ex = _rodar(db, api)
    assert ex.status == "sucesso" and api.sequencias == [1, 1, 2, 3]
    assert _cp(db).concluida is True and [r.dados["Ccm"] for r in _boas(db)] == [2]


@pytest.mark.parametrize("ruim", [(503, {}), httpx.ReadTimeout("zz")])
def test_5xx_e_timeout_nao_pulam(db, ambiente, ruim):
    api = Api([_lote([1]), ruim, ruim, ruim])
    ex = _rodar(db, api)
    assert ex.status == "falha" and _quar(db) == []
    assert api.sequencias == [1, 2, 2, 2]  # retry de sempre (1 + 2 tentativas), sem avançar


def test_401_nao_pula(db, ambiente):
    api = Api([(401, {}), (401, {})])
    ex = _rodar(db, api)
    assert ex.status == "falha" and _quar(db) == [] and api.sequencias == [1, 1]


def test_amostra_nao_pula():
    api = Api(_ruim())
    with pytest.raises(ErroSil):
        _con(api).ler_amostra("Empresas")
    assert api.sequencias == [1, 1]
