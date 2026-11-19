import { render, screen, within } from '@testing-library/react'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ImpostosCidadao, { linhaImovel } from '../ImpostosCidadao'
import { salvaSessao } from '../api'

const resp = {
  cidadao_id: 'c1',
  totais: { imoveis: 1, em_aberto: 184.3, parcelas_em_aberto: 1, vencido: 184.3, parcelas_vencidas: 1, divida_ativa: 646.2, inscricoes_divida: 1, protestada: true },
  imoveis: [
    { codi: '01111010010100', inscricao: '0111101001010', numero: '550', complemento: 'Rua Sintetica', cd_bairro: 10, iptu_anual: 420.75, tipo_posse: 1, desde: '2015-04-30T00:00:00', ate: null, ativo: true },
  ],
  lancamentos: [
    { exercicio: 2026, tributo: '001', parcela: 1, codi: '01111010010100', vencimento: '2026-02-10', valor: 184.3, valor_pago: 184.3, pagamento: '2026-02-09', pago: true, situacao: 'pago' },
    { exercicio: 2026, tributo: '001', parcela: 2, codi: '01111010010100', vencimento: '2026-03-10', valor: 184.3, valor_pago: 0, pagamento: null, pago: false, situacao: 'vencido' },
  ],
  dividas: [{ codi: '01111010010100', sequ: '01', ano: 2024, natureza: 3, natureza_nome: 'IPTU', origem: 'IPTU — imóvel 0111101001010', inscrita_em: '2025-03-01', saldo: 646.2, saldo_bruto: 646.2, pago: null, situacao: 'D', em_aberto: true, valor_origem: 350, protestos: 1, extinta: false }],
}
const mock = (s: number, c: unknown) => vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } })))

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('Impostos e imóveis (TR7)', () => {
  it('mostra totais, imóvel, lançamentos com situação e dívida', async () => {
    mock(200, resp)
    const { container } = render(<ImpostosCidadao id="c1" />)
    expect(await screen.findByText('Posse atual')).toBeVisible()
    expect(screen.getAllByText(/184,30/).length).toBeGreaterThan(0)
    expect(screen.getByText(/protestada/)).toBeVisible()
    const tab = screen.getByRole('table', { name: 'Lançamentos' })
    expect(within(tab).getByText('Pago')).toBeVisible()
    expect(within(tab).getByText('Vencido')).toBeVisible()
    expect(within(screen.getByRole('list', { name: 'Dívida ativa' })).getByText(/646,20/)).toBeVisible()
    expect(screen.getByText(/inscrita em 01\/03\/2025/)).toBeVisible()
    expect(screen.getByText(/IPTU — imóvel 0111101001010/)).toBeVisible()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('DP-P11/DP-P20: usa inscrita_em e mostra linha fora do saldo sem valor', async () => {
    mock(200, { ...resp, dividas: [...resp.dividas, { codi: '01111010010100', ano: 2023, natureza: 3, natureza_nome: null,
      inscrita_em: '2024-03-01', saldo: 0, saldo_bruto: 90, pago: 90, situacao: 'Q', em_aberto: false, valor_origem: 90, protestos: 0, extinta: false }] })
    render(<ImpostosCidadao id="c1" />)
    const lista = await screen.findByRole('list', { name: 'Dívida ativa' })
    expect(within(lista).getByText(/inscrita em 01\/03\/2024/)).toBeVisible()
    expect(within(lista).getByText('Sem saldo em aberto')).toBeVisible()
    expect(within(lista).getAllByText(/IPTU/).length).toBeGreaterThan(0)
  })
  it('mostra as empresas do CPF (SIL) com a situação de débito', async () => {
    mock(200, { ...resp, empresas: [
      { ccm: 1234, nome: 'COMERCIO SINTETICO ME', documento: '***.456.789-**', possui_debito: true, endereco: 'Rua X', bairro: 'CENTRO' },
      { ccm: 5678, nome: 'SERVICOS SINTETICOS', documento: '***.456.789-**', possui_debito: false, endereco: null, bairro: null },
    ] })
    const { container } = render(<ImpostosCidadao id="c1" />)
    const lista = await screen.findByRole('list', { name: 'Empresas' })
    expect(within(lista).getByText('COMERCIO SINTETICO ME')).toBeVisible()
    expect(within(lista).getByText(/CCM 1234 · CENTRO/)).toBeVisible()
    expect(within(lista).getByText('Com débito')).toBeVisible()
    expect(within(lista).getByText('Sem débito')).toBeVisible()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('mostra o endereço na posse atual e na anterior, e mantém a linha sem endereço', () => {
    const base = { cd_bairro: null, iptu_anual: null, tipo_posse: 1 }
    const imoveis = [
      { ...base, codi: '05555560066901', inscricao: '05555560066901', numero: '00901', complemento: null, desde: '2024-06-26T00:00:00', ate: null, ativo: true, endereco: 'Rua Exemplo de Toledo, 266 - Vila Exemplo' },
      { ...base, codi: '06666670077012', inscricao: '06666670077012', numero: '00802', complemento: 'Fundos', desde: '2023-01-15T00:00:00', ate: null, ativo: true, endereco: null },
      { ...base, codi: '07777780088123', inscricao: '07777780088123', numero: '00703', complemento: null, desde: '2018-01-01T00:00:00', ate: '2021-08-25T00:00:00', ativo: false, endereco: 'Rua das Flores Ficticias, 42 - Jardim Exemplo' },
      { ...base, codi: '08888890099234', inscricao: '08888890099234', numero: '00604', complemento: null, desde: '2010-05-02T00:00:00', ate: '2019-03-01T00:00:00', ativo: false, endereco: '   ' },
    ]
    const comAtual = linhaImovel(imoveis[0])
    const semAtual = linhaImovel(imoveis[1])
    const comAnterior = linhaImovel(imoveis[2])
    const semAnterior = linhaImovel(imoveis[3])
    expect(`${comAtual.titulo}${comAtual.detalhe}`).toBe('Imóvel · Inscrição 05555560066901 · Rua Exemplo de Toledo, 266 - Vila Exemplo · desde 26/06/2024')
    expect(`${semAtual.titulo}${semAtual.detalhe}`).toBe('Fundos, 00802 · Inscrição 06666670077012 · desde 15/01/2023')
    expect(`${comAnterior.titulo}${comAnterior.detalhe}`).toBe('Imóvel · Inscrição 07777780088123 · Rua das Flores Ficticias, 42 - Jardim Exemplo · até 25/08/2021')
    expect(`${semAnterior.titulo}${semAnterior.detalhe}`).toBe('Imóvel, 00604 · Inscrição 08888890099234 · até 01/03/2019')
    expect(comAtual.detalhe).not.toContain('00901')
    expect(comAnterior.detalhe).not.toContain('00703')
    render(<ImpostosCidadao id="c1" dados={{ ...resp, totais: { ...resp.totais, imoveis: 2 }, imoveis }} />)
    const lista = screen.getByRole('list', { name: 'Imóveis' })
    expect(lista).toHaveTextContent('Imóvel · Inscrição 05555560066901 · Rua Exemplo de Toledo, 266 - Vila Exemplo · desde 26/06/2024')
    expect(lista).toHaveTextContent('Fundos, 00802 · Inscrição 06666670077012 · desde 15/01/2023')
    expect(lista).toHaveTextContent('Imóvel · Inscrição 07777780088123 · Rua das Flores Ficticias, 42 - Jardim Exemplo · até 25/08/2021')
    expect(lista).toHaveTextContent('Imóvel, 00604 · Inscrição 08888890099234 · até 01/03/2019')
    expect(within(lista).getAllByText('Posse atual')).toHaveLength(2)
    expect(within(lista).getAllByText('Posse anterior')).toHaveLength(2)
    expect(lista).not.toHaveTextContent('00901')
    expect(lista).not.toHaveTextContent('00703')
  })
  it('sem dados e com erro não quebra', async () => {
    mock(200, { ...resp, imoveis: [], lancamentos: [], dividas: [] })
    const { unmount } = render(<ImpostosCidadao id="c1" />)
    expect(await screen.findByText('Nenhum imóvel ligado a este cidadão.')).toBeVisible()
    expect(screen.getByText('Nenhuma dívida ativa para este cidadão.')).toBeVisible()
    expect(screen.getByText('Nenhum imposto ou taxa lançado para este cidadão.')).toBeVisible()
    unmount()
    mock(500, { detail: 'x' })
    render(<ImpostosCidadao id="c1" />)
    expect((await screen.findAllByText(/Não foi possível ler esta área agora/)).length).toBe(4)
  })
})
