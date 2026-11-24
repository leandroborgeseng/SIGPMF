import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

// CT26c: fonte pausada na agenda desabilita amostra, aprovação e carga completa. Dados mockados ZZTESTE, nada real.
const Z = { bronze: 0, prata: 0, ouro: 0, pendentes: 0 }
const SEM = 'Rode a amostra acima e aprove o resultado para liberar a carga completa.'
const base = (id: number, nome: string, conector: string, o: object = {}) => ({
  id, nome, descricao: 'ZZTESTE', grupo: 'painel_cidadao', triagem: 'schema', prioridade: id, status: 'implementado', conector_id: conector,
  tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: false, com_erro: false, contagens: Z, ultima_carga: null,
  completa_disponivel: true, completa_liberada: false, completa_motivo: SEM, exige_amostra: true, amostra: null, amostra_limite: 1000, ...o,
})
const AM_OK = { id: 77, status: 'sucesso', finalizado_em: '2026-10-29T23:00:00Z', lidos: 1000, gravados: 997, com_erro: 3, motivo: null, aprovada: false }
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

const PAUSA_A = 'Fonte pausada na agenda. Reative antes de rodar a amostra.'
const PAUSA_C = 'Fonte pausada na agenda. Reative antes de rodar a carga completa.'
const BASES = [
  base(1, 'ZZTESTE Pausada', 'cemiterio', { agenda_pausada: true, agenda_pausada_motivo: PAUSA_A, completa_motivo: PAUSA_C,
    amostra: AM_OK }),
  base(2, 'ZZTESTE Ativa', 'saude', { agenda_pausada: false, agenda_pausada_motivo: null, amostra: AM_OK }),
]

describe('CT26c: fonte pausada na agenda', () => {
  it('desabilita rodar, aprovar e carga completa com a mesma mensagem; ativa segue como antes', async () => {
    mock((url) => (/cemiterio\/completa$/.test(url) ? [200, SIT(false, PAUSA_C)] : null))
    montar()
    const p = await cartao('ZZTESTE Pausada')
    expect(within(p).getByRole('button', { name: 'Rodar de novo a amostra de ZZTESTE Pausada' })).toBeDisabled()
    expect(within(p).getByRole('button', { name: 'Aprovar amostra de ZZTESTE Pausada' })).toBeDisabled()
    expect(within(p).getByText(PAUSA_A)).toBeInTheDocument()
    await waitFor(() => expect(within(p).getByRole('button', { name: /Executar carga completa de/ })).toBeDisabled())
    expect(within(p).getByText(PAUSA_C)).toBeInTheDocument()
    const a = await cartao('ZZTESTE Ativa')
    expect(within(a).getByRole('button', { name: 'Rodar de novo a amostra de ZZTESTE Ativa' })).toBeEnabled()
    expect(within(a).getByRole('button', { name: 'Aprovar amostra de ZZTESTE Ativa' })).toBeEnabled()
    expect(within(a).queryByText(PAUSA_A)).toBeNull()
  })
})
