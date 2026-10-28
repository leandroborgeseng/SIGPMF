import { render, screen, within } from '@testing-library/react'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { PainelFazenda, PainelGeral, PainelSaude } from '../paineis/Paineis'
import { mesCurto, reais } from '../paineis/graficos'

const k = (dim: string, valor: number) => ({ periodo: '', dim, valor })
const SERIES = {
  kpi: [k('cidadaos', 541880), k('imoveis', 212000), k('atendimentos_12m', 96450), k('vacinas_24m', 61200), k('divida_ativa', 1.08e9),
    k('devedores', 71240), k('protestadas', 1200), k('lancado_ano', 498e6), k('arrecadado_ano', 412e6), k('inadimplencia_pct', 18.4),
    k('espera_media_dias', 47)],
  trib_lancado_mes: [{ periodo: '2026-01', dim: '', valor: 86e6 }, { periodo: '2026-02', dim: '', valor: 46e6 }],
  trib_pago_mes: [{ periodo: '2026-01', dim: '', valor: 74e6 }, { periodo: '2026-02', dim: '', valor: 39e6 }],
  saude_atend_mes: [{ periodo: '2026-09', dim: '', valor: 9000 }, { periodo: '2026-10', dim: '', valor: 9645 }],
  saude_atend_unidade: [{ periodo: '', dim: 'UBS Centro', valor: 6100 }, { periodo: '', dim: 'PS Norte', valor: 9800 }],
  saude_espera_especialidade: [{ periodo: '', dim: 'Ortopedia', valor: 132 }, { periodo: '', dim: 'Cardiologia', valor: 61 }],
  saude_agend_especialidade: [], saude_vacina_mes: [], saude_vacina_tipo: [{ periodo: '', dim: 'Influenza', valor: 61200 }],
  trib_inadimplencia_tributo: [{ periodo: '', dim: 'IPTU', valor: 21.3 }],
  trib_divida_ano: [{ periodo: '', dim: '2024', valor: 300e6 }],
  trib_devedores_faixa: [{ periodo: '', dim: 'até R$ 1 mil', valor: 50000 }],
  trib_saldo_faixa: [], trib_maiores_saldos: [{ periodo: '', dim: '1º', valor: 18.4e6 }, { periodo: '', dim: '2º', valor: 9.8e6 }],
}
const mock = (s: number, body: unknown) => vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status: s, headers: { 'Content-Type': 'application/json' } })))
const montar = (el: React.ReactNode) => render(<MemoryRouter><AuthProvider>{el}</AuthProvider></MemoryRouter>)

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('painéis de gestão (onda 4)', () => {
  it('visão geral: KPIs formatados, colunas mensais e atalhos', async () => {
    mock(200, { atualizado_em: '2026-10-28T06:00:00Z', series: SERIES })
    const { container } = montar(<PainelGeral />)
    expect(await screen.findByText('R$ 412 mi')).toBeVisible()
    expect(screen.getByText('18,4%')).toBeVisible()
    expect(screen.getByText('R$ 1,08 bi')).toBeVisible()
    expect(screen.getByText('47 dias')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Painel da Saúde' })).toHaveAttribute('href', '/painel/saude')
    expect(screen.getByRole('table', { name: /Lançado e Arrecadado por mês/ })).toBeInTheDocument()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('saúde: barras ordenadas pelo maior valor e vazio amigável', async () => {
    mock(200, { atualizado_em: null, series: SERIES })
    montar(<PainelSaude />)
    const lista = await screen.findByRole('list', { name: 'Atendimentos por unidade' })
    expect(within(lista).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['PS Norte9.800', 'UBS Centro6.100'])
    expect(screen.getAllByText(/Sem dados ainda/).length).toBeGreaterThan(0)  // agendamentos vazios
  })
  it('fazenda: onde está o dinheiro e erro com tentar de novo', async () => {
    mock(200, { atualizado_em: null, series: SERIES })
    const { unmount } = montar(<PainelFazenda />)
    expect(await screen.findByText(/Os 10 maiores saldos somam/)).toBeVisible()
    unmount()
    mock(500, { detail: 'x' })
    montar(<PainelFazenda />)
    expect(await screen.findByRole('button', { name: 'Tentar novamente' })).toBeVisible()
  })
  it('formatos', () => {
    expect(mesCurto('2026-03')).toBe('mar/26')
    expect(reais(1.08e9)).toBe('R$ 1,08 bi')
    expect(reais(null)).toBe('—')
  })
})
