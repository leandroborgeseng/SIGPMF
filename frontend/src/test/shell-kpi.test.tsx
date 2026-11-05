import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import axe from 'axe-core'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import PainelFolhaModelo from '../paineis/shell/FolhaModelo'
import { calcula, valorFmt, variacaoTexto } from '../paineis/shell/formato'
import type { KpiDado, RespPainel } from '../paineis/shell/tipos'

const meses = ['2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10']
const serie = (base: number) => meses.map((c, i) => ({ competencia: c, rotulo: c, valor: c === '2026-06' ? null : base + i, sem_carga: c === '2026-06' }))
const kpi = (chave: string, formato: KpiDado['formato'], calculo: KpiDado['calculo'], extra: Partial<KpiDado> = {}): KpiDado => ({
  chave, rotulo: `ZZ ${chave}`, formato, calculo, alta_e_boa: false, ajuda: 'fórmula', valor: 100, suprimido: false,
  mom: { referencia: 'set/2026', delta: 2, pct: 2.0, sentido: 'alta' }, yoy: { referencia: 'out/2025', delta: -3, pct: -2.9, sentido: 'queda' },
  serie: serie(90), ...extra,
})
const OK: RespPainel = {
  painel: 'folha-modelo', titulo: 'Folha de pagamento (modelo)', exemplo: true, k: 5, niveis: ['Secretaria', 'Órgão/lotação', 'Rubrica'], estado: 'ok',
  fonte: { sistema: 'EXEMPLO_FOLHA', rotulo: 'Dados sintéticos', execucao_id: 7, carga_em: '2026-11-04T12:30:00+00:00' },
  filtros: { competencia: '2026-10', secretaria: null, caminho: [] }, secretarias: ['EDUCACAO', 'SAUDE'],
  competencias: [{ valor: '2026-09', rotulo: 'set/2026' }, { valor: '2026-10', rotulo: 'out/2026' }],
  kpis: [kpi('folha_bruta', 'brl', 'm1', { valor: 1.5e6 }), kpi('pct_he', 'pct', 'razao_m2_m1', { valor: 7.5, mom: { referencia: 'set/2026', delta: null, pct: null, sentido: null } })],
  drill: {
    nivel: 'Secretaria', indice: 0, medida: { chave: 'pct_he', rotulo: 'ZZ pct_he', formato: 'pct' }, ocultos: 1, suprimido: false,
    migalhas: [{ rotulo: 'Prefeitura', secretaria: null, caminho: [] }],
    itens: [{ rotulo: 'Saúde', chave: 'SAUDE', m1: 1000, m2: 100, n: 50, valor: 10, outros: false, abre: true },
      { rotulo: 'Outros (grupos pequenos)', chave: 'Outros (grupos pequenos)', m1: 500, m2: 25, n: 6, valor: 5, outros: true, abre: false }],
    total: { m1: 1500, m2: 125, n: 56, valor: 8.33 },
  },
}
const resp = (s: number, body: unknown) => new Response(JSON.stringify(body), { status: s, headers: { 'Content-Type': 'application/json' } })
let fila: Array<() => Response>
function Local() { const l = useLocation(); return <output data-testid="url">{l.search}</output> }
const montar = (url = '/painel/modelo-folha') => render(
  <MemoryRouter initialEntries={[url]}><AuthProvider><Routes><Route path="/painel/modelo-folha" element={<><PainelFolhaModelo /><Local /></>} /></Routes></AuthProvider></MemoryRouter>)

beforeEach(() => {
  sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' })
  fila = []
  vi.stubGlobal('fetch', vi.fn(async () => (fila.shift() ?? (() => resp(200, OK)))()))
})
afterEach(() => vi.unstubAllGlobals())

describe('formatos e variação (P01/P02/UXD3/UXD8)', () => {
  it('seta + sinal + texto, nunca só cor; "—" sem comparação', () => {
    expect(variacaoTexto({ referencia: 'set/2026', delta: 2, pct: 2, sentido: 'alta' }, 'brl').texto).toBe('+2%')
    expect(variacaoTexto({ referencia: 'set/2026', delta: -0.4, pct: null, sentido: 'queda' }, 'pct')).toMatchObject({ seta: '▼', texto: '−0,4 p.p.' })
    expect(variacaoTexto({ referencia: 'jun/2026', delta: null, pct: null, sentido: null }, 'pct').texto).toBe('—')
    expect(valorFmt(1234567, 'brl')).toBe('R$ 1,2 mi')
    expect(valorFmt(12.44, 'pct')).toBe('12,4%')
    expect(calcula('razao_m2_m1', 1000, 75, 3)).toBe(7.5)
  })
})

