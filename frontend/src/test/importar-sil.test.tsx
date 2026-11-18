import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

function mockFetch(rotas: Record<string, { status?: number; corpo: unknown }>) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    void init
    const caminho = String(url).replace('/api', '')
    const r = rotas[caminho] ?? { status: 404, corpo: { detail: 'não' } }
    return new Response(JSON.stringify(r.corpo), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}

function montar(perfil: string) {
  salvaSessao({ token: 't', perfil, nome: 'Teste' })
  return render(<MemoryRouter initialEntries={['/cargas/sil']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

beforeEach(() => sessionStorage.clear())
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Importar imóveis SIL', () => {
  it('só o administrador vê o menu e o formulário', async () => {
    const f = mockFetch({})
    montar('leitura')
    expect(await screen.findByRole('alert')).toHaveTextContent('Você não tem permissão para esta tela.')
    expect(screen.queryByLabelText('Arquivo CSV')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Importar imóveis SIL' })).toBeNull()
    expect(f).not.toHaveBeenCalled()
  })

  it('administrador envia o CSV e o polling mostra concluído com o total', async () => {
    const f = mockFetch({
      '/cargas/sil/imoveis': { status: 202, corpo: { id: 7, status: 'em_andamento', situacao: 'em_andamento', total: 0 } },
      '/cargas/sil/imoveis/7': { corpo: { id: 7, status: 'sucesso', situacao: 'concluido', total: 2, mensagem: null } },
    })
    montar('admin')
    expect(await screen.findByRole('link', { name: 'Importar imóveis SIL' })).toBeVisible()
    const input = screen.getByLabelText('Arquivo CSV') as HTMLInputElement
    await userEvent.upload(input, new File(['a;b'], 'imoveis.csv', { type: 'text/csv' }))
    input.closest('form')!.noValidate = true // jsdom não marca o arquivo como preenchido para a validação nativa
    await userEvent.click(screen.getByRole('button', { name: 'Importar' }))
    expect(await screen.findByText('Concluído')).toBeVisible()
    expect(screen.getByText(/Total carregado:/)).toHaveTextContent('Total carregado: 2')
    const post = f.mock.calls.find((c) => String(c[0]).endsWith('/cargas/sil/imoveis'))
    expect(post?.[1]?.body).toBeInstanceOf(FormData)
    expect((post?.[1]?.headers as Record<string, string>)['Content-Type']).toBeUndefined()
    expect(f.mock.calls.some((c) => String(c[0]).endsWith('/cargas/sil/imoveis/7'))).toBe(true)
  })

  it('mostra em andamento enquanto a carga não termina', async () => {
    mockFetch({
      '/cargas/sil/imoveis': { status: 202, corpo: { id: 8, status: 'em_andamento', situacao: 'em_andamento', total: 0 } },
      '/cargas/sil/imoveis/8': { corpo: { id: 8, status: 'em_andamento', situacao: 'em_andamento', total: 0, mensagem: null } },
    })
    montar('admin')
    const input = (await screen.findByLabelText('Arquivo CSV')) as HTMLInputElement
    await userEvent.upload(input, new File(['a'], 'imoveis.csv', { type: 'text/csv' }))
    input.closest('form')!.noValidate = true
    await userEvent.click(screen.getByRole('button', { name: 'Importar' }))
    expect(await screen.findByText('Em andamento')).toBeVisible()
    expect(screen.queryByText(/Total carregado:/)).toBeNull()
  })

  it('mostra erro quando a carga falha', async () => {
    mockFetch({
      '/cargas/sil/imoveis': { status: 202, corpo: { id: 9, status: 'em_andamento', situacao: 'em_andamento', total: 0 } },
      '/cargas/sil/imoveis/9': { corpo: { id: 9, status: 'falha', situacao: 'erro', total: 0, mensagem: 'Arquivo inválido: linha 2: idfisico inválido' } },
    })
    montar('admin')
    const input = (await screen.findByLabelText('Arquivo CSV')) as HTMLInputElement
    await userEvent.upload(input, new File(['a'], 'imoveis.csv', { type: 'text/csv' }))
    input.closest('form')!.noValidate = true
    await userEvent.click(screen.getByRole('button', { name: 'Importar' }))
    expect(await screen.findByText('Erro')).toBeVisible()
    expect(screen.getByText(/idfisico inválido/)).toBeVisible()
  })
})
