import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

// PR-A (UXA1–UXA5, CT1–CT3, CT13, CT14). Dados mockados ZZTESTE, nada real.
const Z = { bronze: 0, prata: 0, ouro: 0, pendentes: 0 }
const SEM = 'Rode a amostra e confira o resultado antes da carga completa: clique em "Rodar amostra" no card desta fonte, em Conectores > Bases de origem.'
const PEND = 'A amostra terminou e falta aprová-la: confira o resultado e clique em "Aprovar amostra" no card desta fonte, em Conectores > Bases de origem.'
const base = (id: number, nome: string, conector: string, o: object = {}) => ({
  id, nome, descricao: 'ZZTESTE', grupo: 'painel_cidadao', triagem: 'schema', prioridade: id, status: 'implementado', conector_id: conector,
  tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: false, com_erro: false, contagens: Z, ultima_carga: null,
  completa_disponivel: true, completa_liberada: false, completa_motivo: SEM, exige_amostra: true, amostra: null, amostra_limite: 1000, ...o,
})
const AM_OK = { id: 77, status: 'sucesso', finalizado_em: '2026-10-29T23:00:00Z', lidos: 1000, gravados: 997, com_erro: 3, motivo: null, aprovada: false }
const BASES = [
  base(1, 'Dívida ativa', 'divida'),
  base(2, 'Financeiro', 'financeiro'),
  base(3, 'API SIL', 'sil'),
  base(4, 'IPTU', 'iptu', { amostra: { ...AM_OK, id: 5, aprovada: true }, completa_liberada: true, completa_motivo: null }),
]
const SIT = (liberada: boolean, motivo: string | null) => ({ conector: 'x', liberada, motivo, execucao: null, camadas: { bronze: 0, prata: 0, pendencias: 0 }, reconciliacao: null })
const PROGRESSO_VAZIO = { agora: '2026-10-29T18:00:00Z', rodando: false, atualizar_a_cada_s: 30, fontes: [] }