describe('PainelShell (P01–P07, UXD1–UXD7)', () => {
  it('KPIs com valor, duas variações, sparkline com lacuna e selo de fonte/data', async () => {
    const { container } = montar()
    const cartao = await screen.findByRole('article', { name: 'ZZ folha_bruta' })
    expect(within(cartao).getByText('R$ 1,5 mi')).toBeInTheDocument()
    expect(within(cartao).getByLabelText(/vs\. set\/2026: alta de \+2%/)).toBeInTheDocument()
    expect(within(cartao).getByLabelText(/vs\. out\/2025: queda de −2,9%/)).toBeInTheDocument()
    expect(cartao.querySelector('[data-lacuna="2026-06"]')).not.toBeNull()
    const pct = screen.getByRole('article', { name: 'ZZ pct_he' })
    expect(within(pct).getByLabelText(/sem comparação com set\/2026/)).toHaveTextContent('—')
    expect(screen.getByTestId('selo-fonte')).toHaveTextContent('Fonte: Dados sintéticos · carga de 04/11/2026')
    expect(screen.getByText('EXEMPLO')).toBeInTheDocument()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })

  it('drill-down: soma fecha, "Outros" entra; abrir põe na URL e a migalha volta preservando a competência', async () => {
    montar('/painel/modelo-folha?competencia=2026-10')
    expect(await screen.findByTestId('soma-total')).toHaveTextContent('8,3%')
    expect(screen.getByText('Outros (grupos pequenos)')).toBeInTheDocument()
    fila.push(() => resp(200, { ...OK, filtros: { ...OK.filtros, secretaria: 'SAUDE' }, drill: { ...OK.drill!, indice: 1, nivel: 'Órgão/lotação',
      migalhas: [{ rotulo: 'Prefeitura', secretaria: null, caminho: [] }, { rotulo: 'Saúde', secretaria: 'SAUDE', caminho: [] }] } }))
    fireEvent.click(screen.getByRole('button', { name: /Saúde/ }))
    await waitFor(() => expect(screen.getByTestId('url').textContent).toContain('secretaria=SAUDE'))
    expect(screen.getByTestId('url').textContent).toContain('competencia=2026-10')
    const chamada = (fetch as unknown as { mock: { calls: string[][] } }).mock.calls.at(-1)![0]
    expect(chamada).toContain('/paineis/folha-modelo?competencia=2026-10&secretaria=SAUDE')
    fireEvent.click(within(screen.getByRole('article', { name: 'ZZ folha_bruta' })).getByRole('button', { name: 'Ver detalhe' }))
    await waitFor(() => expect(screen.getByTestId('url').textContent).toContain('kpi=folha_bruta'))
    fireEvent.click(await screen.findByRole('button', { name: 'Prefeitura' }))
    await waitFor(() => expect(screen.getByTestId('url').textContent).not.toContain('secretaria='))
    expect(screen.getByTestId('url').textContent).toContain('competencia=2026-10')
  })

  it('filtros na URL: recarregar com a URL mantém competência e secretaria', async () => {
    montar('/painel/modelo-folha?competencia=2026-09&secretaria=SAUDE')
    await screen.findAllByRole('article')
    const url = (fetch as unknown as { mock: { calls: string[][] } }).mock.calls[0][0]
    expect(url).toBe('/api/paineis/folha-modelo?competencia=2026-09&secretaria=SAUDE')
  })

  it('estado sem fonte', async () => {
    fila.push(() => resp(200, { ...OK, estado: 'sem_fonte', fonte: null, kpis: [], drill: null }))
    montar()
    expect(await screen.findByText('Esta área ainda não tem carga de dados.')).toBeInTheDocument()
    expect(screen.getByTestId('selo-fonte')).toHaveTextContent('Fonte: sem carga')
  })

  it('estado vazio com link para a última competência', async () => {
    fila.push(() => resp(200, { ...OK, estado: 'vazio', kpis: [], drill: null, ultima_competencia: { valor: '2026-10', rotulo: 'out/2026' } }))
    montar('/painel/modelo-folha?competencia=2026-06')
    fireEvent.click(await screen.findByRole('button', { name: /Ver última competência carregada \(out\/2026\)/ }))
    await waitFor(() => expect(screen.getByTestId('url').textContent).toContain('competencia=2026-10'))
  })

  it('estado de erro com "Tentar de novo"; carregando mostra esqueleto', async () => {
    fila.push(() => resp(500, { detail: 'x' }))
    montar()
    expect(screen.getByText('Carregando indicadores…')).toBeInTheDocument()
    expect(await screen.findByText('Não foi possível carregar este indicador.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Tentar de novo' }))
    expect(await screen.findByRole('article', { name: 'ZZ folha_bruta' })).toBeInTheDocument()
  })

  it('recorte pequeno mostra o texto de privacidade, não o valor (UXD6)', async () => {
    fila.push(() => resp(200, { ...OK, kpis: [kpi('folha_bruta', 'brl', 'm1', { valor: null, suprimido: true })], drill: { ...OK.drill!, suprimido: true, itens: [], total: null } }))
    montar()
    expect((await screen.findAllByText('Grupo pequeno, oculto por privacidade')).length).toBe(2)
  })
})
