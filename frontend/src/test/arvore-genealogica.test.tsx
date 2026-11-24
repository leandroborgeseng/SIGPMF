import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import Simulador from '../pages/Simulador'

const ficha = {
  cidadao_id: 'ego', nome: 'Ego Caio Ficticio', cpf: '***.000.002-**', cns: null, ve_cadastro: true,
  sistemas: ['SENIOR'], enderecos: [],
  dados: [
    { dominio: 'rh', campo: 'folha_nominal', valor: 'Servidor', fonte: 'SENIOR', atualizado_em: '2026-11-01T15:00:00Z' },
    { dominio: 'identificacao', campo: 'nome', valor: 'Ego Caio Ficticio', fonte: 'SENIOR', atualizado_em: '2026-11-01T15:00:00Z' },
  ],
}
const pessoa = (nome: string, id: string, extra: Record<string, unknown> = {}) => ({
  nome, cpf: '***.000.001-**', cidadao_id: id, nascimento: '1975-04-02',
  indicadores: {
    imovel: { tem: false, fontes: [], ficha: null },
    tumulo: { tem: false, fontes: [], ficha: null },
    empresa: { tem: false, fontes: [], ficha: null },
    ...extra,
  },
})
const arvore = {
  foco: pessoa('Ego Caio Ficticio', 'ego'),
  ascendentes: [
    { nivel: 1, via: null, pessoa: pessoa('Mae Rosa Ficticia', 'mae', { empresa: { tem: true, fontes: ['ISS'], ficha: '/simulador?cid=mae#area-empresas' } }),
      arestas: [{ base: 'SENIOR', forca: 'forte', parentesco: 'mãe', confianca: 1, criterio: 'r036dep', fonte_atualizado_em: '2026-11-01T15:00:00Z', provavel: false },
        { base: 'EDUCACAO', forca: 'forte', parentesco: 'mãe', confianca: 1, criterio: 'pes_idFiliacaoMae', fonte_atualizado_em: '2026-11-01T15:00:00Z', provavel: false }] },
    { nivel: 2, via: 'Mae Rosa Ficticia', pessoa: pessoa('Avo Lina Ficticia', 'avo'),
      arestas: [{ base: 'SAUDE', forca: 'fraco', parentesco: 'mãe', confianca: 0.85, criterio: 'nome normalizado idêntico + diferença de idade compatível', fonte_atualizado_em: '2026-11-01T00:00:00Z', provavel: true }] },
  ],
  descendentes: [
    { nivel: 1, via: null, pessoa: pessoa('Filho Davi Ficticio', 'filho', { imovel: { tem: true, fontes: ['IPTU'], ficha: '/simulador?cid=filho#area-imoveis' }, tumulo: { tem: true, fontes: ['CEMITERIO'], ficha: '/simulador?cid=filho#area-tumulos' } }),
      arestas: [{ base: 'SENIOR', forca: 'forte', parentesco: 'filho(a)', confianca: 1, criterio: 'r036dep', fonte_atualizado_em: '2026-11-01T15:00:00Z', provavel: false }] },
    { nivel: 2, via: 'Filho Davi Ficticio', pessoa: pessoa('Neto Enzo Ficticio', 'neto', { empresa: { tem: true, fontes: ['SIL'], ficha: '/simulador?cid=neto#area-empresas' } }),
      arestas: [{ base: 'EDUCACAO', forca: 'forte', parentesco: 'filho(a)', confianca: 1, criterio: 'pes_idFiliacaoMae', fonte_atualizado_em: null, provavel: false }] },
  ],
  laterais: [],
  provedores: [{ codigo: 'SENIOR', disponivel: true, motivo: null }, { codigo: 'CADUNICO', disponivel: false, motivo: 'sem acesso' }, { codigo: 'BU_EDUCACAO', disponivel: false, motivo: 'sem acesso' }],
  criterio_vinculo_fraco: 'Confiança 0,70 se o nome normalizado é idêntico.',
  isento_k_anonimato: true,
}
const vazio = { cidadao_id: 'ego', totais: { imoveis: 0, em_aberto: 0, parcelas_em_aberto: 0, vencido: 0, parcelas_vencidas: 0, divida_ativa: 0, inscricoes_divida: 0, protestada: false, lancamentos: 0 }, imoveis: [], lancamentos: [], dividas: [], empresas: [] }

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('árvore na ficha', () => {
  it('mostra dois níveis, vínculo forte e provável, procedência e links', async () => {
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      const corpo = u.includes('/genealogia') ? arvore
        : u.includes('/tributario') ? vazio
        : u.includes('/saude') ? { cidadao_id: 'ego', totais: { atendimentos: 0, vacinas: 0 }, proximos: [], atendimentos: [], vacinas: [] }
        : u.includes('/educacao') ? { cidadao_id: 'ego', alunos: [] }
        : u.includes('/cemiterio') ? { cidadao_id: 'ego', titular: [], autorizado: [], leilao: [], sepultamento: [] }
        : u.includes('/linha-do-tempo') ? { eventos: [] }
        : u.includes('/fontes') ? { carregadas: {}, nao_integradas: [] }
        : u.includes('/acessos') ? { acessos: [] }
        : ficha
      return new Response(JSON.stringify(corpo), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    render(<MemoryRouter initialEntries={['/simulador?cid=ego']}><AuthProvider><Simulador /></AuthProvider></MemoryRouter>)
    const arv = await screen.findByTestId('arvore-genealogica')
    expect(await within(arv).findByRole('heading', { name: 'Ascendentes' })).toBeVisible()
    expect(within(arv).getByRole('heading', { name: 'Descendentes' })).toBeVisible()
    expect(within(arv).getByText('Mae Rosa Ficticia')).toBeVisible()
    expect(within(arv).getByText('Avo Lina Ficticia')).toBeVisible()
    expect(within(arv).getByText('Nível 2 · via Mae Rosa Ficticia')).toBeVisible()
    expect(within(arv).getByText('Filho Davi Ficticio')).toBeVisible()
    expect(within(arv).getByText('Neto Enzo Ficticio')).toBeVisible()
    expect(within(arv).getByText('provável · 85%')).toBeVisible()
    expect(within(arv).getAllByText('forte').length).toBeGreaterThanOrEqual(2)
    expect(within(arv).getAllByText(/Senior · atualizado em/).length).toBeGreaterThan(0)
    expect(within(arv).getAllByText(/Educação · atualizado em/).length).toBeGreaterThan(0)
    expect(within(arv).getByText(/Saúde · atualizado em/)).toBeVisible()
    expect(within(arv).getByRole('link', { name: /Imóvel \(IPTU\)/ })).toHaveAttribute('href', '/simulador?cid=filho#area-imoveis')
    expect(within(arv).getByRole('link', { name: /Túmulo \(CEMITERIO\)/ })).toHaveAttribute('href', '/simulador?cid=filho#area-tumulos')
    expect(within(arv).getByRole('link', { name: /Empresa \(SIL\)/ })).toHaveAttribute('href', '/simulador?cid=neto#area-empresas')
    expect(within(arv).getByRole('link', { name: /Empresa \(ISS\)/ })).toHaveAttribute('href', '/simulador?cid=mae#area-empresas')
    expect(within(arv).getByText(/CadÚnico e BU educação/)).toBeVisible()
    expect(screen.getByText('Folha nominal')).toBeVisible()
    expect(screen.getAllByText(/Senior · atualizado em/).length).toBeGreaterThan(0)
  })
})
