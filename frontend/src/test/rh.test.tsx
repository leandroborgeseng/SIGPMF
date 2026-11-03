import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import CargaRh from '../CargaRh'
import { PainelRh } from '../paineis/Rh'

const p = (dim: string, valor: number, periodo = '') => ({ periodo, dim, valor })
const DISP = { meses: [{ ano: 2025, mes: 9 }, { ano: 2025, mes: 13 }, { ano: 2026, mes: 8 }, { ano: 2026, mes: 9 }], secretarias: ['Educação', 'Saúde'], carregado_em: '2026-11-03T22:00:00Z' }
const TOT = { servidores: 5650, suprimido: false, bruto: 30e6, descontos: 8e6, liquido: 22e6, media: 5310, mediana: 4200, afastados: 160, admissoes: 12, desligamentos: 20 }
function resp(q: URLSearchParams) {
  const sec = q.get('secretaria')
  const filtro = { ano: Number(q.get('ano') ?? 2026), mes: Number(q.get('mes') ?? 9), secretaria: sec, grupo: q.get('grupo') }
  return { disponivel: DISP, filtro, atual: sec === 'Pequena' ? { servidores: 3, suprimido: true } : sec ? { ...TOT, servidores: 2000, bruto: 12e6 } : TOT,
    anterior: { ...TOT, servidores: 5000 },
    mensal: { bruto: [p('', 29e6, '2026-08'), p('', 30e6, '2026-09')], servidores: [p('', 5600, '2026-08'), p('', 5650, '2026-09')] },
    anual: { bruto: [p('', 300e6, '2025')], servidores: [p('', 5400, '2025')], admissoes: [p('', 300, '2025')], desligamentos: [p('', 200, '2025')] },
    por_secretaria_bruto: [p('Educação', 12e6), p('Saúde', 10e6)], por_secretaria_servidores: [p('Educação', 2000), p('Saúde', 1500)],
    por_cargo_servidores: [p('PROFESSOR', 900)], por_cargo_media: [p('MEDICO', 15000)], por_situacao: [p('Ativo', 5400)],
    por_faixa: [p('até R$ 3 mil', 900), p('R$ 3 a 5 mil', 2000)], por_tempo: [p('até 5 anos', 1000)] }
}
const urls: string[] = []
function mock(f: (u: string) => [number, unknown]) {
  urls.length = 0
  vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); const [s, b] = f(u); return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } }) }))
}
const painel = () => mock((u) => [200, resp(new URL(u, 'http://x').searchParams)])
const montar = () => render(<MemoryRouter><AuthProvider><PainelRh /></AuthProvider></MemoryRouter>)
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('painel de RH', () => {
  it('números do topo com variação e acessibilidade', async () => {
    painel()
    const { container } = montar()
    const kpis = within(await screen.findByText('Pessoas pagas no mês').then(() => container.querySelector('#t-rh-kpis') as HTMLElement))
    expect(kpis.getByText('5.650')).toBeVisible()
    expect(kpis.getByText('▲ 13% sobre set/2025')).toBeVisible()
    expect(kpis.getByText('R$ 30 mi')).toBeVisible()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('clicar na secretaria filtra e trocar de ano mantém o mês', async () => {
    painel()
    montar()
    const lista = await screen.findByRole('list', { name: 'Pessoas por secretaria' })
    await userEvent.click(within(lista).getByRole('button', { name: /Saúde/ }))
    expect(await screen.findByRole('button', { name: 'Limpar filtros' })).toBeVisible()
    expect(urls.at(-1)).toContain('secretaria=Sa%C3%BAde')
    await userEvent.selectOptions(screen.getByLabelText('Ano'), '2025')
    expect(urls.at(-1)).toContain('ano=2025')
    expect(urls.at(-1)).toContain('mes=9')
  })
  it('recorte com menos de 5 pessoas não mostra números', async () => {
    mock(() => [200, { ...resp(new URLSearchParams('secretaria=Pequena')) }])
    montar()
    expect(await screen.findByText(/menos de 5 pessoas e não é mostrado/)).toBeVisible()
    expect(screen.queryByText('Folha bruta', { selector: '.cartao-rotulo' })).toBeNull()
  })
  it('sem dados aponta para Conectores', async () => {
    mock(() => [200, { disponivel: { meses: [], secretarias: [], carregado_em: null }, filtro: null, atual: null }])
    montar()
    expect(await screen.findByRole('link', { name: 'Conectores → RH (Portal da Transparência)' })).toHaveAttribute('href', '/conectores#fonte-rh')
  })
})

describe('card de RH em Conectores', () => {
  const EST = { meses: 180, linhas: 950000, primeiro: [2012, 6], ultimo: [2026, 9], em_andamento: null, ultima_execucao: { id: 1, status: 'sucesso', mensagem: '180 meses carregados.', mes_atual: null, lidos: 1, iniciado_em: '2026-11-03T20:00:00Z', finalizado_em: '2026-11-03T20:10:00Z' } }
  it('mostra o período; só admin carrega; carga em andamento bloqueia o botão', async () => {
    mock((u) => u.endsWith('/rh/carregar') ? [202, { ...EST, em_andamento: { ...EST.ultima_execucao, status: 'em_andamento', mes_atual: '03/2015' } }] : [200, EST])
    const { unmount } = render(<CargaRh ehAdmin={false} />)
    expect(await screen.findByText(/Carregado de 06\/2012 a 09\/2026: 180 folhas e 950\.000 pagamentos/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Carregar agora' })).toBeNull()
    unmount()
    render(<CargaRh ehAdmin />)
    await userEvent.click(await screen.findByRole('button', { name: 'Carregar agora' }))
    expect(await screen.findByText('Carregando · 03/2015')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Carregar agora' })).toBeDisabled()
  })
})
