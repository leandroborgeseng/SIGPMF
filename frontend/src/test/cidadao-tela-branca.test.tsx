import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { dataBR, dataHoraBR, salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { ErrorBoundary } from '../componentes'

// Dados 100% SINTÉTICOS.
type Rota = (url: string) => [number, unknown]
function mockFetch(rota: Rota) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const [status, corpo] = rota(url)
    return new Response(JSON.stringify(corpo), { status, headers: { 'Content-Type': 'application/json' } })
  }))
}
const LISTA = [{ cidadao_id: 'c-1', nome: 'Fulano Sintético', cpf: '***.456.789-**', sistemas: ['SAUDE'] }]
function ficha(dados: unknown, extra: Record<string, unknown> = {}) {
  return { cidadao_id: 'c-1', nome: 'Fulano Sintético', cpf: '***.456.789-**', sistemas: ['SAUDE'], dados, enderecos: [], ...extra }
}
async function abrirFicha(corpo: unknown, status = 200) {
  mockFetch((u) => (u.includes('/gold/cidadaos/') ? [status, corpo] : [200, LISTA]))
  salvaSessao({ token: 't', perfil: 'operador', nome: 'Teste' })
  render(<MemoryRouter initialEntries={['/simulador']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
  await userEvent.type(screen.getByLabelText('Nome ou CPF completo'), 'Fulano')
  await userEvent.click(screen.getByRole('button', { name: 'Buscar' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Fulano Sintético' }))
}
const nasc = (valor: unknown) => ({ dominio: 'identificacao', campo: 'data_nascimento', valor, fonte: 'SAUDE', atualizado_em: null })

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('datas tolerantes', () => {
  it.each([
    ['1980-05-12', '12/05/1980'], ['1980-05-12 00:00:00', '12/05/1980'], ['1980-05-12T00:00:00', '12/05/1980'],
    ['12/05/1980', '12/05/1980'], ['', '—'], [null, '—'], ['lixo', 'lixo'],
  ])('dataBR(%s) = %s sem lançar', (v, esperado) => expect(dataBR(v as string | null)).toBe(esperado))
  it('dataHoraBR não lança com lixo', () => { expect(dataHoraBR('xx')).toBe('xx'); expect(dataHoraBR(null)).toBe('—') })
})

describe('ficha do cidadão (tela branca)', () => {
  it.each(['1980-05-12 00:00:00', '1980-05-12T00:00:00', '12/05/1980'])('data de nascimento %s renderiza', async (v) => {
    await abrirFicha(ficha([nasc(v)]))
    expect(await screen.findByRole('heading', { name: 'Fulano Sintético' })).toBeInTheDocument()
    expect(screen.getByText(/12\/05\/1980/)).toBeInTheDocument()
    expect(screen.getByTestId('cpf')).toHaveTextContent('***.456.789-**')
  })
  it('lixo, nulos e listas nulas não derrubam a tela', async () => {
    await abrirFicha(ficha([nasc('lixo'), { dominio: 'saude', campo: 'cns', valor: null, fonte: null, atualizado_em: 'xx' }],
      { cpf: null, cns: null, sistemas: null, enderecos: null }))
    expect(await screen.findByRole('heading', { name: 'Fulano Sintético' })).toBeInTheDocument()
    expect(screen.getByText(/lixo/)).toBeInTheDocument()
    expect(screen.getByTestId('cpf')).toHaveTextContent('não informado')
  })
  it('pessoa sem vínculos mostra —', async () => {
    await abrirFicha(ficha(null))
    expect(await screen.findByRole('heading', { name: 'Fulano Sintético' })).toBeInTheDocument()
    const cadastro = screen.getByRole('heading', { name: 'Dados cadastrais' }).closest('section') as HTMLElement
    expect(within(cadastro).getByText('—')).toBeInTheDocument()
  })
  it('500 na rota de detalhe mostra erro com motivo e volta à busca', async () => {
    await abrirFicha({ detail: 'x' }, 500)
    expect(await screen.findByText(/Não foi possível carregar os dados deste cidadão \(HTTP 500\)/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Voltar à busca' }))
    expect(await screen.findByRole('button', { name: 'Fulano Sintético' })).toBeInTheDocument()
  })
})

describe('ErrorBoundary', () => {
  it('erro de render mostra mensagem amigável com motivo e botões', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    let quebra = true
    function Bomba() { if (quebra) throw new RangeError('Invalid time value'); return <p>ok</p> }
    const voltar = vi.fn()
    render(<ErrorBoundary titulo="Não foi possível carregar os dados deste cidadão." onVoltar={voltar} rotuloVoltar="Voltar à busca"><Bomba /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toHaveTextContent('Não foi possível carregar os dados deste cidadão.')
    expect(screen.getByRole('alert')).toHaveTextContent('RangeError: Invalid time value')
    await userEvent.click(screen.getByRole('button', { name: 'Voltar à busca' }))
    expect(voltar).toHaveBeenCalled()
    quebra = false
    await userEvent.click(screen.getByRole('button', { name: 'Tentar de novo' }))
    expect(screen.getByText('ok')).toBeInTheDocument()
  })
})
