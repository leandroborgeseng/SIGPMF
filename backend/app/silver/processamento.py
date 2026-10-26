"""Silver: reconstrói o cadastro correlacionado a partir da versão mais recente de cada registro bronze.
Determinístico: com o mesmo bronze (e as mesmas confirmações humanas), o resultado é o mesmo (A14).

Ordem dos passes:
1. CPF válido (pessoas e terceiros): score 1,0. Mesmo CPF com nascimento divergente -> ambíguo.
2. CNS (Saúde, S5/S6): definitivo válido associa (1,0); provisório só leva à revisão (peso em controle.parametros).
   Vários CNS na mesma matrícula = mesma pessoa. O mesmo CNS em matrículas diferentes = ambíguo, pendente.
3. Atributos (nome normalizado, nascimento, mãe): >= limiar -> revisão; abaixo -> novo cidadao_id.
   Terceiros (titulares, autorizados, leilão) sem CPF válido não passam por aqui: vão para revisão (CM5).
4. Registros de inconsistência do e-SUS: sempre revisão (S7).
5. Óbitos (CM4-CM8): união Obitos + históricas, dedupe pela chave do sepultamento (vale Obitos, divergência
   registrada); candidatos por nome + mãe (sexo não pode divergir); nunca associa sozinho. Só a confirmação
   humana (controle.confirmacoes_obito) marca o cidadão como falecido.
"""
from collections import defaultdict
from datetime import date
from itertools import combinations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.identity import cns as cnsmod
from app.identity import correlacao as corr
from app.identity.cpf import mascara, normaliza, valido
from app.ingestion.mapeamentos import MAPEAMENTOS, canonico
from app.models.camadas import (
    Cidadao,
    CorrelacaoPendente,
    Endereco,
    Identificador,
    Inconsistencia,
    RegistroBronze,
    SistemaSilver,
    Vinculo,
)
from app.models.controle import ConfirmacaoObito, Parametro, Sistema


def _params(db: Session) -> tuple[corr.Parametros, float]:
    v = {p.chave: float(p.valor) for p in db.scalars(select(Parametro))}
    d = corr.Parametros()
    p = corr.Parametros(v.get("correlacao_limiar_revisao", d.limiar_revisao), v.get("correlacao_peso_nome", d.peso_nome),
                        v.get("correlacao_peso_nascimento", d.peso_nascimento), v.get("correlacao_peso_mae", d.peso_mae))
    return p, v.get("correlacao_peso_cns_provisorio", 0.95)


def parametros(db: Session) -> corr.Parametros:
    return _params(db)[0]


def _ultimas_versoes(db: Session) -> list[RegistroBronze]:
    sub = select(func.max(RegistroBronze.id)).where(RegistroBronze.quarentena.is_(None)).group_by(  # CC9
        RegistroBronze.sistema_origem, RegistroBronze.tabela, RegistroBronze.id_origem)
    return list(db.scalars(select(RegistroBronze).where(RegistroBronze.id.in_(sub)).order_by(RegistroBronze.id)))


def _data(v) -> date | None:
    from app.util.datas import normaliza_data
    return normaliza_data(v)


def _sexo(v) -> str | None:
    s = corr.normaliza_nome(v)
    return s[:1] if s else None


