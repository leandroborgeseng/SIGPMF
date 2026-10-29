"""Consultas somente leitura do explorador. Só o Postgres do SIGPMF; nenhum conector de origem é importado aqui (EX3)."""
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import String, and_, cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.explorador import catalogo as cat
from app.explorador.mascara import mascara_campo, mascara_dict, mascara_linha, mascara_restrita, mascara_valor, tipo_sensivel
from app.models.camadas import CorrelacaoPendente, DadoGold, Identificador, MunicipePendencia, RegistroBronze, Vinculo
from app.models.controle import Execucao

_NOME_OK = re.compile(r"^[A-Za-z0-9_.\- ]{1,200}$")
_COL_OK = re.compile(r"^[A-Za-z0-9_]{1,64}$")
ETAPAS = {"leitura": 1, "prata": 2, "ouro": 3}  # controle.execucoes.etapa (CC7)
QUAR_OCULTA = "sei_reclassificado_restrito"  # mesmo valor de QUAR_RECLASS do SEI (sem importar o conector)
STATUS_OK = ("sucesso", "sem_novidades", "concluida", "ok")


def _422(msg: str):
    raise HTTPException(422, msg)


def _json(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def tem_coluna(db: Session, schema: str, tabela: str, coluna: str) -> bool:
    return db.scalar(text("SELECT 1 FROM information_schema.columns WHERE table_schema=:s AND table_name=:t "
                          "AND column_name=:c"), {"s": schema, "t": tabela, "c": coluna}) is not None


# ---------- conectores e estado ----------
def conectores(db: Session) -> list[str]:
    b = set(db.scalars(select(RegistroBronze.sistema_origem).distinct()))
    v = set(db.scalars(select(Vinculo.sistema).distinct()))
    return sorted(b | v)


def valida_conector(db: Session, c: str) -> str:
    if not isinstance(c, str) or not _NOME_OK.match(c) or c not in conectores(db):
        raise HTTPException(404, "Conector não encontrado")
    return c


def _execs(db: Session, c: str) -> list[Execucao]:
    ex = db.scalars(select(Execucao).where(Execucao.sistema.is_not(None)).order_by(Execucao.id.desc()).limit(300))
    return [e for e in ex if c in (e.sistema or "").split("+")]


def estado(db: Session, c: str) -> dict:
    """EX7: 'Processando… etapa X' via coluna controle.execucoes.etapa (CC7) quando existir; senão degrada."""
    execs = _execs(db, c)
    andamento = next((e for e in execs if e.status == "em_andamento"), None)
    ultima_ok = next((e for e in execs if e.status in STATUS_OK and e.finalizado_em), None)
    out = {"processando": andamento is not None, "etapa": None, "etapa_num": None, "etapas_total": 3,
           "atualizado_em": None, "parcial": False, "mensagem": None,
           "ultima_carga": _json(ultima_ok.finalizado_em) if ultima_ok else None}
    if andamento is not None:
        etapa, atualizado = None, None
        if tem_coluna(db, "controle", "execucoes", "etapa"):
            sel = "etapa, atualizado_em" if tem_coluna(db, "controle", "execucoes", "atualizado_em") else "etapa, NULL"
            etapa, atualizado = db.execute(text(f"SELECT {sel} FROM controle.execucoes WHERE id=:i"), {"i": andamento.id}).one()
        num = ETAPAS.get(etapa or "")
        from app.services.carga_completa import NOME_ETAPA
        out.update(etapa=etapa, etapa_nome=NOME_ETAPA.get(etapa or ""), etapa_num=num, etapas_total=3,
                   atualizado_em=_json(atualizado), parcial=True)
        if num:
            out["mensagem"] = f"Processando… etapa {num} de 3 · {NOME_ETAPA.get(etapa, etapa)}"
        else:
            out["mensagem"] = f"Processando… etapa {etapa}" if etapa else "Processando…"
    return out


# ---------- filtros por conector ----------
def _where_conector(spec: cat.Spec, c: str):
    t = spec.tabela
    if spec.filtro == "sistema":
        return t.c.sistema == c
    if spec.filtro == "vinculos":
        return t.c.cidadao_id.in_(select(Vinculo.cidadao_id).where(Vinculo.sistema == c))
    if spec.filtro == "json_array":
        return cast(t.c.sistemas, JSONB).op("@>")(cast(json.dumps([c]), JSONB))
    return text("true") if spec.sistema_fixo == c else text("false")


def specs(camada: str, c: str) -> dict[str, cat.Spec]:
    return {n: s for n, s in cat.POR_CAMADA[camada].items() if s.filtro != "fixo" or s.sistema_fixo == c}


def spec(camada: str, c: str, tabela: str) -> cat.Spec:
    s = specs(camada, c).get(tabela)
    if s is None:
        raise HTTPException(404, "Tabela não encontrada")
    return s


# ---------- EX1: tabelas ----------
def tabelas(db: Session, c: str, camada: str) -> dict:
    est = estado(db, c)
    if camada == "bronze":
        rows = db.execute(select(RegistroBronze.tabela, func.count(), func.max(RegistroBronze.extraido_em))
                          .where(RegistroBronze.sistema_origem == c).group_by(RegistroBronze.tabela)
                          .order_by(RegistroBronze.tabela)).all()
        tabs = [{"tabela": t, "linhas": int(n), "ultima_carga": _json(u)} for t, n, u in rows]
    else:
        tabs = []
        for nome, s in specs(camada, c).items():
            n = db.scalar(select(func.count()).select_from(s.tabela).where(_where_conector(s, c))) or 0
            tabs.append({"tabela": nome, "linhas": int(n), "ultima_carga": est["ultima_carga"]})
    total = sum(t["linhas"] for t in tabs)
    return {"camada": camada, "tabelas": tabs, "total": total, "estado": est,
            "vazia": total == 0, "mensagem_vazia": "Camada ainda não processada" if total == 0 else None}


# ---------- bronze: chaves do JSON ----------
def _bronze_base(c: str, tabela: str) -> dict:
    return {"s": c, "t": tabela}


def valida_tabela_bronze(db: Session, c: str, tabela: str) -> str:
    if not isinstance(tabela, str) or not _NOME_OK.match(tabela):
        raise HTTPException(404, "Tabela não encontrada")
    if db.scalar(select(func.count()).select_from(RegistroBronze)
                 .where(RegistroBronze.sistema_origem == c, RegistroBronze.tabela == tabela)) == 0:
        raise HTTPException(404, "Tabela não encontrada")
    return tabela


def chaves_bronze(db: Session, c: str, tabela: str) -> list[str]:
    ks = db.scalars(text("SELECT DISTINCT k FROM (SELECT dados FROM bronze.registros WHERE sistema_origem=:s AND "
                         "tabela=:t ORDER BY id LIMIT :n) x, LATERAL jsonb_object_keys(x.dados::jsonb) k ORDER BY k"),
                    {**_bronze_base(c, tabela), "n": cat.BRONZE_AMOSTRA_CHAVES})
    return [k for k in ks if _COL_OK.match(k)]


def _amostras(valores, coluna: str, livres=None) -> list:
    vistos, out = set(), []
    for v in valores:
        r = json.dumps(_json(v), sort_keys=True, default=str)
        if v is None or v == "" or r in vistos:
            continue
        vistos.add(r)
        out.append(mascara_campo(coluna, _json(v)) if livres is None else mascara_restrita(coluna, _json(v), livres))
        if len(out) == 3:
            break
    return out


def tipo_amigavel(tecnico: str) -> str:
    t = (tecnico or "").lower()
    if any(x in t for x in ("bool",)):
        return "Sim/Não"
    if any(x in t for x in ("int", "float", "numeric", "number", "double", "real")):
        return "Número"
    if any(x in t for x in ("date", "time")):
        return "Data"
    if any(x in t for x in ("json", "object", "array")) and "string" not in t:
        return "Estruturado"
    return "Texto"


def _perfil_item(col: str, tipo: str, base: int, preenchidos: int, distintos: int, exemplos: list, aprox: bool,
                 sensivel: bool | None = None) -> dict:
    pct = round(100.0 * preenchidos / base, 1) if base else 0.0
    return {"coluna": col, "tipo": tipo_amigavel(tipo), "tipo_tecnico": tipo, "preenchido_pct": pct,
            "nulo_pct": round(100.0 - pct, 1) if base else 0.0, "distintos": int(distintos), "aprox": aprox,
            "exemplos": exemplos, "sensivel": tipo_sensivel(col) is not None if sensivel is None else sensivel}


def perfil(db: Session, c: str, camada: str, tabela: str) -> dict:
    """EX1: tipo, % preenchido/nulo, distintos e 3 exemplos (mascarados) por coluna.
    Custo limitado: % e distintos saem de uma amostra de até PERFIL_AMOSTRA linhas (nunca COUNT(DISTINCT) na
    tabela inteira); se a tabela é maior, os números vêm com aprox=True ("aprox." na tela)."""
    n = cat.PERFIL_AMOSTRA
    if camada == "bronze":
        valida_tabela_bronze(db, c, tabela)
        p = {**_bronze_base(c, tabela), "n": n}
        total = int(db.scalar(text("SELECT count(*) FROM bronze.registros WHERE sistema_origem=:s AND tabela=:t"), p))
        base, aprox = min(total, n), total > n
        amostra_sql = "(SELECT dados::jsonb AS d FROM bronze.registros WHERE sistema_origem=:s AND tabela=:t ORDER BY id LIMIT :n) x"
        cols, livres = [], cat.bronze_livres(c, tabela)
        for k in chaves_bronze(db, c, tabela):
            q = {**p, "k": k}
            pre, dist, tp = db.execute(text(
                "SELECT count(*) FILTER (WHERE NULLIF(d->>:k,'') IS NOT NULL), count(DISTINCT d->>:k), "
                f"mode() WITHIN GROUP (ORDER BY jsonb_typeof(d->:k)) FROM {amostra_sql}"), q).one()
            vals = db.scalars(text(f"SELECT d->:k FROM {amostra_sql} WHERE NULLIF(d->>:k,'') IS NOT NULL LIMIT 50"), q)
            cols.append(_perfil_item(k, f"json:{tp or 'null'}", base, pre or 0, dist or 0, _amostras(vals, k, livres), aprox,
                                     sensivel=k not in livres or tipo_sensivel(k) is not None))
        return {"camada": camada, "tabela": tabela, "linhas": total, "aprox": aprox, "amostra_perfil": base, "colunas": cols}
    s = spec(camada, c, tabela)
    t, w = s.tabela, _where_conector(s, c)
    total = int(db.scalar(select(func.count()).select_from(t).where(w)) or 0)
    base, aprox = min(total, n), total > n
    sub = select(*[t.c[x] for x in s.colunas]).where(w).order_by(t.c[s.id_col]).limit(n).subquery()
    cols = []
    for col in s.colunas:
        cc = sub.c[col]
        pre, dist = db.execute(select(func.count(cc), func.count(func.distinct(cast(cc, String))))).one()
        vals = db.scalars(select(cc).where(cc.is_not(None)).limit(50))
        ex = _amostras(vals, col)
        if s.valor_col == col:  # chave/valor: o rótulo varia por linha, então os exemplos ficam sempre mascarados
            ex = [mascara_valor("documento" if str(v).replace(".", "").replace("-", "").isdigit() else "nome", v)
                  if isinstance(v, str) else v for v in ex]
        cols.append(_perfil_item(col, s.tipo(col), base, pre or 0, dist or 0, ex, aprox))
    return {"camada": camada, "tabela": tabela, "linhas": total, "aprox": aprox, "amostra_perfil": base, "colunas": cols}


# ---------- EX2/EX4: amostra paginada ----------
def _filtro_valor(v: Any) -> str:
    if not isinstance(v, (str, int, float)) or len(str(v)) > 200:
        _422("Filtro inválido")
    return str(v).strip()


def colunas_sensiveis_filtradas(filtros: dict[str, Any], spec_: "cat.Spec | None" = None,
                                bronze: bool = False) -> list[str]:
    """Colunas pessoais (ou o `valor` de tabela chave/valor) usadas no filtro: exigem admin_lgpd + motivo.
    Bronze (P1, sem catálogo): QUALQUER filtro que não seja `id` exige admin_lgpd + motivo."""
    out = []
    for k, v in (filtros or {}).items():
        if v in (None, ""):
            continue
        if (bronze and k != "id") or tipo_sensivel(k) or (spec_ is not None and k == spec_.valor_col):
            out.append(k)
    return sorted(out)


def amostra(db: Session, c: str, camada: str, tabela: str, pagina: int, filtros: dict[str, Any],
            somente_pendencias: bool = False) -> dict:
    """No máximo 50 linhas por página, paginação no servidor, sem COUNT total (UX-E2: "Página X").
    Filtro: SEMPRE igualdade exata (nunca LIKE), nome de coluna só da allowlist, valor como bind."""
    if camada == "prata" and somente_pendencias:
        return pendencias(db, c, pagina, filtros)
    pp = cat.MAX_LINHAS
    off = (pagina - 1) * pp
    filtros = {k: _filtro_valor(v) for k, v in (filtros or {}).items() if v not in (None, "")}
    if camada == "bronze":
        valida_tabela_bronze(db, c, tabela)
        chaves = chaves_bronze(db, c, tabela)
        quar = tem_coluna(db, "bronze", "registros", "quarentena")
        conds, params = ["sistema_origem=:s", "tabela=:t"], _bronze_base(c, tabela)
        if quar:  # SEI: processo reclassificado para não público some do explorador (nada é apagado)
            conds.append(f"quarentena IS DISTINCT FROM '{QUAR_OCULTA}'")
        for i, (k, v) in enumerate(filtros.items()):
            if k == "id_origem":
                conds.append(f"id_origem = :v{i}")
            elif k == "id":
                if not v.isdigit():
                    _422("Filtro inválido")
                conds.append(f"id = :v{i}")
                v = int(v)
            elif k in chaves:
                conds.append(f"dados::jsonb->>:k{i} = :v{i}")
                params[f"k{i}"] = k
            else:
                _422("Coluna não permitida no filtro")
            params[f"v{i}"] = v
        where = " AND ".join(conds)  # só fragmentos fixos acima; valores e chaves sempre como bind
        extra = ", quarentena" if quar else ""
        rows = db.execute(text(f"SELECT id, id_origem, extraido_em, atualizado_origem_em, dados{extra} "
                               f"FROM bronze.registros WHERE {where} ORDER BY id LIMIT :lim OFFSET :off"),
                          {**params, "lim": pp + 1, "off": off}).mappings().all()
        linhas, livres = [], cat.bronze_livres(c, tabela)
        for r in rows[:pp]:
            d = r["dados"] if isinstance(r["dados"], dict) else json.loads(r["dados"])
            linha = mascara_dict({"id": r["id"], "id_origem": r["id_origem"], "extraido_em": _json(r["extraido_em"])})
            linha.update({k: mascara_restrita(k, _json(d.get(k)), livres) for k in chaves if k not in linha})
            if quar:
                linha["quarentena"] = r["quarentena"]
            linhas.append(linha)
        colunas = ["id", "id_origem", "extraido_em", *chaves] + (["quarentena"] if quar else [])
        sens = [k for k in chaves if k not in livres or tipo_sensivel(k)]
        return _pagina(camada, tabela, colunas, "id", None, linhas, pagina, len(rows) > pp, sens)
    s = spec(camada, c, tabela)
    t = s.tabela
    conds = [_where_conector(s, c)]
    for k, v in filtros.items():
        if k not in s.colunas:
            _422("Coluna não permitida no filtro")
        conds.append(cast(t.c[k], String) == v)
    rows = db.execute(select(*[t.c[x] for x in s.colunas]).where(and_(*conds)).order_by(t.c[s.id_col]).limit(pp + 1)
                      .offset(off)).mappings().all()
    linhas = [mascara_linha({k: _json(v) for k, v in r.items()}, s.rotulo_col, s.valor_col) for r in rows[:pp]]
    sens = [k for k in s.colunas if tipo_sensivel(k) or k == s.valor_col]
    return _pagina(camada, tabela, s.colunas, s.id_col, s.cidadao_col, linhas, pagina, len(rows) > pp, sens)


def _pagina(camada, tabela, colunas, id_col, cidadao_col, linhas, pagina, tem_proxima, sensiveis=()) -> dict:
    return {"camada": camada, "tabela": tabela, "colunas": colunas, "id_coluna": id_col, "cidadao_coluna": cidadao_col,
            "colunas_sensiveis": list(sensiveis), "linhas": linhas, "pagina": pagina, "por_pagina": cat.MAX_LINHAS,
            "tem_anterior": pagina > 1, "tem_proxima": tem_proxima}


MOTIVOS = {
    "sem_correspondencia": "Sem correspondência",
    "obito_ambiguo": "Óbito com mais de um candidato",
    "obito_revisao": "Óbito em revisão",
    "obito_sem_candidato": "Óbito sem cidadão correspondente",
    "terceiro_cpf_invalido": "CPF de terceiro inválido",
    "versoes_duplicadas": "Duplicata",
    "duplicata": "Duplicata",
    "em_quarentena": "Chave nula na origem",
    "cidadao_sem_dado_no_ouro": "Cidadão sem dado no ouro",
    "ambiguo": "Mais de um candidato",
}


def rotulo_motivo(m: str | None) -> str:
    if not m:
        return "—"
    if m.startswith("quarentena"):
        return "Chave nula na origem"
    return MOTIVOS.get(m, m.replace("_", " ").capitalize())


def _chave_mascarada(ch: str | None) -> str | None:
    """UX-E5: chave da origem mascarada (pode conter documento); mostra só o começo."""
    if not ch:
        return ch
    ch = str(ch)
    return ch[:2] + "***" if len(ch) > 2 else "***"


def pendencias(db: Session, c: str, pagina: int, filtros: dict[str, Any]) -> dict:
    """EX4/UX-E5: Motivo (rótulo + código), Tabela, Chave mascarada, Data da carga. 'em quarentena', nunca 'ignorada'."""
    itens: list[dict] = []
    datas = {}
    pend = list(db.scalars(select(CorrelacaoPendente).where(CorrelacaoPendente.sistema == c).order_by(CorrelacaoPendente.id)))
    if pend:
        datas = dict(db.execute(select(RegistroBronze.id, RegistroBronze.extraido_em)
                                .where(RegistroBronze.id.in_([p.bronze_id for p in pend]))).all())
    for p in pend:
        itens.append({"motivo_codigo": p.motivo, "tabela": p.tabela, "chave": p.chave_origem,
                      "data_carga": _json(datas.get(p.bronze_id)), "status": p.status})
    if c == "ISS":
        for p in db.scalars(select(MunicipePendencia).order_by(MunicipePendencia.chave)):
            itens.append({"motivo_codigo": p.motivo, "tabela": "Municipes", "chave": p.chave, "data_carga": None,
                          "status": p.status})
    quar = tem_coluna(db, "bronze", "registros", "quarentena")
    if quar:
        for r in db.execute(text("SELECT tabela, id_origem, quarentena, extraido_em FROM bronze.registros "
                                 "WHERE sistema_origem=:s AND quarentena IS NOT NULL AND quarentena <> :oc ORDER BY id"),
                            {"s": c, "oc": QUAR_OCULTA}):
            itens.append({"motivo_codigo": f"quarentena:{r[2]}", "tabela": r[0], "chave": r[1],
                          "data_carga": _json(r[3]), "status": "em quarentena"})
    fm = _filtro_valor(filtros.get("motivo")) if filtros and filtros.get("motivo") else None
    if fm:  # igualdade exata no código do motivo (ou no rótulo)
        itens = [i for i in itens if fm in (i["motivo_codigo"], rotulo_motivo(i["motivo_codigo"]))
                 or (fm == "em_quarentena" and i["motivo_codigo"].startswith("quarentena"))]
    pp, off = cat.MAX_LINHAS, (pagina - 1) * cat.MAX_LINHAS
    pag = [{"motivo": rotulo_motivo(i["motivo_codigo"]), **i, "chave": _chave_mascarada(i["chave"])}
           for i in itens[off:off + pp]]
    return _pagina("prata", "pendencias", ["motivo", "tabela", "chave", "data_carga"], "chave", None,
                   pag, pagina, off + pp < len(itens)) | {"quarentena_disponivel": quar}


# ---------- revelar (admin_lgpd) ----------
def valor_original(db: Session, c: str, camada: str, tabela: str, linha_id: Any, coluna: str) -> Any:
    if camada == "bronze":
        valida_tabela_bronze(db, c, tabela)
        if coluna not in chaves_bronze(db, c, tabela):
            _422("Coluna não permitida")
        try:
            lid = int(linha_id)
        except (TypeError, ValueError):
            _422("Linha inválida")
        v = db.scalar(text("SELECT dados::jsonb->:k FROM bronze.registros WHERE id=:i AND sistema_origem=:s AND tabela=:t "
                           "AND quarentena IS DISTINCT FROM :oc"),
                      {"k": coluna, "i": lid, "s": c, "t": tabela, "oc": QUAR_OCULTA})
        return v
    s = spec(camada, c, tabela)
    if coluna not in s.colunas:
        _422("Coluna não permitida")
    t = s.tabela
    idc = t.c[s.id_col]
    try:
        lid = int(linha_id) if issubclass(idc.type.python_type, int) else str(linha_id)
    except (TypeError, ValueError, NotImplementedError):
        lid = str(linha_id)
    r = db.execute(select(t.c[coluna]).where(idc == lid, _where_conector(s, c))).first()
    if r is None:
        raise HTTPException(404, "Linha não encontrada")
    return _json(r[0])


# ---------- EX4 funil ----------
def funil(db: Session, c: str) -> dict:
    """Bronze → Prata → Ouro com descartes por motivo. A reconciliação vem da MESMA função da carga completa
    (carga_completa.reconciliacao: bronze + quarentena == COUNT(*) da origem, exato) para os totais baterem."""
    from app.services.carga_completa import reconciliacao  # só leitura do Postgres do SIGPMF

    def distintos(quar: bool | None) -> int:
        q = select(func.count(func.distinct(func.concat(RegistroBronze.tabela, "|", RegistroBronze.id_origem)))).where(
            RegistroBronze.sistema_origem == c)
        if quar is not None:
            q = q.where(RegistroBronze.quarentena.is_not(None) if quar else RegistroBronze.quarentena.is_(None))
        return int(db.scalar(q) or 0)

    bronze = int(db.scalar(select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == c)) or 0)
    unicos, bronze_ok, quar = distintos(None), distintos(False), distintos(True)
    prata = int(db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == c)) or 0)
    ouro = int(db.scalar(select(func.count(func.distinct(DadoGold.cidadao_id))).where(DadoGold.sistema == c)) or 0)
    descartes = []
    if bronze - unicos:
        descartes.append({"etapa": "bronze→prata", "motivo": "versoes_duplicadas", "quantidade": bronze - unicos})
    if quar:
        descartes.append({"etapa": "bronze→prata", "motivo": "em_quarentena", "quantidade": quar})
    for m, n in db.execute(select(CorrelacaoPendente.motivo, func.count()).where(CorrelacaoPendente.sistema == c)
                           .group_by(CorrelacaoPendente.motivo).order_by(CorrelacaoPendente.motivo)):
        descartes.append({"etapa": "bronze→prata", "motivo": m, "quantidade": int(n)})
    cid_prata = int(db.scalar(select(func.count(func.distinct(Vinculo.cidadao_id))).where(Vinculo.sistema == c)) or 0)
    if cid_prata - ouro > 0:
        descartes.append({"etapa": "prata→ouro", "motivo": "cidadao_sem_dado_no_ouro", "quantidade": cid_prata - ouro})
    for d in descartes:
        d["rotulo"] = "Chave nula na origem" if d["motivo"] == "em_quarentena" else rotulo_motivo(d["motivo"])
        d["etapa"] = d["etapa"].replace("bronze→prata", "Bronze → Prata").replace("prata→ouro", "Prata → Ouro")
    rec = reconciliacao(db, c)
    rec_tot = None
    if rec:
        origem = [r["origem"] for r in rec.values()]
        o = sum(x for x in origem if x is not None) if all(x is not None for x in origem) else None
        b, q = sum(r["bronze"] for r in rec.values()), sum(r["quarentena"] for r in rec.values())
        ok = all(r["ok"] for r in rec.values())
        rec_tot = {"origem": o, "bronze": b, "quarentena": q, "ok": ok,
                   "status": ("ok_com_pendencias" if q else "ok") if ok else "divergente",
                   "tabelas": [{"tabela": t, **r} for t, r in rec.items()]}
    return {"conector": c, "bronze": bronze, "bronze_unicos": unicos, "bronze_ok": bronze_ok, "quarentena": quar,
            "prata": prata, "prata_cidadaos": cid_prata, "ouro": ouro, "descartes": descartes, "estado": estado(db, c),
            "quarentena_disponivel": True, "reconciliacao": rec_tot}


