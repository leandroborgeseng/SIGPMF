import { render, screen, within } from '@testing-library/react'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ImpostosCidadao from '../ImpostosCidadao'
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
  dividas: [{ codi: '01111010010100', ano: 2024, natureza: 3, inscricao: '2025-03-01', saldo: 646.2, valor_origem: 350, protestos: 1, extinta: false }],
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
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('sem dados e com erro não quebra', async () => {
    mock(200, { ...resp, imoveis: [], lancamentos: [], dividas: [] })
    const { unmount } = render(<ImpostosCidadao id="c1" />)
    expect(await screen.findByText(/Nenhum imóvel, lançamento ou dívida/)).toBeVisible()
    unmount()
    mock(500, { detail: 'x' })
    render(<ImpostosCidadao id="c1" />)
    expect(await screen.findByText('Sem leitura dos dados tributários.')).toBeVisible()
  })
})
