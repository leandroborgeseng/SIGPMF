"""Consultas somente leitura do explorador. Só o Postgres do SIGPMF; nenhum conector de origem é importado aqui (EX3)."""
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import String, and_, cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.explorador import catalogo as cat
from app.explorador.mascara import mascara_campo, mascara_dict, mascara_linha, tipo_sensivel
from app.models.camadas import CorrelacaoPendente, DadoGold, Identificador, MunicipePendencia, RegistroBronze, Vinculo
from app.models.controle import Execucao

_NOME_OK = re.compile(r"^[A-Za-z0-9_.\- ]{1,200}$")
_COL_OK = re.compile(r"^[A-Za-z0-9_]{1,64}$")
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
    out = {"processando": andamento is not None, "etapa": None, "mensagem": None,
           "ultima_carga": _json(ultima_ok.finalizado_em) if ultima_ok else None}
    if andamento is not None:
        etapa = None
        if tem_coluna(db, "controle", "execucoes", "etapa"):
            etapa = db.scalar(text("SELECT etapa FROM controle.execucoes WHERE id=:i"), {"i": andamento.id})
        out["etapa"] = etapa
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


def _amostras(valores, coluna: str) -> list:
    vistos, out = set(), []
    for v in valores:
        r = json.dumps(_json(v), sort_keys=True, default=str)
        if v is None or v == "" or r in vistos:
            continue
        vistos.add(r)
        out.append(mascara_campo(coluna, _json(v)))
        if len(out) == 3:
            break
    return out


def _perfil_item(col: str, tipo: str, total: int, preenchidos: int, distintos: int, exemplos: list) -> dict:
    pct = round(100.0 * preenchidos / total, 1) if total else 0.0
    return {"coluna": col, "tipo": tipo, "preenchido_pct": pct, "nulo_pct": round(100.0 - pct, 1) if total else 0.0,
            "distintos": int(distintos), "exemplos": exemplos, "sensivel": tipo_sensivel(col) is not None}


def perfil(db: Session, c: str, camada: str, tabela: str) -> dict:
    """EX1: tipo, % preenchido/nulo, distintos e 3 exemplos (mascarados) por coluna."""
    if camada == "bronze":
        valida_tabela_bronze(db, c, tabela)
        p = _bronze_base(c, tabela)
        total = int(db.scalar(text("SELECT count(*) FROM bronze.registros WHERE sistema_origem=:s AND tabela=:t"), p))
        cols = []
        for k in chaves_bronze(db, c, tabela):
            q = {**p, "k": k}
            pre, dist, tp = db.execute(text(
                "SELECT count(*) FILTER (WHERE NULLIF(dados::jsonb->>:k,'') IS NOT NULL), "
                "count(DISTINCT dados::jsonb->>:k), mode() WITHIN GROUP (ORDER BY jsonb_typeof(dados::jsonb->:k)) "
                "FROM bronze.registros WHERE sistema_origem=:s AND tabela=:t"), q).one()
            vals = db.scalars(text("SELECT dados::jsonb->:k FROM bronze.registros WHERE sistema_origem=:s AND tabela=:t "
                                   "AND NULLIF(dados::jsonb->>:k,'') IS NOT NULL ORDER BY id LIMIT 50"), q)
            cols.append(_perfil_item(k, f"json:{tp or 'null'}", total, pre or 0, dist or 0, _amostras(vals, k)))
        return {"camada": camada, "tabela": tabela, "linhas": total, "colunas": cols}
    s = spec(camada, c, tabela)
    t, w = s.tabela, _where_conector(s, c)
    total = int(db.scalar(select(func.count()).select_from(t).where(w)) or 0)
    cols = []
    for col in s.colunas:
        cc = t.c[col]
        pre, dist = db.execute(select(func.count(cc), func.count(func.distinct(cast(cc, String)))).where(w)).one()
        vals = db.scalars(select(cc).where(w, cc.is_not(None)).limit(50))
        ex = _amostras(vals, col)
        if s.valor_col == col:  # chave/valor: mascarar sempre os exemplos (o rótulo varia por linha)
            ex = [mascara_campo("documento" if str(v).replace(".", "").replace("-", "").isdigit() else "nome", v)
                  if isinstance(v, str) else v for v in ex]
        cols.append(_perfil_item(col, s.tipo(col), total, pre or 0, dist or 0, ex))
    return {"camada": camada, "tabela": tabela, "linhas": total, "colunas": cols}