function mock(rota: (url: string, init?: RequestInit) => [number, unknown] | null) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    let r: [number, unknown] | null = rota(url, init)
    if (!r) {
      if (url === '/api/fontes/progresso') r = [200, PROGRESSO_VAZIO]
      else if (url.startsWith('/api/agenda')) r = [200, { agendas: [] }]
      else if (/\/completa$/.test(url)) r = [200, SIT(false, SEM)]
      else if (url === '/api/conectores') r = [200, { conectores: [], execucoes: [], bases: BASES, totais: {}, carga_habilitada: true }]
      else r = [404, { detail: 'não mockado' }]
    }
    return new Response(JSON.stringify(r[1]), { status: r[0], headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
const montar = () => {
  salvaSessao({ token: 't', perfil: 'admin', nome: 'T' })
  return render(<MemoryRouter initialEntries={['/conectores']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}
const cartao = async (nome: string) => (await screen.findByRole('heading', { name: new RegExp(`^${nome}`) })).closest('li') as HTMLElement

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('amostra obrigatória no card (PR-A)', () => {
  it('CT14/UXA5: nenhuma fonte que exige amostra fica sem jeito de rodá-la', async () => {
    mock(() => null)
    const { container } = montar()
    for (const b of BASES) {
      const c = await cartao(b.nome)
      const rodar = within(c).queryByRole('button', { name: `Rodar amostra (1.000 linhas) de ${b.nome}` })
      const aprovada = within(c).queryByText('Amostra aprovada')
      expect(rodar !== null || aprovada !== null).toBe(true)
      if (rodar) expect(rodar).toBeEnabled()
    }
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
  })

  it('UXA1/CT1/CT13: completa desabilitada com o motivo dizendo onde clicar', async () => {
    mock(() => null)
    montar()
    const c = await cartao('Dívida ativa')
    expect(within(c).getByRole('button', { name: 'Rodar amostra (1.000 linhas) de Dívida ativa' })).toHaveTextContent('Rodar amostra (1.000 linhas)')
    expect(within(c).getByRole('button', { name: 'Executar carga completa de Dívida ativa' })).toBeDisabled()
    expect(within(c).getByText(/clique em "Rodar amostra" no card desta fonte/)).toBeInTheDocument()
  })

  it('UXA2/UXA3/CT2: roda, mostra linhas e erros, "Ver erros", aprova e libera', async () => {
    let aprovada = false
    let rodou = false
    const f = mock((url, init) => {
      if (url === '/api/conectores/divida/amostra' && init?.method === 'POST') rodou = true
      if (url === '/api/conectores/divida/amostra' && init?.method === 'POST') return [202, { id: 77, status: 'em_andamento', em_andamento: true, lidos: 0, ignorados: 0, mensagem: null }]
      if (url === '/api/conectores/divida/amostra/77') return [200, { id: 77, status: 'sucesso', em_andamento: false, lidos: 1000, ignorados: 3, mensagem: null }]
      if (url === '/api/conectores/divida/completa/quarentena') return [200, { linhas: [{ id: 1, tabela: 'ZZTESTE_DIVIDA', motivo: 'chave nula', campos_nulos: ['id'], referencia: 'linha 12' }] }]
      if (url === '/api/conectores/divida/amostra/77/aprovar') { aprovada = true; return [200, { ...AM_OK, aprovada: true }] }
      if (url === '/api/conectores') return [200, { conectores: [], execucoes: [], carga_habilitada: true,
        bases: BASES.map((b) => b.conector_id !== 'divida' ? b
          : aprovada ? { ...b, amostra: { ...AM_OK, aprovada: true }, completa_liberada: true, completa_motivo: null }
          : rodou ? { ...b, amostra: AM_OK, completa_motivo: PEND } : b) }]
      if (url === '/api/conectores/divida/completa') return [200, SIT(aprovada, aprovada ? null : PEND)]
      return null
    })
    montar()
    const c = await cartao('Dívida ativa')
    await userEvent.click(within(c).getByRole('button', { name: 'Rodar amostra (1.000 linhas) de Dívida ativa' }))
    const ocupado = within(c).getByRole('button', { name: 'Rodando amostra de Dívida ativa…' })
    expect(ocupado).toBeDisabled()
    expect(ocupado).toHaveAttribute('aria-busy', 'true')
    await screen.findByText(/clique em "Aprovar amostra" no card desta fonte/, {}, { timeout: 5000 })
    await screen.findByText(/Amostra concluída: 1.000 linhas lidas, 3 com erro/, {}, { timeout: 5000 })
    expect(await screen.findByText(/Amostra concluída: 1.000 linhas lidas, 3 com erro/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Rodar de novo a amostra de Dívida ativa' }, { timeout: 5000 })).toBeEnabled()
    expect(screen.getByText(/clique em "Aprovar amostra" no card desta fonte/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ver erros da amostra de Dívida ativa' }))
    expect(await screen.findByText(/chave nula/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Aprovar amostra de Dívida ativa' }))
    await waitFor(async () => expect(within(await cartao('Dívida ativa')).getByText('Amostra aprovada')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Executar carga completa de Dívida ativa' })).toBeEnabled())
    expect(f.mock.calls.some(([u]) => String(u).includes('/franca/imoveis/atualizar'))).toBe(false)
  })

  it('UXA4: amostra com falha mostra o motivo e o botão segue ativo', async () => {
    let falhou = false
    mock((url, init) => {
      if (url === '/api/conectores/sil/amostra' && init?.method === 'POST') { falhou = true; return [202, { id: 9, status: 'falha', em_andamento: false, lidos: 0, ignorados: 0, mensagem: null, erro: { motivo: 'Sem acesso à API (ZZTESTE)' } }] }
      if (url === '/api/conectores' && falhou) return [200, { conectores: [], execucoes: [], carga_habilitada: true,
        bases: BASES.map((b) => b.conector_id === 'sil' ? { ...b, amostra: { ...AM_OK, id: 9, status: 'falha', lidos: 0, com_erro: 0, motivo: 'Sem acesso à API (ZZTESTE)' } } : b) }]
      return null
    })
    montar()
    const c = await cartao('API SIL')
    await userEvent.click(within(c).getByRole('button', { name: 'Rodar amostra (1.000 linhas) de API SIL' }))
    await screen.findByText('A amostra falhou: Sem acesso à API (ZZTESTE). Corrija e rode de novo.')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rodar amostra (1.000 linhas) de API SIL' })).toBeEnabled())
    expect(screen.getByText('A amostra falhou: Sem acesso à API (ZZTESTE). Corrija e rode de novo.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Aprovar amostra de API SIL/ })).toBeNull()
  })

  it('CT3: amostra que já passou aparece como aprovada, sem pedir outra', async () => {
    mock(() => null)
    montar()
    const c = await cartao('IPTU')
    expect(within(c).getByText('Amostra aprovada')).toBeInTheDocument()
    expect(within(c).queryByRole('button', { name: /Aprovar amostra/ })).toBeNull()
  })
})
