"""Carga do Munícipe (ISS.Municipes) para a silver (aceite M1-M6).

Leitura: só pelo conector com a política ISS (lista explícita de colunas, guard SELECT-only, ROLLBACK, timeout,
TOP AMOSTRA_LIMITE). Credenciais só pelo ambiente. Os testes usam linhas sintéticas (`linhas=`).
"""
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.sqlserver.politicas import ISS, filtra_colunas
from app.core.audit import audita
from app.ingestion import municipe as mun
from app.models.camadas import Municipe, MunicipePendencia, MunicipeVersao
from app.models.controle import Execucao, Log
from app.services.diagnostico import MSG_ORIGEM_VAZIA, ORIGEM_VAZIA, FalhaLeitura, linha_json

TABELA = "Municipes"
# CC9: chave da linha na origem = documento só dígitos mantido pelo ISS. NULL/vazio -> quarentena na bronze.
CHAVE_CC9 = ("Documento_SoNumeros",)
MOTIVO_CHAVE_NULA = "chave_nula"  # mesmo valor do CC9 genérico (factory/sigpmf-etapas-prata)


def limite_nulos_pct() -> float:
    """SIGPMF_CARGA_NULOS_LIMITE_PCT (mesmo nome do CC9 genérico); padrão 1%."""
    import os
    try:
        return min(max(float(os.environ.get("SIGPMF_CARGA_NULOS_LIMITE_PCT", "1")), 0.0), 100.0)
    except ValueError:
        return 1.0


class ChaveNulaAcimaDoLimite(RuntimeError):
    """CC9.3: nulos acima do limite % e do mínimo. A mensagem só tem contagens (sem dado pessoal) e vai para a execução."""


def limite_nulos_min() -> int:
    """SIGPMF_CARGA_NULOS_LIMITE_MIN (mesmo nome do CC9 genérico); padrão 10 linhas."""
    import os
    try:
        return max(int(os.environ.get("SIGPMF_CARGA_NULOS_LIMITE_MIN", "10")), 0)
    except ValueError:
        return 10


def bloqueia_nulos(nulos: int, total: int) -> bool:
    """CC9: só bloqueia se os nulos passarem do limite % E do mínimo absoluto (tabela pequena não para)."""
    return bool(nulos) and 100.0 * nulos / max(total, 1) > limite_nulos_pct() and nulos > limite_nulos_min()


def chave_nula(linha: dict) -> list[str]:
    """Campos da chave CC9 nulos/vazios. Linha sem a coluna (fixtures antigas) não é avaliada."""
    from app.identity import documento as docmod
    return [c for c in CHAVE_CC9 if c in linha and not docmod.normaliza(mun._txt(linha.get(c)))]


def _sql_nulos(limite: int) -> tuple[str, str]:
    """SELECTs separados, limitados e pela guarda: contagem (total, nulos) e as linhas de chave nula."""
    from app.connectors.sqlserver.connector import _ident
    from app.core.sql_guard import garante_somente_leitura
    cols = ISS.colunas_select(TABELA)
    t = _ident("dbo." + TABELA)
    nulos = " OR ".join(f"{_ident(c)} IS NULL OR LTRIM(RTRIM({_ident(c)})) = ''" for c in CHAVE_CC9)
    q1 = f"SELECT COUNT(*), SUM(CASE WHEN {nulos} THEN 1 ELSE 0 END) FROM {t}"
    q2 = (f"SELECT TOP ({int(limite)}) {', '.join(_ident(c) for c in cols)} FROM {t} WHERE ({nulos}) "
          f"ORDER BY {', '.join(_ident(c) for c in cols)}")
    return garante_somente_leitura(q1), garante_somente_leitura(q2)


def ler_nulos(connector) -> tuple[int | None, int, list[dict]]:
    """CC9: (COUNT(*) da origem, linhas de chave nula, linhas de chave nula lidas com TOP limitado)."""
    from app.core.sql_guard import executa_somente_leitura
    conn = connector._conectar()
    try:
        q1, _ = _sql_nulos(1)
        res = executa_somente_leitura(conn, q1, limite=1, como_dict=False)
        try:
            total, nulos = (list(res[0].values()) if isinstance(res[0], dict) else list(res[0]))[:2]
            total, nulos = int(total or 0), int(nulos or 0)
        except (IndexError, TypeError, ValueError):
            return None, 0, []  # contagem indisponível: segue sem reconciliação (nunca inventa números)
        if not nulos or bloqueia_nulos(nulos, total):
            return total, nulos, []
        _, q2 = _sql_nulos(nulos)
        return total, nulos, executa_somente_leitura(conn, q2, limite=nulos)
    finally:
        conn.close()


def _chaves_bronze(linhas: list[dict]) -> list[str]:
    """Id estável na bronze; linhas idênticas recebem sufixo de ocorrência (nenhuma colapsa)."""
    import hashlib
    vistos: dict[str, int] = {}
    out = []
    for r in linhas:
        k = mun.chave_linha(mun.canonico(r))
        n = vistos.get(k, 0)
        vistos[k] = n + 1
        out.append(hashlib.sha256(f"{k}#{n}".encode()).hexdigest()[:32] if n else k)
    return out