# ---------- EX2/EX4: amostra paginada ----------
def _filtro_valor(v: Any) -> str:
    if not isinstance(v, (str, int, float)) or len(str(v)) > 200:
        _422("Filtro inválido")
    return str(v).strip()


def _like(v: str) -> str:
    return "%" + v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def amostra(db: Session, c: str, camada: str, tabela: str, pagina: int, filtros: dict[str, Any],
            somente_pendencias: bool = False) -> dict:
    """No máximo 50 linhas por página, paginação no servidor. Coluna sensível: filtro por igualdade exata
    (não vira oráculo de substring); demais: contém (ILIKE, com escape). Nome de coluna só da allowlist."""
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
        for i, (k, v) in enumerate(filtros.items()):
            if k in cat.BRONZE_META and k == "id_origem":
                conds.append(f"id_origem = :v{i}")
            elif k in chaves:
                conds.append(f"dados::jsonb->>:k{i} = :v{i}" if tipo_sensivel(k)
                             else f"dados::jsonb->>:k{i} ILIKE :v{i} ESCAPE '\\'")
                params[f"k{i}"] = k
            else:
                _422("Coluna não permitida no filtro")
            params[f"v{i}"] = v if (tipo_sensivel(k) or k == "id_origem") else _like(v)
        where = " AND ".join(conds)  # só fragmentos fixos acima; valores e chaves sempre como bind
        extra = ", quarentena" if quar else ""
        total = int(db.scalar(text(f"SELECT count(*) FROM bronze.registros WHERE {where}"), params))
        rows = db.execute(text(f"SELECT id, id_origem, extraido_em, atualizado_origem_em, dados{extra} "
                               f"FROM bronze.registros WHERE {where} ORDER BY id LIMIT :lim OFFSET :off"),
                          {**params, "lim": pp + 1, "off": off}).mappings().all()
        linhas = []
        for r in rows[:pp]:
            d = r["dados"] if isinstance(r["dados"], dict) else json.loads(r["dados"])
            linha = {"id": r["id"], "id_origem": r["id_origem"], "extraido_em": _json(r["extraido_em"]),
                     **{k: _json(d.get(k)) for k in chaves}}
            if quar:
                linha["quarentena"] = r["quarentena"]
            linhas.append(mascara_dict(linha))
        colunas = ["id", "id_origem", "extraido_em", *chaves] + (["quarentena"] if quar else [])
        return _pagina(camada, tabela, colunas, "id", None, linhas, total, pagina, len(rows) > pp)
    s = spec(camada, c, tabela)
    t = s.tabela
    conds = [_where_conector(s, c)]
    for k, v in filtros.items():
        if k not in s.colunas:
            _422("Coluna não permitida no filtro")
        if tipo_sensivel(k) or k == s.valor_col:
            conds.append(cast(t.c[k], String) == v)
        else:
            conds.append(cast(t.c[k], String).ilike(_like(v), escape="\\"))
    w = and_(*conds)
    total = int(db.scalar(select(func.count()).select_from(t).where(w)) or 0)
    rows = db.execute(select(*[t.c[x] for x in s.colunas]).where(w).order_by(t.c[s.id_col]).limit(pp + 1)
                      .offset(off)).mappings().all()
    linhas = [mascara_linha({k: _json(v) for k, v in r.items()}, s.rotulo_col, s.valor_col) for r in rows[:pp]]
    return _pagina(camada, tabela, s.colunas, s.id_col, s.cidadao_col, linhas, total, pagina, len(rows) > pp)


def _pagina(camada, tabela, colunas, id_col, cidadao_col, linhas, total, pagina, tem_proxima) -> dict:
    return {"camada": camada, "tabela": tabela, "colunas": colunas, "id_coluna": id_col, "cidadao_coluna": cidadao_col,
            "linhas": linhas, "total": total, "pagina": pagina, "por_pagina": cat.MAX_LINHAS,
            "tem_anterior": pagina > 1, "tem_proxima": tem_proxima}


