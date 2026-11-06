import { render, screen, within } from '@testing-library/react'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { estadoArea, type Fontes } from '../AreaFicha'
import { CemiterioCidadao, EducacaoCidadao, SaudeCidadao } from '../FichaPortal'
import Simulador from '../pages/Simulador'

const tudo: Fontes = { carregadas: { vacinas: true, consultas: true, agendamentos: true, imoveis: true, lancamentos: true, dividas: true, empresas: true, educacao: true, cemiterio: true }, nao_integradas: ['Protocolo'] }
const ficha = { cidadao_id: 'c1', nome: 'Pessoa Ficticia', cpf: '***.456.789-**', cns: null, ve_cadastro: true, sistemas: ['SAUDE'], enderecos: [],
  dados: [{ dominio: 'identificacao', campo: 'data_nascimento', valor: '1980-02-03', fonte: 'SAUDE', atualizado_em: null }] }
const tribVazio = { cidadao_id: 'c1', totais: { imoveis: 0, em_aberto: 0, parcelas_em_aberto: 0, vencido: 0, parcelas_vencidas: 0, divida_ativa: 0, inscricoes_divida: 0, protestada: false, lancamentos: 0 }, imoveis: [], lancamentos: [], dividas: [], empresas: [] }
const saudeVac = { cidadao_id: 'c1', totais: { atendimentos: 0, vacinas: 7 }, proximos: [], atendimentos: [], vacinas: Array.from({ length: 7 }, (_, i) => ({ data: `2026-0${i + 1}-10`, vacina: `Vacina ${i}`, dose: 'D1', unidade: 'UBS' })) }
const cemVazio = { cidadao_id: 'c1', titular: [], autorizado: [], leilao: [], sepultamento: [] }

function rotas(extra: Record<string, [number, unknown]> = {}) {
  const base: Record<string, [number, unknown]> = { '/fontes': [200, tudo], '/tributario': [200, tribVazio], '/saude': [200, saudeVac], '/educacao': [200, { cidadao_id: 'c1', alunos: [] }],
    '/cemiterio': [200, cemVazio], '/linha-do-tempo': [200, { eventos: [] }], ...extra }
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const k = Object.keys(base).find((x) => u.includes(x))
    const [s, c] = k ? base[k] : [200, ficha]
    return new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } })
  }))
}
const abre = () => render(<MemoryRouter initialEntries={['/simulador?cid=c1']}><AuthProvider><Simulador /></AuthProvider></MemoryRouter>)
const mapa = async (nome: string) => { await screen.findByText('Visão do cidadão · esta consulta fica registrada na auditoria'); return within(screen.getByRole('region', { name: 'O que existe para este cidadão' })).findByRole('button', { name: new RegExp(`^${nome}`) }) }

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin_lgpd', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('estadoArea: "não tem" só quando dá para afirmar', () => {
  it('cada caso', () => {
    expect(estadoArea('ok', 2, tudo, 'vacinas')).toBe('tem')
    expect(estadoArea('ok', 0, tudo, 'vacinas')).toBe('nao_tem')
    expect(estadoArea('ok', 0, { ...tudo, carregadas: {} }, 'vacinas')).toBe('sem_base')
    expect(estadoArea('ok', 2, { ...tudo, carregadas: {} }, 'vacinas')).toBe('tem')
    expect(estadoArea('ok', 0, false, 'vacinas')).toBe('falha')
    expect(estadoArea('ok', 0, null, 'vacinas')).toBe('carregando')
    expect(estadoArea('sem_acesso', 0, tudo, 'vacinas')).toBe('sem_acesso')
    expect(estadoArea('falha', 0, tudo, 'vacinas')).toBe('falha')
  })
})

