import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

// Parte 2 (PI12): o card final mostra o modo da prata/ouro e as contagens do delta. Só dados sintéticos.
const Z = { bronze: 0, prata: 0, ouro: 0, pendentes: 0 }
const SAUDE = {
  id: 1, nome: 'Saude', descricao: null, grupo: 'painel_cidadao', triagem: 'schema', status: 'implementado', prioridade: 1,
  conector_id: 'saude', tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: true, com_erro: false,
  contagens: { ...Z, bronze: 13 }, ultima_carga: null, completa_disponivel: true, completa_liberada: true, completa_motivo: null,
}
const EX = { id: 5, status: 'sucesso', usuario: 'admin@aion.local', iniciado_em: '2026-10-26T15:00:00Z',
  finalizado_em: '2026-10-26T15:02:00Z', lidos: 13, novos: 3, atualizados: 2, ignorados: 8, lotes: 2, gravados: 5,
  total_origem: 13, processados: 13, tabela_atual: null, pausa_motivo: null, cancelamento_pedido: false, mensagem: null, erro: null }

function monta(execucao: object) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const c = url.endsWith('/conectores')
      ? { conectores: [], execucoes: [], bases: [SAUDE], totais: {}, carga_habilitada: false }
      : { conector: 'saude', liberada: true, motivo: null, execucao, camadas: { bronze: 13, prata: 9, pendencias: 1 }, reconciliacao: null }
    return new Response(JSON.stringify(c), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  salvaSessao({ token: 't', perfil: 'admin', nome: 'T' })
  render(<MemoryRouter initialEntries={['/conectores']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('Parte 2: modo da prata/ouro no card final', () => {
  it('incremental mostra novas, alteradas, excluídas e sem alteração', async () => {
    monta({ ...EX, camadas_modo: 'incremental',
      camadas_contagens: { novas: 3, alteradas: 2, excluidas: 0, sem_alteracao: 8, motivo: 'só fatos/tributário mudaram' } })
    const modo = await screen.findByLabelText('Modo da prata e do ouro')
    expect(within(modo).getByText('Incremental')).toBeInTheDocument()
    for (const [t, v] of [['Novas', '3'], ['Alteradas', '2'], ['Excluídas', '0'], ['Sem alteração', '8']]) {
      expect(within(modo).getByText(t).nextSibling).toHaveTextContent(v)
    }
    expect(screen.getByText('Motivo: só fatos/tributário mudaram')).toBeInTheDocument()
  })

  it('recomposição mostra o modo e o motivo, sem contagens do delta', async () => {
    monta({ ...EX, camadas_modo: 'recomposicao', camadas_contagens: { motivo: 'pedida pelo administrador' } })
    const modo = await screen.findByLabelText('Modo da prata e do ouro')
    expect(within(modo).getByText('Recomposição completa')).toBeInTheDocument()
    expect(within(modo).queryByText('Novas')).toBeNull()
    expect(screen.getByText('Motivo: pedida pelo administrador')).toBeInTheDocument()
  })

  it('execução antiga (sem modo) não mostra o bloco', async () => {
    monta(EX)
    await screen.findByLabelText('Resultado da carga completa de Saúde')
    expect(screen.queryByLabelText('Modo da prata e do ouro')).toBeNull()
  })
})
