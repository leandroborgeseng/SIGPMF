import { render, screen } from '@testing-library/react'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import CargaAns from '../CargaAns'
import { PainelCobertura } from '../paineis/Cobertura'

const P = (dim: string, valor: number, periodo = '') => ({ dim, valor, periodo })
const RESP = { competencia: { ano: 2026, mes: 8 }, carregado_em: '2026-11-03T22:00:00Z', estado: 39.1, posicao: 12, total_pares: 40,
  atual: { beneficiarios: 115906, odonto: 41684, populacao: 366534, taxa: 31.62, taxa_anterior: 30.9, beneficiarios_anterior: 113000, aderidos: 1890, cancelados: 2085 },
  cobertura_mes: [P('', 30.9, '2025-08'), P('', 31.6, '2026-08')], beneficiarios_mes: [P('', 113000, '2025-08'), P('', 115906, '2026-08')],
  taxa_faixa: [P('0 a 4 anos', 30), P('60 a 64 anos', 25)], operadoras: [P('OP A', 76585)], contratos: [P('Coletivo Empresarial', 55647)],
  cidades: [P('Ribeirão Preto', 50), P('Franca', 31.6)], bairros: [{ bairro: 'Centro', pessoas: 900, taxa_estimada: 28.4 }] }
function mock(f: (u: string, i?: RequestInit) => [number, unknown]) {
  vi.stubGlobal('fetch', vi.fn(async (u: string, i?: RequestInit) => { const [s, b] = f(u, i); return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } }) }))
}
const montar = () => render(<MemoryRouter><AuthProvider><PainelCobertura /></AuthProvider></MemoryRouter>)
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('cobertura de planos', () => {
  it('mostra a taxa, avisa que conta vínculos e rotula o bairro como estimativa', async () => {
    mock(() => [200, RESP])
    const { container } = montar()
    expect(await screen.findByText('31,6%', { selector: '.cartao-valor' })).toBeVisible()
    expect(screen.getByText(/conta vínculos, não pessoas/)).toBeVisible()
    expect(screen.getByText('12ª de 40')).toBeVisible()
    expect(screen.getByText('calculada, não medida')).toBeVisible()
    expect(screen.getByText(/A ANS não publica bairro/)).toBeVisible()
    expect(screen.getByRole('row', { name: /Centro/ })).toHaveTextContent('28,4%')
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })

  it('sem dados aponta para Conectores; erro oferece nova tentativa', async () => {
    mock(() => [200, { atual: null }])
    const { unmount } = montar()
    expect(await screen.findByRole('link', { name: /ANS e IBGE/ })).toHaveAttribute('href', '/conectores#fonte-ans')
    unmount()
    mock(() => [500, { detail: 'x' }])
    montar()
    expect(await screen.findByText(/Não foi possível carregar a cobertura/)).toBeVisible()
  })

  it('card da carga: admin dispara e vê os meses', async () => {
    salvaSessao({ token: 't', perfil: 'admin', nome: 'T' })
    mock(() => [200, { meses: 64, de: 202105, ate: 202608, em_andamento: null, ultima_execucao: null }])
    render(<MemoryRouter><AuthProvider><CargaAns ehAdmin /></AuthProvider></MemoryRouter>)
    expect(await screen.findByText(/64 meses carregados, de 05\/2021 a 08\/2026/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Carregar agora' })).toBeEnabled()
  })
})