describe('ficha com todas as áreas', () => {
  it('mostra o que tem e o que não tem, sem violação de acessibilidade', async () => {
    rotas()
    const { container } = abre()
    expect(await mapa('Vacinas')).toHaveTextContent('7 doses')
    expect(await mapa('Consultas')).toHaveTextContent('Não tem')
    expect(await mapa('Túmulos')).toHaveTextContent('Não tem')
    expect(await mapa('Dívida ativa')).toHaveTextContent('Não tem')
    expect(await mapa('Educação')).toHaveTextContent('Não tem')
    expect(await screen.findByText('Nenhuma consulta registrada para este cidadão.')).toBeVisible()
    expect(screen.getByText('Nenhum túmulo ligado ao CPF deste cidadão.')).toBeVisible()
    expect(screen.getByText('Nenhuma dívida ativa para este cidadão.')).toBeVisible()
    expect(screen.getByText('Nenhuma matrícula na rede municipal para este cidadão.')).toBeVisible()
    expect(screen.getAllByText('Não informado na origem').length).toBeGreaterThanOrEqual(4)
    expect(screen.getByText(/Ainda não integradas ao SIGPMF.*Protocolo/)).toBeVisible()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('403 vira "Sem acesso", nunca "Não tem"', async () => {
    rotas({ '/cemiterio': [403, { detail: 'x' }] })
    abre()
    expect(await mapa('Túmulos')).toHaveTextContent('Sem acesso')
    expect((await screen.findAllByText(/Seu perfil não tem acesso a esta área/)).length).toBe(2)
    expect(screen.queryByText('Nenhum túmulo ligado ao CPF deste cidadão.')).toBeNull()
  })
  it('base vazia vira "Base não carregada"; erro vira "Erro de leitura"', async () => {
    rotas({ '/fontes': [200, { ...tudo, carregadas: { ...tudo.carregadas, cemiterio: false } }], '/educacao': [500, { detail: 'x' }] })
    abre()
    expect(await mapa('Túmulos')).toHaveTextContent('Base não carregada')
    expect(await mapa('Educação')).toHaveTextContent('Erro de leitura')
    expect(screen.queryByText('Nenhum túmulo ligado ao CPF deste cidadão.')).toBeNull()
  })
})

describe('blocos com registro', () => {
  it('saúde mostra 5 e abre o resto', async () => {
    render(<SaudeCidadao saude={saudeVac} leitura="ok" fontes={tudo} />)
    expect(within(screen.getByRole('list', { name: 'Vacinas' })).getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Ver todos (7)' })).toBeVisible()
  })
  it('cemitério: titular, autorizado, leilão e sepultamento', () => {
    const l = { cemiterio: 'Municipal', quadra: 'Q1', sepultura: 'S1' }
    render(<CemiterioCidadao leitura="ok" fontes={tudo} cemiterio={{ cidadao_id: 'c1', titular: [{ ...l, ativo: true }, { ...l, sepultura: 'S2', ativo: false }], autorizado: [{ ...l, parentesco: 'Filho' }],
      leilao: [{ ...l, data_venda: '2024-02-02', data_liberacao: null }], sepultamento: [{ ...l, gaveta: '1', lado: 'A', data_morte: '2020-01-01', data_sepultamento: '2020-01-02' }] }} />)
    const t = within(screen.getByRole('list', { name: 'Túmulos' }))
    expect(t.getByText('Titular')).toBeVisible()
    expect(t.getByText('Foi titular')).toBeVisible()
    expect(t.getByText('Autorizado')).toBeVisible()
    expect(t.getByText('Comprado em leilão')).toBeVisible()
    expect(screen.getByText(/sepultado em 02\/01\/2020/)).toBeVisible()
  })
  it('educação: matrícula atual, histórico e aviso de menor', () => {
    const m = { escola: 'EM Fictícia', unidade: null, turma: '5A', inativa: false, data_alteracao: null }
    render(<EducacaoCidadao leitura="ok" fontes={tudo} educacao={{ cidadao_id: 'c1', alunos: [{ menor: true, mascarado: true, inativo: false, matricula_atual: m, historico: [m, { ...m, turma: '4A', inativa: true }], responsaveis: [{ tipo: 'M', nome: 'M** F***', principal: true }] }] }} />)
    expect(screen.getByText('EM Fictícia · turma 5A')).toBeVisible()
    expect(screen.getByText('EM Fictícia · turma 4A')).toBeVisible()
    expect(screen.getByText(/dados pessoais mascarados/)).toBeVisible()
  })
})
