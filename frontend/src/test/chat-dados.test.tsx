import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

function montar(caminho = '/chat') {
  salvaSessao({ token: 't', perfil: 'leitura', nome: 'Teste leitura' })
  return render(<MemoryRouter initialEntries={[caminho]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

const OK = {
  resposta: 'A última carga da SIL leu 17 registros.',
  estado: 'ok', consulta: 'ultima_carga', fonte: 'SIL', filtros: { fonte: 'SIL' },
  carga_em: '2026-11-02T15:30:00+00:00', status_carga: 'sucesso',
  numeros: { lidos: 17, novos: 4, atualizados: 2, ignorados: 1 },
}

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('Chat de dados', () => {
  it('está no menu e mostra fonte, filtros e data da carga', async () => {
    let soltar: (r: Response) => void = () => {}
    const f = vi.fn(() => new Promise<Response>((resolve) => { soltar = resolve }))
    vi.stubGlobal('fetch', f)
    montar()
    expect(await screen.findByRole('link', { name: 'Chat de dados' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chat de dados' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Pergunta'), 'Qual a última carga da SIL?')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(await screen.findByText('Consultando os dados…')).toBeInTheDocument()
    expect(f).toHaveBeenCalledWith('/api/chat', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ pergunta: 'Qual a última carga da SIL?' }),
      headers: expect.objectContaining({ Authorization: 'Bearer t' }),
    }))
    soltar(new Response(JSON.stringify(OK), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    expect(await screen.findByText(OK.resposta)).toBeInTheDocument()
    expect(screen.getByText('Fonte').nextElementSibling).toHaveTextContent('SIL')
    expect(screen.getByText('Filtros').nextElementSibling).toHaveTextContent('fonte: SIL')
    expect(screen.getByText('Data da carga').nextElementSibling).toHaveTextContent('02/11/2026 12:30')
    expect(screen.getByText(/Lidos 17/)).toBeInTheDocument()
  })

  it('mostra o estado sem dados', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      resposta: 'Não há carga.', estado: 'sem_carga', consulta: 'ultima_carga', fonte: 'SIL',
      filtros: { fonte: 'SIL' }, carga_em: null, status_carga: null, numeros: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    montar()
    await userEvent.type(screen.getByLabelText('Pergunta'), 'Última carga da SIL')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(await screen.findByText('Não há carga concluída desta fonte.')).toBeInTheDocument()
    expect(screen.getByText('Data da carga').nextElementSibling).toHaveTextContent('—')
  })

  it('mostra a falha do LiteLLM e a recusa do catálogo', async () => {
    const f = vi.fn()
    vi.stubGlobal('fetch', f)
    f.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'O serviço de linguagem não está disponível agora. Tente de novo em instantes.' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }))
    montar()
    await userEvent.type(screen.getByLabelText('Pergunta'), 'Última carga da SIL')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(await screen.findByText(/não está disponível agora/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeInTheDocument()

    f.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Essa pergunta está fora das consultas disponíveis.' }),
      { status: 422, headers: { 'Content-Type': 'application/json' } }))
    await userEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }))
    expect(await screen.findByText(/fora das consultas disponíveis/)).toBeInTheDocument()
    expect(screen.queryByText(/não está disponível agora/)).toBeNull()
  })

  it('mostra só o texto quando a resposta vem como JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      resposta: '```json\n{ "resposta": "Só o texto da folha." }\n```',
      estado: 'ok', consulta: 'folha_senior', fonte: 'Folha vigente', filtros: { ano: '2099', mes: '6' },
      carga_em: null, status_carga: null, numeros: { vencimentos: 90000 },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    montar()
    await userEvent.type(screen.getByLabelText('Pergunta'), 'Qual o maior salário da prefeitura?')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(await screen.findByText('Só o texto da folha.')).toBeInTheDocument()
    expect(screen.queryByText(/\{/)).toBeNull()
    expect(screen.getByText('Fonte').nextElementSibling).toHaveTextContent('Folha vigente')
    expect(screen.getByText(/Vencimentos 90/)).toBeInTheDocument()
  })
})
