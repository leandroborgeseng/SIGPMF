"""IEG-M (TCE-SP): importação do arquivo anual e consulta do painel. Sem pessoa.

O painel público do TCE (painel.tce.sp.gov.br) só responde com o login anônimo embutido na própria página; o SIGPMF
não automatiza esse login. O índice muda uma vez por ano, então a carga é por arquivo (CSV), enviado pelo admin na
tela Conectores:

    ano_base;ano_apuracao;indice;abrangencia;faixa;municipios
    2024;2025;i-Educ;3516200;B;1        <- Franca (código IBGE), municipios = 1
    2024;2025;i-Educ;SP;B;244           <- estado: quantos municípios ficaram na faixa

Cada arquivo substitui os anos que ele traz (por abrangência). Arquivo inválido não grava nada.
"""
import csv
import io
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.controle import Execucao
from app.models.iegm import FaixaIegm

TIPO = "carga_iegm"
MUNICIPIO = "3516200"  # Franca (IBGE)
COLUNAS = ("ano_base", "ano_apuracao", "indice", "abrangencia", "faixa", "municipios")
FAIXAS = ("A", "B+", "B", "C+", "C")
NOME_FAIXA = {"A": "Altamente efetiva", "B+": "Muito efetiva", "B": "Efetiva", "C+": "Em fase de adequação",
              "C": "Baixo nível de adequação"}
# índice -> (nome, área responsável na prefeitura, o que o TCE mede)
INDICES = {
    "IEG-M": ("IEG-M geral", "Prefeitura (todas as áreas)", "Média dos sete índices temáticos"),
    "i-Plan": ("Planejamento", "Planejamento", "Consistência entre o planejado e o executado; metas × recursos"),
    "i-Fiscal": ("Gestão fiscal", "Fazenda / Finanças", "Execução financeira e orçamentária e respeito à LRF"),
    "i-Educ": ("Educação", "Educação", "Educação infantil e ensino fundamental, com foco em infraestrutura escolar"),
    "i-Saude": ("Saúde", "Saúde", "Atenção básica, saúde da família, conselho de saúde, tratamentos e vacinação"),
    "i-Amb": ("Meio ambiente", "Meio Ambiente", "Resíduos sólidos, educação ambiental e estrutura dos conselhos"),
    "i-Cidade": ("Proteção dos cidadãos", "Defesa Civil", "Planejamento para acidentes e desastres naturais"),
    "i-Gov TI": ("Governança de TI", "Tecnologia da Informação", "Capacitação, transparência e segurança da informação"),
}


class ArquivoInvalido(ValueError):
    """Arquivo fora do formato do IEG-M. Mensagem segura para a tela (cita a linha, nunca o conteúdo inteiro)."""


def le_csv(conteudo: bytes) -> list[dict]:
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = conteudo.decode("iso-8859-1")
    leitor = csv.reader(io.StringIO(texto), delimiter=";")
    cab = tuple(c.strip() for c in next(leitor, []))
    if cab != COLUNAS:
        raise ArquivoInvalido(f"Cabeçalho esperado: {';'.join(COLUNAS)}.")
    linhas, vistos = [], set()
    for n, v in enumerate(leitor, start=2):
        if not v or all(not x.strip() for x in v):
            continue
        if len(v) != len(COLUNAS):
            raise ArquivoInvalido(f"Linha {n}: {len(v)} colunas (esperado {len(COLUNAS)}).")
        r = dict(zip(COLUNAS, (x.strip() for x in v), strict=True))
        if not (r["ano_base"].isdigit() and r["ano_apuracao"].isdigit() and r["municipios"].isdigit()):
            raise ArquivoInvalido(f"Linha {n}: ano e quantidade de municípios precisam ser números.")
        if r["indice"] not in INDICES:
            raise ArquivoInvalido(f"Linha {n}: índice {r['indice']!r} desconhecido (use {', '.join(INDICES)}).")
        if r["faixa"] not in FAIXAS:
            raise ArquivoInvalido(f"Linha {n}: faixa {r['faixa']!r} inválida (use A, B+, B, C+ ou C).")
        if not (r["abrangencia"] == "SP" or (r["abrangencia"].isdigit() and len(r["abrangencia"]) == 7)):
            raise ArquivoInvalido(f"Linha {n}: abrangência é o código IBGE do município (7 dígitos) ou SP.")
        chave = (r["ano_base"], r["indice"], r["abrangencia"], r["faixa"])
        if chave in vistos:
            raise ArquivoInvalido(f"Linha {n}: repete ano, índice, abrangência e faixa de uma linha anterior.")
        vistos.add(chave)
        linhas.append({"ano_base": int(r["ano_base"]), "ano_apuracao": int(r["ano_apuracao"]), "indice": r["indice"],
                       "abrangencia": r["abrangencia"], "faixa": r["faixa"], "municipios": int(r["municipios"])})
    if not linhas:
        raise ArquivoInvalido("O arquivo não tem nenhuma linha de dados.")
    return linhas


