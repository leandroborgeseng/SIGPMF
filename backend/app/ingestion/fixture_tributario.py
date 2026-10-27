"""Fixtures SINTÉTICAS com a estrutura (tabelas/colunas) de IPTU, Divida e Financeiro (onda 2). Nenhum valor real.

T1  imóvel 01111010010100: possuidor PF ativo com o CPF de SA1 (Saúde) -> ligado ao cidadão de SA1;
    possuidor anterior com baixa (CPF válido sem cidadão) fica no histórico, inativo
T2  imóvel 02222020020200: possuidor PJ (CNPJ) -> sem cidadão
T3  imóvel 03333030030300: possuidor PF com CPF válido que não é cidadão -> sem cidadão
L1/L2  lançamentos 2026 do T1 (um pago, um em aberto) -> cidadão de SA1
L3  lançamento cujo Contribuinte não é imóvel (inscrição do ISS) -> sem codi nem cidadão
D1  dívida aberta do T1 com protesto -> cidadão de SA1;  D2 dívida extinta do T2
Colunas sensíveis presentes na origem (NOME, OBS) nunca chegam à bronze (política).
"""
from app.identity.cpf import digitos_verificadores


def _cpf(base: str) -> str:
    return base + digitos_verificadores(base)


def gerar(cpf_sa1: str) -> dict[tuple[str, str], list[dict]]:
    outro, sem_cidadao = _cpf("123456780"), _cpf("987654320")

    def imovel(codi, insc, nr, bairro, terr, cons, data):
        return {"CODI": codi, "Insc_Imob": insc, "NR_CASE": nr, "COMPLE": "Rua Sintetica", "Cd_Bai": bairro,
                "Quadra": "7", "Lote": "1", "STER": 180.0, "SITU": 1, "Iptu_Terr_Liqu": terr, "Iptu_Cons_Liqu": cons,
                "Data_Alte": data, "NOME": "Nome que nunca entra", "OBS": "texto livre"}

    def lanc(ex, parcela, contrib, venc, valor, pago, dt_pag):
        return {"Exercicio": ex, "Tributo": "001", "Nr_Carne": 5000 + parcela, "Sequencia": "00", "Referencia": "0000",
                "Renovacao": 0, "Parcela": parcela, "Duplicidade": 0, "Contribuinte": contrib, "Receita": "001",
                "Data_Vencimento": venc, "Valor": valor, "Data_Pagamento": dt_pag, "Valor_Pago": pago,
                "Status": "PAGO" if dt_pag else None, "Data_Ultima_Alteracao_Registro": "2026-10-01 08:00:00",
                "Documento": "nunca entra"}

    def div(codi, sequ, ano, saldo, extinta, protestos):
        return {"CODI": codi, "SEQU": sequ, "ANO": ano, "Nr_Carne": 900 + ano % 100, "Cod_Nat": 3,
                "DT_INSCR": f"{ano + 1}-03-01 00:00:00", "DT_VENC": f"{ano}-12-20 00:00:00", "VR_ORIGEM": 350.0,
                "VR_TOTA_DIVI": saldo, "VR_PAGO_TOTAL": 0.0, "STATUS": "A", "SITUACAO": 1, "DT_PAGAMENTO": None,
                "Data_Extincao": "2025-01-10 00:00:00" if extinta else None, "QTDE_PROTESTO": protestos,
                "NR_PROC": None, "DT_CALCULO": "2026-10-01 00:00:00", "OBS": "texto livre"}

    return {
        ("IPTU", "Cadastro"): [
            imovel("01111010010100", "0111101001010", "550", 10, 120.5, 300.25, "2026-09-01 00:00:00"),
            imovel("02222020020200", "0222202002020", "12", 11, 80.0, None, "2026-09-02 00:00:00"),
            imovel("03333030030300", "0333303003030", "77", 12, 60.0, 40.0, "2026-09-03 00:00:00"),
        ],
        ("IPTU", "Cadastro_Possuidor"): [
            {"CODI": "01111010010100", "CPF_CNPJ": cpf_sa1, "Data_Inclusao": "2015-04-30 00:00:00", "Data_Baixa": None, "Tipo_Posse": 1},
            {"CODI": "01111010010100", "CPF_CNPJ": outro, "Data_Inclusao": "2001-01-01 00:00:00",
             "Data_Baixa": "2015-04-29 00:00:00", "Tipo_Posse": 1},
            {"CODI": "02222020020200", "CPF_CNPJ": "11222333000181", "Data_Inclusao": "2019-06-01 00:00:00", "Data_Baixa": None, "Tipo_Posse": 1},
            {"CODI": "03333030030300", "CPF_CNPJ": sem_cidadao, "Data_Inclusao": "2020-02-02 00:00:00", "Data_Baixa": None, "Tipo_Posse": 2},
        ],
        ("FINANCEIRO", "Financeiro_Exercicio"): [
            lanc(2026, 1, "01111010010100", "2026-02-10 00:00:00", 184.3, 184.3, "2026-02-09 00:00:00"),
            lanc(2026, 2, "01111010010100", "2026-03-10 00:00:00", 184.3, 0.0, None),
            lanc(2026, 3, "0010100020", "2026-03-10 00:00:00", 55.0, 0.0, None),
        ],
        ("DIVIDA", "Divida"): [
            div("01111010010100", "00", 2024, 646.2, False, 1),
            div("02222020020200", "00", 2022, 0.0, True, 0),
        ],
    }
