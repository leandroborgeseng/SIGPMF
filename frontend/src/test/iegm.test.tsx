import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import CargaIegm from '../CargaIegm'
import { PainelIegm } from '../paineis/Iegm'

const NOMES = { A: 'Altamente efetiva', 'B+': 'Muito efetiva', B: 'Efetiva', 'C+': 'Em fase de adequação', C: 'Baixo nível de adequação' }
const ind = (codigo: string, nome: string, area: string, serie: string[], tendencia: string | null, pctMelhor: number) => ({
  codigo, nome, area, descricao: `mede ${nome}`, serie: serie.map((f, i) => ({ ano_base: 2022 + i, faixa: f })),
  atual: serie[serie.length - 1], anterior: serie[serie.length - 2], tendencia,
  estado_ultimo: { A: 0, 'B+': 3, B: 97, 'C+': 261, C: 283 }, estado_total: 644, pct_melhor: pctMelhor, pct_mesma: 40.5 })
const RESP = { anos: [2022, 2023, 2024], apuracao: { '2024': 2025 }, ultimo: 2024, faixas: NOMES, carregado_em: '2026-11-03T22:00:00Z',
  indices: [ind('IEG-M', 'IEG-M geral', 'Prefeitura (todas as áreas)', ['C+', 'C+', 'C+'], 'igual', 15.5),
    ind('i-Gov TI', 'Governança de TI', 'Tecnologia da Informação', ['B', 'B', 'B+'], 'subiu', 5),
    ind('i-Fiscal', 'Gestão fiscal', 'Fazenda / Finanças', ['B+', 'B', 'C+'], 'caiu', 60)] }
function mock(f: (u: string, i?: RequestInit) => [number, unknown]) {
  vi.stubGlobal('fetch', vi.fn(async (u: string, i?: RequestInit) => { const [s, b] = f(u, i); return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } }) }))
}
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('IEG-M', () => {
  it('painel: faixa atual, subiu/caiu, matriz por área e comparação com o estado', async () => {
    mock(() => [200, RESP])
    const { container } = render(<MemoryRouter><AuthProvider><PainelIegm /></AuthProvider></MemoryRouter>)
    expect(await screen.findByText('Em fase de adequação', { selector: '.kpi-nota' })).toBeVisible()
    expect(screen.getByText('Governança de TI', { selector: '.kpi-nota' })).toBeVisible()  // subiu
    expect(screen.getByText('Gestão fiscal', { selector: '.kpi-nota' })).toBeVisible()  // caiu
    const matriz = screen.getByRole('table', { name: 'Faixa do IEG-M por área e ano base' })
    const fiscal = within(matriz).getByRole('row', { name: /Gestão fiscal/ })
    expect(fiscal).toHaveTextContent('B+ (Muito efetiva)B (Efetiva)C+ (Em fase de adequação)')
    expect(screen.getByRole('img', { name: /Gestão fiscal: A 0, B\+ 3, B 97, C\+ 261, C 283 municípios; Franca C\+/ })).toBeInTheDocument()
    expect(screen.getByText(/Ano base 2024, publicado pelo TCE em 2025/)).toBeVisible()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })

  it('painel vazio aponta para Conectores', async () => {
    mock(() => [200, { anos: [], indices: [], ultimo: null, faixas: NOMES }])
    render(<MemoryRouter><AuthProvider><PainelIegm /></AuthProvider></MemoryRouter>)
    expect(await screen.findByRole('link', { name: 'Conectores → IEG-M (TCE-SP)' })).toHaveAttribute('href', '/conectores#fonte-iegm')
  })

  it('card em Conectores: anos carregados; admin envia o arquivo e vê o motivo da recusa', async () => {
    mock((u) => u.endsWith('/iegm/arquivo') ? [422, { detail: "Linha 2: faixa 'D' inválida (use A, B+, B, C+ ou C)." }]
      : [200, { anos: [2014, 2024], ultima_execucao: { status: 'sucesso', mensagem: 'ok', finalizado_em: '2026-11-03T22:00:00Z' } }])
    const { unmount } = render(<CargaIegm ehAdmin={false} />)
    expect(await screen.findByText(/Anos base carregados: 2014 a 2024/)).toBeVisible()
    expect(screen.queryByLabelText('Enviar arquivo do IEG-M (.csv)')).toBeNull()
    unmount()
    render(<CargaIegm ehAdmin />)
    await userEvent.upload(await screen.findByLabelText('Enviar arquivo do IEG-M (.csv)'), new File(['x'], 'iegm.csv', { type: 'text/csv' }))
    expect(await screen.findByText(/faixa 'D' inválida/)).toBeVisible()
  })
})