def pendencias(db: Session, c: str, pagina: int, filtros: dict[str, Any]) -> dict:
    """EX4: pendências da prata (sem correspondência/ambíguas e afins) + quarentena da bronze (CC9) se a coluna existir."""
    itens: list[dict] = []
    for p in db.scalars(select(CorrelacaoPendente).where(CorrelacaoPendente.sistema == c).order_by(CorrelacaoPendente.id)):
        itens.append({"origem": p.sistema, "tabela": p.tabela, "chave": p.chave_origem, "motivo": p.motivo,
                      "status": p.status, "nome": p.nome})
    if c == "ISS":
        for p in db.scalars(select(MunicipePendencia).order_by(MunicipePendencia.chave)):
            itens.append({"origem": "ISS", "tabela": "Municipes", "chave": p.chave, "motivo": p.motivo,
                          "status": p.status, "nome": p.nome})
    if tem_coluna(db, "bronze", "registros", "quarentena"):
        for r in db.execute(text("SELECT tabela, id_origem, quarentena FROM bronze.registros WHERE sistema_origem=:s "
                                 "AND quarentena IS NOT NULL ORDER BY id"), {"s": c}):
            itens.append({"origem": c, "tabela": r[0], "chave": r[1], "motivo": f"quarentena:{r[2]}",
                          "status": "em quarentena", "nome": None})
    fm = _filtro_valor(filtros.get("motivo")) if filtros and filtros.get("motivo") else None
    if fm:
        itens = [i for i in itens if fm.lower() in (i["motivo"] or "").lower()]
    pp, off = cat.MAX_LINHAS, (pagina - 1) * cat.MAX_LINHAS
    pag = [mascara_dict(i) for i in itens[off:off + pp]]
    return _pagina("prata", "pendencias", ["origem", "tabela", "chave", "motivo", "status", "nome"], "chave", None,
                   pag, len(itens), pagina, off + pp < len(itens)) | {
        "quarentena_disponivel": tem_coluna(db, "bronze", "registros", "quarentena")}


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
        v = db.scalar(text("SELECT dados::jsonb->:k FROM bronze.registros WHERE id=:i AND sistema_origem=:s AND tabela=:t"),
                      {"k": coluna, "i": lid, "s": c, "t": tabela})
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
    """Bronze → Prata → Ouro com descartes por motivo. Os números saem das mesmas contagens do painel (indicadores)."""
    bronze = int(db.scalar(select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == c)) or 0)
    unicos = int(db.scalar(select(func.count(func.distinct(func.concat(RegistroBronze.tabela, "|", RegistroBronze.id_origem))))
                           .where(RegistroBronze.sistema_origem == c)) or 0)
    quar = 0
    if tem_coluna(db, "bronze", "registros", "quarentena"):
        quar = int(db.scalar(text("SELECT count(*) FROM bronze.registros WHERE sistema_origem=:s AND quarentena IS NOT NULL"),
                             {"s": c}) or 0)
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
    return {"conector": c, "bronze": bronze, "bronze_unicos": unicos, "quarentena": quar, "prata": prata,
            "prata_cidadaos": cid_prata, "ouro": ouro, "descartes": descartes, "estado": estado(db, c),
            "quarentena_disponivel": tem_coluna(db, "bronze", "registros", "quarentena")}


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
                            "extraido_em": _json(r.extraido_em), "dados": mascara_dict(r.dados or {})}
    idents = [mascara_linha({"tipo": i.tipo, "valor": i.valor}, "tipo", "valor")
              for i in db.scalars(select(Identificador).where(Identificador.cidadao_id == cidadao_id))]
    ouro = [{"dominio": d, "campo": f, "sistema": s, "quantidade": int(n)} for d, f, s, n in db.execute(
        select(DadoGold.dominio, DadoGold.campo, DadoGold.sistema, func.count()).where(DadoGold.cidadao_id == cidadao_id)
        .group_by(DadoGold.dominio, DadoGold.campo, DadoGold.sistema).order_by(DadoGold.dominio, DadoGold.campo))]
    if not vs and not ouro:
        raise HTTPException(404, "Cidadão não encontrado")
    prata = [{"origem": v.sistema, "tabela": v.tabela, "chave": v.chave_origem, "bronze_id": v.bronze_id,
              "metodo": v.metodo, "confianca": v.confianca, "bronze": bronze.get(v.bronze_id)} for v in vs]
    return {"cidadao_id": cidadao_id, "prata": prata, "identificadores": idents, "ouro": ouro}