def reconciliacao(db: Session, total_origem: int | None, amostra: bool = False) -> dict:
    """CC9: bronze_ok + quarentena == COUNT(*) da origem (exato). Pendentes > 0 -> "ok_com_pendencias", nunca "ok".
    Amostra (TOP AMOSTRA_LIMITE): a igualdade com a origem não se aplica -> status "amostra", ok=None, sem conferência."""
    from sqlalchemy import text
    b, q = db.execute(text(
        "SELECT COUNT(*) FILTER (WHERE quarentena IS NULL), COUNT(*) FILTER (WHERE quarentena IS NOT NULL) "
        "FROM bronze.registros WHERE sistema_origem = 'ISS' AND tabela = :t"), {"t": TABELA}).one()
    if amostra:
        return {"origem": total_origem, "bronze": int(b), "quarentena": int(q), "ok": None, "status": "amostra",
                "rotulo": ROTULO_AMOSTRA, "cor": "cinza"}  # neutro: nunca vermelho/"divergente"
    ok = total_origem is not None and b + q == total_origem
    return {"origem": total_origem, "bronze": int(b), "quarentena": int(q), "ok": ok,
            "status": ("ok_com_pendencias" if q else "ok") if ok else "divergente"}


ROTULO_AMOSTRA = "Amostra · reconciliação só na carga completa"


def texto_quarentena(n: int) -> str:
    return f"{n} linha(s) em quarentena (chave nula)"


def ler_origem(connector) -> list[dict]:
    """Amostra controlada (AMOSTRA_LIMITE, padrão 1000) de ISS.dbo.Municipes."""
    return connector.ler_amostra(TABELA, politica=ISS)


def _data(v) -> date | None:
    from app.util.datas import normaliza_data
    return normaliza_data(v)


