# Dívida em aberto — saldo líquido por linha (DP-P20 / DP-P4 / DP-P19 / DP-P1 / DP-P11)

Regra liberada pelo Leandro em 05/11/2026 (provisória), com o aceite fechado pelo Analista: **COMPO ∈ {D, G, P, B}**, `extinta IS NOT TRUE`,
`saldo = GREATEST(0, VR_TOTA_DIVI − Σ Vr_Pago da Divida_Conta_Corrente por CODI/SEQU/ANO)`, somado pela linha do débito e nunca pelo mestre.
O alvo agregado da C-DIV5a é **433,7 mi** (D 372,1 + G 59,6 + P 2,0 + B 0,0). Os testes usam só números sintéticos ZZTESTE.

## O que entra
- **Política e mapeamento:** `COMPO` em `Divida` e a tabela `Divida_Conta_Corrente`, só com `Data_Movimentacao, Sequencia, CODI, SEQU, ANO, Vr_Pago, Compo`, sem texto livre, com chave `CODI/SEQU/ANO/Sequencia`.
- **Migração 0046**, aditiva e idempotente: faz `ADD COLUMN IF NOT EXISTS` em `silver.dividas` para `sequ`, `compo`, `saldo_bruto`, `pago_conta_corrente`, `em_aberto` e `qualidade`, e cria o índice `em_aberto`. O downgrade não apaga nada.
- **Prata** (`silver/tributario.py`): `situacao_divida()` calcula o saldo líquido com piso 0, e `em_aberto` vale só para D/G/P/B. Um Q com valor e sem pagamento recebe `qualidade = quitado_sem_baixa` (DP-P19) e fica fora do KPI. `saldo` agora guarda o líquido, e o bruto fica em `saldo_bruto`.
- **Ouro, KPI e API:** `cubo_divida`, os KPIs `divida_ativa`, `devedores`, `protestadas`, `trib_divida_ano` e as faixas, `cruzamentos` (fornecedor com dívida e concentração), `vinculos._divida` e o total `divida_ativa` da ficha do cidadão passam a filtrar por `em_aberto IS TRUE`.
- **DP-P4 e DP-P1:** os mestres N/M/W e o original parcelado ficam fora. Os membros D/G contam uma vez cada, e um parcelamento conta só pelas parcelas P/B em aberto. No teste, 10 parcelas de 50 com 3 pagas somam **350**.
- **DP-P11:** o campo `inscricao` (data) da dívida na API virou **`inscrita_em`**. O único consumidor é `ImpostosCidadao.tsx`, atualizado neste PR junto com os testes. A `inscricao` do imóvel, que é a inscrição imobiliária, não muda.
- **Origem legível**, incluída a pedido do Tech Lead: a API devolve `origem = "IPTU — imóvel {inscrição}"` quando o CODI é um imóvel em `silver.imoveis`, e `natureza_nome` vem de `ref.divida_natureza`. A tela mostra os dois.
- Linhas fora do saldo aparecem como "Sem saldo em aberto", e as extintas como "Extinta".

## Fora deste PR / atenção
- **R/S/X/T** continuam fora até a decisão do Leandro. Incluí-los é só mudar `COMPO_ABERTO`.
- **Recarga necessária:** até a próxima carga DIVIDA trazer `COMPO` e a `Divida_Conta_Corrente`, as linhas antigas ficam com `compo` nulo e **não** entram no saldo, então o KPI fica em 0 até a recarga. Isso foi intencional, para não voltar ao bruto.
- **DP-P10** (carnê de parcelamento também em `silver.lancamentos`): não há ligação mapeada entre `Financeiro.Nr_Carne` e a dívida, então fica para um follow-up com o DBA.
- Não houve merge, deploy nem escrita em produção.

## Evidência
- `ruff check .`: `ruff.txt`
- pytest completo em Postgres local `sigpmf_divida_liquido_test`: `pytest.txt`
- frontend (vitest, tsc e lint): `frontend.txt`
- Testes novos: `backend/tests/test_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_divida_saldo_liquido.py`
