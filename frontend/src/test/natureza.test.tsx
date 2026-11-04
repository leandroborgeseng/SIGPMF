import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import CargaNatureza from '../CargaNatureza'

const VAZIO = { total: 0, mais_usadas: [], sem_nome: [], ultima_execucao: null }
const CHEIO = { total: 839, mais_usadas: [{ codigo: 1, descricao: 'IPTU', inscricoes: 1200 }], sem_nome: [{ codigo: 77, inscricoes: 9 }],
  ultima_execucao: { status: 'sucesso', mensagem: 'ok', finalizado_em: '2026-11-03T22:00:00Z' } }
function mock(f: (u: string, i?: RequestInit) => [number, unknown]) {
  vi.stubGlobal('fetch', vi.fn(async (u: string, i?: RequestInit) => { const [s, b] = f(u, i); return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } }) }))
}
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('natureza da dívida', () => {
  it('envia o arquivo, mostra a conferência e os códigos sem nome', async () => {
    let enviou = false
    mock((_u, i) => {
      if (i?.method === 'POST') { enviou = true; return [200, { mensagem: '839 naturezas carregadas. Recalcule os cubos para ver os nomes nos painéis.' }] }
      return [200, enviou ? CHEIO : VAZIO]
    })
    render(<CargaNatureza ehAdmin />)
    expect(await screen.findByText(/Nenhuma natureza carregada/)).toBeVisible()
    await userEvent.upload(screen.getByLabelText(/Enviar arquivo das naturezas/), new File(['COD_NATU;DESCRICAO\n1;IPTU\n'], 'n.csv', { type: 'text/csv' }))
    expect(await screen.findByText(/Recalcule os cubos para ver/)).toBeVisible()
    expect(await screen.findByText('839 naturezas carregadas.')).toBeVisible()
    expect(screen.getByText(/Códigos em uso sem nome no arquivo: 77/)).toBeVisible()
    expect(screen.getByText('Conferir as mais usadas na dívida')).toBeVisible()
  })

  it('arquivo recusado mostra o motivo; quem não é admin não envia', async () => {
    mock((_u, i) => (i?.method === 'POST' ? [422, { detail: 'Cabeçalho esperado: COD_NATU;DESCRICAO, sem outras colunas.' }] : [200, VAZIO]))
    const { unmount } = render(<CargaNatureza ehAdmin />)
    await screen.findByText(/Nenhuma natureza carregada/)
    await userEvent.upload(screen.getByLabelText(/Enviar arquivo das naturezas/), new File(['x'], 'n.csv', { type: 'text/csv' }))
    expect(await screen.findByText(/Cabeçalho esperado/)).toBeVisible()
    unmount()
    render(<CargaNatureza ehAdmin={false} />)
    await screen.findByText(/Nenhuma natureza carregada/)
    expect(screen.queryByLabelText(/Enviar arquivo das naturezas/)).toBeNull()
  })
})
