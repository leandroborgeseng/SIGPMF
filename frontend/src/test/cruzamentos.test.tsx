import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { PainelCruzamentos } from '../paineis/Cruzamentos'

const RESP = {
  calculado: true, gerado_em: '2026-11-03T22:00:00Z',
  medidas: [{ chave: 'pessoas', nome: 'Pessoas no cadastro', formato: 'n', bom: null }, { chave: 'inadimplencia', nome: 'Inadimplência', formato: 'pct', bom: 'baixo' }],
  bairros: [{ bairro_id: 1, bairro: 'Centro', pessoas: 500, inadimplencia: 10 }, { bairro_id: 2, bairro: 'Vila Nova', pessoas: 900, inadimplencia: null }, { bairro_id: 3, bairro: 'Jardim Alto', pessoas: 200, inadimplencia: 40 }],
  destaques: [{ bairro: 'Jardim Alto', medida: 'inadimplencia', valor: 40, z: 3.1, sinal: 'ruim', frase: 'Inadimplência: 40,0%, 2,5× a média.' }],
  correlacoes: [{ a: 'x', b: 'y', r: 0.62, bairros: 40, frase: 'Onde x é maior, y tende a ser maior.' }],
  cidade: [{ id: 'falecido_imovel', titulo: 'Falecidos ainda donos de imóvel', valor: 1234, formato: 'n', detalhe: 'Óbito registrado e posse ativa.', areas: ['Saúde', 'IPTU'], acao: 'Atualizar o cadastro.' }],
}
function mock(f: (u: string, i?: RequestInit) => [number, unknown]) {
  vi.stubGlobal('fetch', vi.fn(async (u: string, i?: RequestInit) => { const [s, b] = f(u, i); return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } }) }))
}
const montar = () => render(<MemoryRouter><AuthProvider><PainelCruzamentos /></AuthProvider></MemoryRouter>)
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('Cruzamentos', () => {
  it('mostra achados, destaques, correlações e ordena a tabela, sem botão para quem não é admin', async () => {
    mock((u) => [200, u.endsWith('/cruzamentos') ? RESP : { em_andamento: null }])
    const { container } = montar()
    expect(await screen.findByText('Falecidos ainda donos de imóvel')).toBeVisible()
    expect(screen.getByText('Inadimplência: 40,0%, 2,5× a média.')).toBeVisible()
    expect(screen.getByText(/r = 0,62 em 40 bairros/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /Recalcular/ })).toBeNull()
    const tabela = screen.getByRole('table', { name: 'Medidas por bairro' })
    const bairros = () => within(tabela).getAllByRole('rowheader').map((c) => c.textContent)
    expect(bairros()).toEqual(['Vila Nova', 'Centro', 'Jardim Alto'])
    await userEvent.click(within(tabela).getByRole('button', { name: /Inadimplência/ }))
    expect(bairros()).toEqual(['Jardim Alto', 'Centro', 'Vila Nova'])  // sem valor fica por último
    await userEvent.type(screen.getByRole('searchbox', { name: /Buscar bairro/ }), 'vila')
    expect(bairros()).toEqual(['Vila Nova'])
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })

  it('não calculado: admin dispara o primeiro cálculo', async () => {
    salvaSessao({ token: 't', perfil: 'admin', nome: 'T' })
    const posts: string[] = []
    mock((u, i) => {
      if (i?.method === 'POST') { posts.push(u); return [202, {}] }
      return [200, u.endsWith('/cruzamentos') ? { calculado: false, medidas: [], bairros: [], destaques: [], correlacoes: [], cidade: [] } : { em_andamento: null }]
    })
    montar()
    expect(await screen.findByText(/ainda não foram calculados/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Recalcular cubos' }))
    expect(posts.some((u) => u.endsWith('/cubos/recalcular'))).toBe(true)
    expect(await screen.findByRole('button', { name: 'Recalculando…' })).toBeDisabled()
  })

  it('erro da API vira mensagem com nova tentativa', async () => {
    mock(() => [500, { detail: 'x' }])
    montar()
    expect(await screen.findByText(/Não foi possível carregar os cruzamentos/)).toBeVisible()
  })
})
