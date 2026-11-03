import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { PainelFinancas } from '../paineis/Financas'

const p = (dim: string, valor: number, periodo = '') => ({ periodo, dim, valor })
const DISP = { anos: [2026, 2025], ultimo_mes: { '2026': 8, '2025': 12 }, publicado_tce: { '2026': 'Wed, 23 Sep 2026 12:02:03 GMT', '2025': null },
  orgaos: ['CÂMARA MUNICIPAL DE FRANCA', 'PREFEITURA MUNICIPAL DE FRANCA'], funcoes: ['EDUCAÇÃO', 'SAÚDE'], fontes: ['TESOURO'], carregado_em: '2026-11-03T21:00:00Z' }
const TOT = { empenhado: 1296.1e6, liquidado: 940.8e6, pago: 875.6e6, receita: 1091.6e6, resultado: 150.8e6, sem_licitacao_pct: 13.9, pessoas_fisicas: 19.2e6, restos_pagos: 121.8e6 }
function resp(filtro: Record<string, unknown>) {
  const saude = filtro.funcao === 'SAÚDE'
  return {
    disponivel: DISP,
    filtro: { ano: 2026, mes_ate: 8, orgao: null, funcao: null, fonte: null, ...filtro },
    atual: saude ? { ...TOT, empenhado: 337.4e6, receita: null, resultado: null, restos_pagos: null } : TOT,
    anterior: { ...TOT, receita: 1000e6 },
    mensal: { receita: [p('', 217.4e6, '2026-01'), p('', 124.1e6, '2026-02')], liquidado: [p('', 90e6, '2026-01'), p('', 110e6, '2026-02')], empenhado: [], pago: [] },
    por_funcao: [p('EDUCAÇÃO', 362.3e6), p('SAÚDE', 259.4e6)], por_natureza: [p('Pessoal e encargos', 417.7e6)], por_fonte: [p('TESOURO', 700e6)],
    por_modalidade: [p('PREGÃO ELETRÔNICO', 167.1e6)], por_orgao: [p('PREFEITURA MUNICIPAL DE FRANCA', 863.2e6)],
    credores_pj: [p('MUNICIPIO DE FRANCA', 291e6)], receita_origem: [p('Transferências Correntes', 580.8e6)], impostos: [p('IPTU', 158.4e6)], transferencias: [p('Cota-Parte do ICMS', 142.3e6)],
  }
}
let urls: string[] = []
function mock(f: (u: string) => [number, unknown]) {
  urls = []
  vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); const [s, b] = f(u); return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } }) }))
}
const montar = () => render(<MemoryRouter><AuthProvider><PainelFinancas /></AuthProvider></MemoryRouter>)
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('painel Finanças (AUDESP)', () => {
  it('números do topo com comparação, aviso do atraso do TCE e acessibilidade', async () => {
    mock((u) => [200, resp(Object.fromEntries(new URL(u, 'http://x').searchParams))])
    const { container } = montar()
    expect(await screen.findByText('R$ 1,09 bi')).toBeVisible()  // receita
    const kpis = within(container.querySelector('#t-fin-kpis') as HTMLElement)
    expect(kpis.getByText('▲ 9,2% sobre jan–ago/2025')).toBeVisible()
    expect(kpis.getByText('R$ 1,3 bi')).toBeVisible()  // empenhada
    expect(screen.getByText(/Dados do TCE até agosto de 2026, publicados em 23\/09\/2026/)).toBeVisible()
    expect(screen.getByRole('list', { name: 'Maiores credores pessoa jurídica' })).toHaveTextContent('MUNICIPIO DE FRANCA')
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })

  it('clicar na função filtra a página; receita some e Limpar volta', async () => {
    mock((u) => [200, resp(Object.fromEntries(new URL(u, 'http://x').searchParams))])
    const { container } = montar()
    const lista = await screen.findByRole('list', { name: 'Despesa liquidada por função' })
    await userEvent.click(within(lista).getByRole('button', { name: /SAÚDE/ }))
    expect(await within(container.querySelector('#t-fin-kpis') as HTMLElement).findByText('R$ 337,4 mi')).toBeVisible()
    expect(urls.at(-1)).toContain('funcao=SA%C3%9ADE')
    expect(screen.queryByText('Receita arrecadada')).toBeNull()
    expect(screen.getByRole('region', { name: 'Despesa liquidada por mês' })).toBeVisible()  // sem a receita da cidade toda
    expect(screen.queryByRole('region', { name: 'Receita × despesa liquidada por mês' })).toBeNull()
    expect(screen.getByText(/a receita e os restos a pagar não aparecem/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Limpar filtros' }))
    expect(await screen.findByText('Receita arrecadada')).toBeVisible()
  })

  it('trocar o ano pede o ano novo; sem dados aponta para Conectores; erro tem tentar de novo', async () => {
    mock((u) => [200, resp(Object.fromEntries(new URL(u, 'http://x').searchParams))])
    const { unmount } = montar()
    await userEvent.selectOptions(await screen.findByLabelText('Ano'), '2025')
    expect(urls.at(-1)).toContain('ano=2025')
    unmount()
    mock(() => [200, { disponivel: { ...DISP, anos: [] }, filtro: null, atual: null, anterior: null }])
    const { unmount: u2 } = montar()
    expect(await screen.findByRole('link', { name: 'Conectores → AUDESP (TCE-SP)' })).toHaveAttribute('href', '/conectores#fonte-audesp')
    u2()
    mock(() => [500, { detail: 'x' }])
    montar()
    expect(await screen.findByRole('button', { name: 'Tentar novamente' })).toBeVisible()
  })
})