def importa(db: Session, usuario: str, conteudo: bytes) -> Execucao:
    """Valida o arquivo inteiro e troca os anos que ele traz (por abrangência), numa transação só."""
    ex = Execucao(tipo=TIPO, sistema="IEGM", status="em_andamento", usuario=usuario, modo="completa",
                  atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    try:
        linhas = le_csv(conteudo)
    except ArquivoInvalido as exc:
        return _fim(db, ex, "falha", str(exc), 0)
    anos = sorted({x["ano_base"] for x in linhas})
    for abr in {x["abrangencia"] for x in linhas}:
        anos_abr = {x["ano_base"] for x in linhas if x["abrangencia"] == abr}
        db.execute(delete(FaixaIegm).where(FaixaIegm.abrangencia == abr, FaixaIegm.ano_base.in_(anos_abr)))
    db.add_all(FaixaIegm(**x, execucao_id=ex.id) for x in linhas)
    return _fim(db, ex, "sucesso", f"IEG-M carregado: anos base {anos[0]} a {anos[-1]} ({len(linhas)} linhas).", len(linhas))


def _fim(db: Session, ex: Execucao, status: str, mensagem: str, n: int) -> Execucao:
    ex.status, ex.mensagem, ex.lidos, ex.novos = status, mensagem, n, n if status == "sucesso" else 0
    ex.finalizado_em = ex.atualizado_em = datetime.now(UTC)
    db.commit()
    return ex


def painel(db: Session, municipio: str = MUNICIPIO) -> dict:
    """Série do município (índice × ano base), distribuição do estado e posição do município no último ano."""
    mun = list(db.scalars(select(FaixaIegm).where(FaixaIegm.abrangencia == municipio)
                          .order_by(FaixaIegm.indice, FaixaIegm.ano_base)))
    if not mun:
        return {"anos": [], "indices": [], "ultimo": None, "faixas": NOME_FAIXA}
    est: dict[tuple[int, str], dict[str, int]] = {}
    for e in db.scalars(select(FaixaIegm).where(FaixaIegm.abrangencia == "SP")):
        est.setdefault((e.ano_base, e.indice), {})[e.faixa] = e.municipios
    anos = sorted({x.ano_base for x in mun})
    apuracao = {x.ano_base: x.ano_apuracao for x in mun}
    ultimo = anos[-1]
    indices = []
    for cod, (nome, area, descricao) in INDICES.items():
        serie = {x.ano_base: x.faixa for x in mun if x.indice == cod}
        if not serie:
            continue
        atual = serie.get(ultimo)
        anterior = serie.get(ultimo - 1)
        dist = est.get((ultimo, cod), {})
        total = sum(dist.values())
        pos = FAIXAS.index(atual) if atual else None
        melhor = sum(n for f, n in dist.items() if FAIXAS.index(f) < pos) if pos is not None and total else None
        igual = dist.get(atual, 0) if pos is not None and total else None
        indices.append({
            "codigo": cod, "nome": nome, "area": area, "descricao": descricao,
            "serie": [{"ano_base": a, "faixa": serie.get(a)} for a in anos],
            "atual": atual, "anterior": anterior,
            "tendencia": None if not (atual and anterior) else
            ("subiu" if FAIXAS.index(atual) < FAIXAS.index(anterior) else "caiu" if FAIXAS.index(atual) > FAIXAS.index(anterior)
             else "igual"),
            "estado_ultimo": {f: dist.get(f, 0) for f in FAIXAS}, "estado_total": total,
            # faixas empatam muito: "em faixa melhor" e "na mesma faixa" separados, em % dos municípios do estado
            "pct_melhor": round(100 * melhor / total, 1) if melhor is not None else None,
            "pct_mesma": round(100 * igual / total, 1) if igual is not None else None,
        })
    return {"anos": anos, "apuracao": {str(a): apuracao[a] for a in anos}, "ultimo": ultimo, "indices": indices,
            "faixas": NOME_FAIXA, "carregado_em": max(x.carregado_em for x in mun)}


def estado_carga(db: Session) -> dict:
    ult = db.scalar(select(Execucao).where(Execucao.tipo == TIPO).order_by(Execucao.id.desc()).limit(1))
    anos = sorted({a for a, in db.execute(select(FaixaIegm.ano_base).where(FaixaIegm.abrangencia == MUNICIPIO).distinct())})
    return {"anos": anos, "ultima_execucao": None if ult is None else {
        "status": ult.status, "mensagem": ult.mensagem, "finalizado_em": ult.finalizado_em}}
