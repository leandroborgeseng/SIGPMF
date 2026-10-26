import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { perda } from '../pages/PainelBases'

const Z = { bronze: 0, prata: 0, ouro: 0, pendentes: 0 }
const base = (o: object) => ({ descricao: null, grupo: 'painel_cidadao', triagem: 'schema', prioridade: null, tem_conector: false, pode_carregar: false, motivo_bloqueio: 'Conector ainda não implementado', tem_amostra: false, com_erro: false, contagens: Z, ultima_carga: null, ...o })
const ULT = { id: 9, tipo: 'carga_demo_amostra', status: 'sucesso', iniciado_em: '2026-10-26T00:30:00Z', finalizado_em: '2026-10-26T00:30:05Z', lidos: 1200, mensagem: null }
const BASES = [
  base({ id: 1, nome: 'Saude', status: 'implementado', prioridade: 1, tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: false, contagens: { bronze: 1200, prata: 1080, ouro: 1080, pendentes: 7 }, ultima_carga: ULT }),
  base({ id: 2, nome: 'Cemiterio', status: 'implementado', prioridade: 2, tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: true, com_erro: true, contagens: { bronze: 300, prata: 300, ouro: 250, pendentes: 0 }, ultima_carga: { ...ULT, status: 'falha' } }),
  base({ id: 3, nome: 'ISS', status: 'mapeado', prioridade: 3, motivo_bloqueio: 'Sem lista de colunas aprovada' }),
  base({ id: 4, nome: 'Bibliotecario', status: 'triado', triagem: 'nome' }),
  base({ id: 5, nome: 'Senhas', status: 'excluido', grupo: 'descartavel', motivo_bloqueio: 'Base descartada na triagem' }),
]
const RESP = { conectores: [], execucoes: [], bases: BASES, totais: {} }

function mock(rota: (url: string, init?: RequestInit) => [number, unknown]) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const [s, c] = rota(url, init)
    return new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
const montar = (perfil: string) => {
  salvaSessao({ token: 't', perfil, nome: 'T' })
  return render(<MemoryRouter initialEntries={['/conectores']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}
const cartao = async (nome: string) => (await screen.findByRole('heading', { name: new RegExp(`^${nome}`) })).closest('li') as HTMLElement

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('painel de bases', () => {
  it('status com texto, contagens com milhar, "—" sem conector, totais das visíveis, sem botões p/ leitura (UX-C1..C4, C9, C10)', async () => {
    mock(() => [200, RESP])
    const { container } = montar('leitura')
    const saude = await cartao('Saude')
    expect(within(saude).getByText('Pronto')).toBeInTheDocument()
    expect(within(saude).getByText('1.200')).toBeInTheDocument()
    expect(within(saude).getByText(/7 sem correspondência/)).toBeInTheDocument()
    const iss = await cartao('ISS')
    expect(within(iss).getByText('Mapeado')).toBeInTheDocument()
    expect(within(iss).getAllByText('—').length).toBe(3)
    expect(within(iss).getByText(/Pendências: —/)).toBeInTheDocument()
    expect(within(await cartao('Bibliotecario')).getByText('Só triado')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^Senhas/ })).toBeNull() // descartadas ocultas por padrão
    const totais = screen.getByRole('list', { name: /Totais por camada/ })
    expect(within(totais).getByText('1.500')).toBeInTheDocument()
    expect(within(totais).getByText('1.330')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Puxar amostra/ })).toBeNull()
    expect(screen.getByText(/5 bases cadastradas · 2 prontas · 1 mapeadas · 1 triadas · 1 descartadas/)).toBeInTheDocument()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
  })

  it('filtros: descartadas e com erro (UX-C1, PB8)', async () => {
    mock(() => [200, RESP])
    montar('leitura')
    await cartao('Saude')
    await userEvent.click(screen.getByRole('button', { name: 'Descartadas' }))
    const s = await cartao('Senhas')
    expect(within(s).getByText('Descartado')).toBeInTheDocument()
    expect(within(s).getByText('Base descartada na triagem')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Com erro' }))
    expect(within(await cartao('Cemiterio')).getByText('Última carga falhou')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^Saude/ })).toBeNull()
  })

  it('perda em destaque (UX-C5, PB10)', async () => {
    expect(perda(1200, 1080)).toBe(10)
    expect(perda(100, 100)).toBeNull()
    mock(() => [200, RESP])
    montar('leitura')
    expect(within(await cartao('Saude')).getByText(/−10% na prata/)).toBeInTheDocument()
    expect(within(await cartao('Cemiterio')).getByText(/−17% no ouro/)).toBeInTheDocument()
  })

  it('admin: amostra dispara, completa travada sem amostra, reprocessar pede confirmação (UX-C6..C8)', async () => {
    const f = mock((url, init) => (init?.method === 'POST' ? [200, { status: 'sucesso', lidos: 10, mensagem: null }] : [200, RESP]))
    montar('admin')
    const saude = await cartao('Saude')
    expect(within(saude).getByRole('button', { name: 'Puxar carga completa de Saude' })).toBeDisabled()
    expect(within(saude).getByText(/Libera depois da conferência da amostra/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Puxar amostra de ISS' })).toBeNull()
    await userEvent.click(within(saude).getByRole('button', { name: 'Puxar amostra de Saude' }))
    expect(await screen.findByText(/Puxar amostra de Saude concluída/)).toBeInTheDocument()
    const post = f.mock.calls.find(([, i]) => i?.method === 'POST')!
    expect(post[0]).toBe('/api/conectores/1/executar')
    expect(JSON.parse(String(post[1]!.body))).toEqual({ modo: 'amostra' })
    const cem = await cartao('Cemiterio')
    await userEvent.click(within(cem).getByRole('button', { name: 'Reprocessar Cemiterio' }))
    const dlg = screen.getByRole('dialog')
    expect(dlg).toHaveTextContent(/Reprocessar prata e ouro de Cemiterio/)
    await userEvent.click(within(dlg).getByRole('button', { name: 'Cancelar' }))
    expect(f.mock.calls.filter(([, i]) => i?.method === 'POST').length).toBe(1)
  })

  it('erro na execução vira alerta; erro de API e vazio (UX-C8, estados)', async () => {
    mock((_u, init) => (init?.method === 'POST' ? [409, { detail: 'Saude: Sem lista de colunas aprovada.' }] : [200, RESP]))
    montar('admin')
    await userEvent.click(within(await cartao('Saude')).getByRole('button', { name: 'Puxar amostra de Saude' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Falha na carga de Saude: Saude: Sem lista de colunas aprovada.')
  })

  it('erro de API mostra tentar de novo', async () => {
    mock(() => [500, {}])
    montar('leitura')
    expect(await screen.findByText('Não foi possível carregar os conectores.')).toBeInTheDocument()
  })

  it('vazio', async () => {
    mock(() => [200, { ...RESP, bases: [] }])
    montar('leitura')
    expect(await screen.findByText('Nenhuma base cadastrada.')).toBeInTheDocument()
  })
})