# ---------- EX5 linhagem ----------
def linhagem(db: Session, cidadao_id: str) -> dict:
    if not isinstance(cidadao_id, str) or not re.match(r"^[0-9a-fA-F-]{1,36}$", cidadao_id):
        _422("cidadao_id inválido")
    vs = list(db.scalars(select(Vinculo).where(Vinculo.cidadao_id == cidadao_id).order_by(Vinculo.id)))
    ids = [v.bronze_id for v in vs]
    bronze = {}
    if ids:
        for r in db.scalars(select(RegistroBronze).where(RegistroBronze.id.in_(ids))):
            bronze[r.id] = {"bronze_id": r.id, "origem": r.sistema_origem, "tabela": r.tabela, "chave": r.id_origem,
                            "extraido_em": _json(r.extraido_em),
                            "dados": {k: mascara_restrita(k, _json(v), cat.bronze_livres(r.sistema_origem, r.tabela))
                                      for k, v in (r.dados or {}).items()}}
    idents = [mascara_linha({"tipo": i.tipo, "valor": i.valor}, "tipo", "valor")
              for i in db.scalars(select(Identificador).where(Identificador.cidadao_id == cidadao_id))]
    ouro = [{"dominio": d, "campo": f, "sistema": s, "quantidade": int(n)} for d, f, s, n in db.execute(
        select(DadoGold.dominio, DadoGold.campo, DadoGold.sistema, func.count()).where(DadoGold.cidadao_id == cidadao_id)
        .group_by(DadoGold.dominio, DadoGold.campo, DadoGold.sistema).order_by(DadoGold.dominio, DadoGold.campo))]
    if not vs and not ouro:
        raise HTTPException(404, "Cidadão não encontrado")
    prata = [{"origem": v.sistema, "tabela": v.tabela, "chave": v.chave_origem, "bronze_id": v.bronze_id,
              "metodo": v.metodo, "confianca": v.confianca, "bronze": bronze.get(v.bronze_id)} for v in vs]
    veio_de = []  # UX-E6: Conector, Tabela, Chave, Camada (+ alvo para abrir a linha)
    for v in vs:
        ch = _chave_mascarada(v.chave_origem)
        veio_de.append({"conector": v.sistema, "tabela": "vinculos", "chave": ch, "camada": "prata",
                        "abrir": {"camada": "prata", "tabela": "vinculos", "filtros": {"id": str(v.id)}}})
        b = bronze.get(v.bronze_id)
        if b:
            veio_de.append({"conector": b["origem"], "tabela": b["tabela"], "chave": ch, "camada": "bronze",
                            "abrir": {"camada": "bronze", "tabela": b["tabela"], "filtros": {"id": str(b["bronze_id"])}}})
    for p in prata:
        p["chave"] = _chave_mascarada(p["chave"])
        if p["bronze"]:
            p["bronze"]["chave"] = _chave_mascarada(p["bronze"]["chave"])
    return {"cidadao_id": cidadao_id, "veio_de": veio_de, "prata": prata, "identificadores": idents, "ouro": ouro}