class _Indice:
    """Cidadãos por atributo exato (nome normalizado, nascimento, mãe normalizada), cada balde na ordem de inserção.

    corr.score soma o peso de cada atributo igual (nome, data_nascimento, nome_mae), com teto 0,99. Com pesos
    positivos, o score de um cidadão é o valor do conjunto exato de atributos que ele compartilha; logo o melhor score
    é o maior valor entre os subconjuntos S dos atributos do registro que têm alguém em comum (interseção dos baldes
    de S), e o vencedor do algoritmo antigo (primeiro na ordem de inserção com o score máximo, comparação estrita) é o
    menor primeiro-da-interseção entre os S com esse valor. Até 7 subconjuntos por registro, cada interseção começa
    pelo menor balde: sem o O(n²) e com exatamente o mesmo resultado (teste de regressão contra o linear)."""

    def __init__(self):
        self.pos: dict[str, int] = {}
        self.chaves: dict[str, list[tuple]] = {}
        self.baldes: dict[tuple, dict[str, None]] = defaultdict(dict)

    @staticmethod
    def _chaves(c: dict) -> list[tuple]:
        ks = []
        n, mae = corr.normaliza_nome(c.get("nome")), corr.normaliza_nome(c.get("nome_mae"))
        if n:
            ks.append(("nome", n))
        if c.get("data_nascimento"):
            ks.append(("data_nascimento", c.get("data_nascimento")))
        if mae:
            ks.append(("nome_mae", mae))
        if n and mae:
            ks.append(("nm", n, mae))  # óbitos: nome + mãe
        return ks

    def poe(self, cid: str, c: dict) -> None:
        reatribuido = cid in self.chaves
        for k in self.chaves.pop(cid, []):  # cid reatribuído: mantém a posição, troca os atributos (como o dict)
            self.baldes[k].pop(cid, None)
        self.pos.setdefault(cid, len(self.pos))
        self.chaves[cid] = ks = self._chaves(c)
        for k in ks:
            b = self.baldes[k]
            b[cid] = None
            if reatribuido:  # raro: reordena o balde pela posição original
                self.baldes[k] = dict.fromkeys(sorted(b, key=self.pos.__getitem__))

    def _primeiro(self, ks: list[tuple]) -> str | None:
        bs = sorted((self.baldes.get(k) or {} for k in ks), key=len)
        if not bs[0]:
            return None
        for cid in bs[0]:  # balde em ordem de inserção: o primeiro que está em todos é o de menor posição
            if all(cid in b for b in bs[1:]):
                return cid
        return None

    def melhor(self, c: dict, p: corr.Parametros) -> tuple[float, str | None]:
        pesos = {"nome": p.peso_nome, "data_nascimento": p.peso_nascimento, "nome_mae": p.peso_mae}
        ks = [k for k in self._chaves(c) if k[0] != "nm"]
        achados = []
        for r in range(1, len(ks) + 1):
            for sub in combinations(ks, r):
                cid = self._primeiro(list(sub))
                if cid is not None:
                    achados.append((min(round(sum(pesos[k[0]] for k in sub), 4), 0.99), self.pos[cid], cid))
        if not achados:
            return 0.0, None
        top = max(v for v, _, _ in achados)
        return top, min((pos, cid) for v, pos, cid in achados if v == top)[1]

    def candidatos(self, c: dict, so: str | None = None) -> list[str]:
        ks = [k for k in self._chaves(c) if (k[0] == so if so else k[0] != "nm")]
        out = set()
        for k in ks:
            out.update(self.baldes.get(k, ()))
        return sorted(out, key=self.pos.__getitem__)