def _datahora(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _bronze_iss(db: Session, linhas: list[dict], execucao_id: int, quarentena: bool = False) -> None:
    """Linhas brutas do ISS na bronze (indicador por camada). Sem PK na origem: id = hash estável da linha.
    CC9: com `quarentena`, as linhas ficam marcadas como pendência (guardadas; prata e ouro as ignoram)."""
    from sqlalchemy import text

    from app.bronze.ingestao import ingere
    chaves = _chaves_bronze(linhas)
    regs = [{**r, "_chave_sigpmf": k} for r, k in zip(linhas, chaves, strict=True)]
    ingere(db, "ISS", TABELA, "_chave_sigpmf", "Data_Alteracao", regs, execucao_id, confirmar=False)
    if quarentena and regs:
        import json
        for r in regs:
            db.execute(text("UPDATE bronze.registros SET quarentena = :m, quarentena_campos = CAST(:c AS JSONB) "
                            "WHERE sistema_origem = 'ISS' AND tabela = :t AND id_origem = :k"),
                       {"m": MOTIVO_CHAVE_NULA, "c": json.dumps(chave_nula(r) or list(CHAVE_CC9)), "t": TABELA,
                        "k": r["_chave_sigpmf"]})


def executar_carga_municipe(db: Session, usuario: str, linhas=None, connector=None, bronze: bool = False,
                            nulos: list[dict] | None = None, total_origem: int | None = None,
                            total_nulos: int | None = None) -> Execucao:
    """`nulos`/`total_origem`/`total_nulos` só para testes sintéticos (no conector vêm de SELECTs separados)."""
    ex = Execucao(tipo="carga_municipe_amostra", sistema="ISS", status="em_andamento", usuario=usuario)
    db.add(ex)
    db.commit()
    try:
        if linhas is None:
            try:
                linhas = [linha_json(r, ISS.colunas_select(TABELA)) for r in ler_origem(connector)]
            except Exception as exc:  # noqa: BLE001
                raise FalhaLeitura(TABELA, exc) from exc
        if linhas is not None and hasattr(connector, "_conectar") and nulos is None:  # SqlServerConnector real
            try:
                total_origem, total_nulos, nulos = ler_nulos(connector)
            except Exception as exc:  # noqa: BLE001
                raise FalhaLeitura(TABELA, exc) from exc
            nulos = [linha_json(r, ISS.colunas_select(TABELA)) for r in nulos]
        # Ressalva 2: só colunas permitidas e nunca uma sensível (senha, token, CID, renda...) chegam à bronze.
        linhas = [filtra_colunas(r, ISS.colunas_select(TABELA), ISS.proibidas) for r in linhas]
        nulos = [filtra_colunas(r, ISS.colunas_select(TABELA), ISS.proibidas) for r in (nulos or [])]
        # CC9: chave nula sai do fluxo normal (a amostra pode trazê-las; não duplicam com o SELECT separado).
        ja = set(_chaves_bronze([r for r in linhas if chave_nula(r)]))
        quar = [r for r in linhas if chave_nula(r)]
        quar += [r for r, k in zip(nulos, _chaves_bronze(nulos), strict=True) if k not in ja]
        linhas = [r for r in linhas if not chave_nula(r)]
        n_nulos = total_nulos if total_nulos is not None else len(quar)
        if n_nulos and (total_origem is not None or total_nulos is not None):
            base = total_origem if total_origem is not None else len(linhas) + len(quar)
            if bloqueia_nulos(n_nulos, base):  # CC9.3: problema de origem -> DBA
                pct = 100.0 * n_nulos / max(base, 1)
                raise ChaveNulaAcimaDoLimite(f"Chave nula em {n_nulos} de {base} linha(s) de {TABELA} ({pct:.2f}%), acima do "
                                   f"limite de {limite_nulos_pct():g}% e do mínimo de {limite_nulos_min()} linha(s): "
                                   "corrigir na origem (DBA).")
        if not linhas and not quar:  # 0 linhas lidas: nunca "sem_novidades"
            ex.status, ex.mensagem = ORIGEM_VAZIA, MSG_ORIGEM_VAZIA
            ex.finalizado_em = datetime.now(UTC)
            db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=MSG_ORIGEM_VAZIA))
            db.commit()
            return ex
        if bronze:
            _bronze_iss(db, linhas, ex.id)
        if quar:  # CC9: quarentena sempre guardada na bronze (nunca descartada)
            _bronze_iss(db, quar, ex.id, quarentena=True)
            db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=f"{TABELA}: {texto_quarentena(len(quar))}"))
        existentes = dict(db.execute(select(Municipe.documento, Municipe.hash)).all())
        r = mun.classificar(linhas, existentes)
        if not r.fecha():  # M6: as somas têm de fechar com as lidas
            raise RuntimeError("Contagens do Munícipe não fecham com as linhas lidas.")
        for reg in r.registros:
            d = reg.dados
            db.merge(Municipe(documento=reg.documento, tipo=reg.tipo, tipo_pessoa=d["tipo_pessoa"], nome=d["nome"],
                              nome_mae=d["nome_mae"], data_nascimento=_data(d["data_nascimento"]),
                              pis_pasep=d["pis_pasep"], data_alteracao=_datahora(d["data_alteracao"]),
                              cidadao_id=reg.cidadao_id, confianca=reg.confianca, hash=reg.hash, execucao_id=ex.id))
        for v in r.versoes:
            ja = db.scalar(select(MunicipeVersao.id).where(MunicipeVersao.documento == v["documento"],
                                                           MunicipeVersao.hash == v["hash"]))
            if ja is None:
                db.add(MunicipeVersao(documento=v["documento"], hash=v["hash"], dados=v["dados"], execucao_id=ex.id))
        for p in r.pendencias:
            db.merge(MunicipePendencia(chave=p["chave"], motivo=p["motivo"], tipo_pessoa=p["tipo_pessoa"],
                                       documento_mascarado=p.get("documento_mascarado"),
                                       nome=p["nome"], status="pendente", execucao_id=ex.id))
        c = r.contagem
        ex.lidos, ex.novos, ex.atualizados = c["lidas"], c["novas"], c["atualizadas"]
        ex.ignorados, ex.duplicados, ex.pendentes = c["inalteradas"], c["duplicadas"], c["pendentes"]
        ex.lotes = 1
        ex.status = "sucesso" if (c["novas"] or c["atualizadas"]) else "sem_novidades"
        # Só contagens na mensagem/log: nenhum documento em claro (M7).
        ex.mensagem = (f"lidas={c['lidas']} novas={c['novas']} atualizadas={c['atualizadas']} "
                       f"inalteradas={c['inalteradas']} duplicadas={c['duplicadas']} pendentes={c['pendentes']} "
                       f"cidadaos={c['cidadaos']} empresas={c['empresas']} versoes={len(r.versoes)} "
                       f"divergencia_documento={c['divergencia_documento']}")
        if quar:
            ex.mensagem += f"; {texto_quarentena(len(quar))}"
        if total_origem is not None and bronze:
            rec = reconciliacao(db, total_origem, amostra=ex.tipo == "carga_municipe_amostra")
            ex.mensagem += (f"; reconciliacao origem={rec['origem']} bronze={rec['bronze']} "
                            f"quarentena={rec['quarentena']} status={rec['status']}"
                            + (f" ({rec['rotulo']})" if rec.get("rotulo") else ""))
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=ex.mensagem))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status = "falha"
        ex.registra_erro(exc)
        ex.mensagem = str(exc) if isinstance(exc, ChaveNulaAcimaDoLimite) else f"Falha: {type(exc).__name__}"
    ex.finalizado_em = datetime.now(UTC)
    audita(db, usuario, "carga_executada", f"execucao:{ex.id}",
           {"status": ex.status, "sistema": "ISS", "tabela": TABELA, "lidos": ex.lidos, "novos": ex.novos,
            "atualizados": ex.atualizados, "duplicados": ex.duplicados, "pendentes": ex.pendentes}, commit=False)
    db.commit()
    return ex