def reconstruir(db: Session, progresso=None, indexado: bool = True) -> dict[str, int]:
    """progresso(processados, total): sinal de vida (chamado a cada bloco). indexado=False mantém o algoritmo
    linear antigo só para o teste de regressão (mesmo resultado)."""
    p, peso_prov = _params(db)
    for m in (Endereco, Vinculo, Identificador, CorrelacaoPendente, Inconsistencia, Cidadao, SistemaSilver):
        db.execute(delete(m))
    for s in db.scalars(select(Sistema)):
        db.add(SistemaSilver(codigo=s.codigo, nome=s.nome, dominio=s.dominio))

    cid_dados: dict[str, dict] = {}
    idx = _Indice()
    pesos_positivos = min(p.peso_nome, p.peso_nascimento, p.peso_mae) > 0
    cidadaos: dict[str, Cidadao] = {}
    ids: dict[tuple[str, str], Identificador] = {}
    vinculos: list[tuple[RegistroBronze, dict, str, str, float, list[str]]] = []
    pend: list[CorrelacaoPendente] = []
    inc: list[Inconsistencia] = []

    def novo(cid, r, c):
        cidadaos[cid] = Cidadao(cidadao_id=cid, nome=c.get("nome") or "", data_nascimento=_data(c.get("data_nascimento")),
                                atualizado_em=r.atualizado_origem_em)
        cid_dados[cid] = c
        idx.poe(cid, c)

    def ident(cid, tipo, valor):
        if (tipo, valor) not in ids:
            ids[(tipo, valor)] = Identificador(cidadao_id=cid, tipo=tipo, valor=valor)

    def pendente(r, c, motivo, candidatos=(), detalhe=None):
        pend.append(CorrelacaoPendente(sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem, bronze_id=r.id,
                                       motivo=motivo, nome=c.get("nome"), candidatos=sorted(set(candidatos)), detalhe=detalhe))

    def inconsist(r, tipo, campo, detalhe, cid=None):
        inc.append(Inconsistencia(cidadao_id=cid, sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem,
                                  tipo=tipo, campo=campo, detalhe=detalhe))

    regs = []
    for r in _ultimas_versoes(db):
        m = MAPEAMENTOS.get((r.sistema_origem, r.tabela), {})
        if not m:  # bronze sem mapeamento para a silver (ex.: ISS.Municipes, que tem silver própria): não correlaciona
            continue
        regs.append((r, canonico(r.sistema_origem, r.tabela, r.dados), m.get("papel", "pessoa"), m))
    por_papel = defaultdict(list)
    for x in regs:
        por_papel[x[2]].append(x)
    total, feitos = len(regs), [0]

    def passo():
        """CC11: uma unidade só, cada registro da bronze contado uma vez (nunca passa do total)."""
        feitos[0] += 1
        if progresso and feitos[0] % 5000 == 0:
            progresso(min(feitos[0], total), total)

    # CNS por pessoa (sistema, matrícula) e donos de cada CNS
    cns_pessoa: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r, c, _, _ in por_papel["pessoa"] + por_papel["cns_extra"]:
        bruto = c.get("cns")
        if not bruto:
            continue
        dono = (r.sistema_origem, str(c.get("chave_pessoa") or r.id_origem))
        if cnsmod.valido(bruto):
            cns_pessoa[dono].add(cnsmod.normaliza(bruto))
        else:
            inconsist(r, "cns_invalido", "cns", f"CNS inválido {cnsmod.mascara(bruto)}")
    donos: dict[str, set] = defaultdict(set)
    for dono, cs in cns_pessoa.items():
        for x in cs:
            donos[x].add(dono)
    cns_ambiguos = {x for x, d in donos.items() if len(d) > 1}

    def cns_de(r):
        return cns_pessoa.get((r.sistema_origem, r.id_origem), set())

    # 1) CPF
    sem_cpf = []
    for r, c, papel, _ in por_papel["pessoa"] + por_papel["terceiro"]:
        passo()
        cpf = normaliza(c.get("cpf"))
        if cpf and valido(cpf):
            cid = corr.cidadao_id_por_cpf(cpf)
            if cid not in cidadaos:
                novo(cid, r, c)
                ident(cid, "cpf", cpf)
            elif papel == "pessoa" and c.get("data_nascimento") and cid_dados[cid].get("data_nascimento") \
                    and cid_dados[cid].get("data_nascimento") != c.get("data_nascimento"):
                pendente(r, c, "ambiguo", [cid])
                inconsist(r, "cpf_nascimento_divergente", "data_nascimento",
                          f"Mesmo CPF {mascara(cpf)} com data de nascimento divergente", cid)
                continue
            elif r.atualizado_origem_em and (cidadaos[cid].atualizado_em is None or r.atualizado_origem_em > cidadaos[cid].atualizado_em):
                cidadaos[cid].nome, cidadaos[cid].atualizado_em = c.get("nome") or cidadaos[cid].nome, r.atualizado_origem_em
            vinculos.append((r, c, cid, "cpf", 1.0, ["cpf"]))
            for x in cns_de(r) - cns_ambiguos:
                ident(cid, "cns", x)
        else:
            if cpf:
                inconsist(r, "cpf_invalido", "cpf", f"CPF inválido {mascara(cpf)}")
            if papel == "terceiro":
                pendente(r, c, "terceiro_cpf_invalido")
            else:
                sem_cpf.append((r, c))

    # 2) CNS
    sem_ident = []
    for r, c in sem_cpf:
        meus = cns_de(r)
        if meus and meus <= cns_ambiguos:
            outros = sorted({f"{s}:{m}" for x in meus for (s, m) in donos[x] if m != r.id_origem})
            pendente(r, c, "cns_ambiguo", detalhe={"outras_matriculas": outros})
            continue
        validos = meus - cns_ambiguos
        defin = sorted(x for x in validos if cnsmod.tipo(x) == "definitivo")
        existente = next((ids[("cns", x)].cidadao_id for x in sorted(validos) if ("cns", x) in ids), None)
        if defin and existente:
            vinculos.append((r, c, existente, "cns", 1.0, ["cns"]))
        elif defin:
            cid = str(corr.uuid.uuid5(corr.NAMESPACE, f"cns:{defin[0]}"))
            novo(cid, r, c)
            vinculos.append((r, c, cid, "cns", 1.0, ["cns"]))
        elif existente:  # só provisório batendo com alguém: revisão
            pendente(r, c, "revisao", [existente], {"score": peso_prov, "atributos": ["cns_provisorio"]})
            continue
        else:
            sem_ident.append((r, c))
            continue
        for x in validos:
            ident(vinculos[-1][2], "cns", x)

    # 3) atributos
    for r, c in sem_ident:  # já contados na etapa 1 (CC11: sem contar de novo)
        melhor, mcid, mattrs = 0.0, None, []
        if indexado and pesos_positivos:
            top, cid = idx.melhor(c, p)
            if cid is not None and top > melhor:
                melhor, mcid = top, cid
                mattrs = corr.score(c, cid_dados[cid], p)[1]
        else:  # algoritmo linear (referência do teste; pesos não positivos em controle.parametros)
            for cid in cid_dados:
                sc, attrs = corr.score(c, cid_dados[cid], p)
                if sc > melhor:
                    melhor, mcid, mattrs = sc, cid, attrs
        if mcid and melhor >= p.limiar_revisao:
            pendente(r, c, "revisao", [mcid], {"score": melhor, "atributos": mattrs})
            continue
        cid = corr.cidadao_id_por_origem(r.sistema_origem, r.tabela, r.id_origem)
        novo(cid, r, c)
        vinculos.append((r, c, cid, "novo_sem_cpf", round(melhor, 4), mattrs))
        for x in cns_de(r) - cns_ambiguos:
            ident(cid, "cns", x)

    # 4) inconsistências do e-SUS: sempre revisão
    for _ in por_papel["cns_extra"]:  # só alimentam o CNS acima: contam uma vez aqui
        passo()
    for r, c, _, _ in por_papel["revisao"]:
        passo()
        x = cnsmod.normaliza(c.get("cns"))
        cand = [ids[("cns", x)].cidadao_id] if x and ("cns", x) in ids else []
        pendente(r, c, "esus_inconsistencia", cand)

    # 5) óbitos: união + dedupe + candidatos
    confirmados = {(k.sistema, k.chave_origem): k for k in db.scalars(select(ConfirmacaoObito))}
    grupos: dict[tuple[str, str], list] = defaultdict(list)
    for r, c, _, m in por_papel["obito"]:
        grupos[(r.sistema_origem, r.id_origem)].append((m.get("prioridade", 9), r.tabela, r, c))
    for (sis, chave), itens in sorted(grupos.items()):
        for _ in itens:  # cada registro da bronze do grupo conta uma vez
            passo()
        itens.sort(key=lambda t: (t[0], t[1]))
        _, _, r, c = itens[0]
        for _, tab, r2, c2 in itens[1:]:
            dif = [k for k in ("nome", "nome_mae", "sexo", "data_obito") if (c2.get(k) or None) != (c.get(k) or None)]
            if dif:
                inconsist(r2, "obito_divergente_historico", ",".join(dif),
                          f"Divergência com {r.tabela} na chave do sepultamento; prevalece {r.tabela}")
        cand = []
        if c.get("nome") and c.get("nome_mae"):
            # nome/mãe só com espaços normalizam para "" e casam com quem também está vazio: aí vale o linear
            indexavel = corr.normaliza_nome(c.get("nome")) and corr.normaliza_nome(c.get("nome_mae"))
            for cid in (idx.candidatos(c, "nm") if indexado and indexavel else cid_dados):
                ref = cid_dados[cid]
                if corr.normaliza_nome(ref.get("nome")) == corr.normaliza_nome(c.get("nome")) and \
                        corr.normaliza_nome(ref.get("nome_mae")) == corr.normaliza_nome(c.get("nome_mae")):
                    s1, s2 = _sexo(ref.get("sexo")), _sexo(c.get("sexo"))
                    if s1 and s2 and s1 != s2:
                        continue
                    cand.append(cid)
        conf = confirmados.get((sis, chave))
        if conf and conf.cidadao_id in cand:
            cz = cidadaos[conf.cidadao_id]
            cz.falecido, cz.data_obito, cz.obito_fonte = True, _data(c.get("data_obito")), f"{sis}.{r.tabela}"
            vinculos.append((r, c, conf.cidadao_id, "obito_confirmado", 1.0, ["confirmacao_humana"]))
            continue
        sinais = {}
        for cid in cand:
            nasc, morte = cidadaos[cid].data_nascimento, _data(c.get("data_obito"))
            if nasc and morte:
                sinais[cid] = {"idade_estimada": (morte - nasc).days // 365, "sinal": "fraco"}
        motivo = "obito_revisao" if len(cand) == 1 else "obito_ambiguo" if cand else "obito_sem_candidato"
        pendente(r, c, motivo, cand, {"tabela_origem": r.tabela, "sinais": sinais, "pre_selecionado": None})

    db.add_all(cidadaos.values())
    db.flush()
    db.add_all(list(ids.values()) + pend + inc)
    enderecos: list[Endereco] = []
    for r, c, cid, metodo, conf, attrs in vinculos:
        db.add(Vinculo(cidadao_id=cid, sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem, bronze_id=r.id,
                       metodo=metodo, confianca=conf, atributos=attrs, fonte_atualizado_em=r.atualizado_origem_em))
        if c.get("endereco"):  # CM10: endereço de sepultura/titular fica como texto livre
            enderecos.append(Endereco(cidadao_id=cid, logradouro=c["endereco"], sistema=r.sistema_origem,
                                      fonte_atualizado_em=r.atualizado_origem_em))
    ultimo: dict[str, Endereco] = {}
    for e in enderecos:
        u = ultimo.get(e.cidadao_id)
        if u is None or (e.fonte_atualizado_em and (u.fonte_atualizado_em is None or e.fonte_atualizado_em > u.fonte_atualizado_em)):
            ultimo[e.cidadao_id] = e
    for e in ultimo.values():
        e.mais_recente = True
    db.add_all(enderecos)
    db.flush()
    if progresso:
        progresso(min(feitos[0], total), total)
    return {"cidadaos": len(cidadaos), "vinculos": len(vinculos), "pendentes": len(pend), "tratados": min(feitos[0], total),
            "total_bronze": total}
