import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { formata, plural } from '../explorar'

// Somente dados sintéticos, já mascarados como o backend devolve.
const CPF = '52998224725'
const CID = '400d13f0-e1ab-53be-a504-157cd444d410'
const EST = { processando: false, etapa: null, mensagem: null, ultima_carga: '2026-10-26T12:00:00Z' }
const linhas = (pag: number) => Array.from({ length: pag < 3 ? 50 : 20 }, (_, i) => ({
  id: (pag - 1) * 50 + i + 1, id_origem: `P${(pag - 1) * 50 + i}`, extraido_em: '2026-10-26T12:00:00Z', cpf: '***.982.247-**', nome: 'Ful*** S***', email: 'f***@exemplo.test',
}))
type Rota = (c: Record<string, unknown>) => [number, unknown]
type Chamada = { url: string; corpo: Record<string, unknown> }
const TAB = (camada: string, tabela = 'Pacientes', n = 120) => ({ camada, tabelas: [{ tabela, linhas: n, ultima_carga: '2026-10-26T12:00:00Z' }], total: n, estado: EST, vazia: n === 0, mensagem_vazia: null })

function mock(over: Partial<Record<string, Rota>> = {}, revelar = false) {
  const chamadas: Chamada[] = []
  const padrao: Record<string, Rota> = {
    conectores: () => [200, { conectores: [{ conector: 'EXSINT', estado: EST }], pode_revelar: revelar }],
    funil: () => [200, { bronze: 120, bronze_unicos: 120, quarentena: 0, prata: 1, prata_cidadaos: 1, ouro: 1, estado: EST, quarentena_disponivel: false,
      reconciliacao: { origem: 122, bronze: 120, quarentena: 2, ok: true, status: 'ok_com_pendencias' },
      descartes: [{ etapa: 'Bronze → Prata', motivo: 'obito_ambiguo', rotulo: 'Óbito com mais de um candidato', quantidade: 1 },
        { etapa: 'Bronze → Prata', motivo: 'em_quarentena', rotulo: 'Chave nula na origem', quantidade: 2 }] }],
    tabelas: (c) => c.camada === 'ouro' ? [200, TAB('ouro', 'cidadao_dados', 1)] : c.camada === 'prata' ? [200, TAB('prata', 'vinculos', 1)] : [200, TAB('bronze')],
    perfil: () => [200, { tabela: 'Pacientes', linhas: 120, aprox: true, amostra_perfil: 100, colunas: [
      { coluna: 'cpf', tipo: 'Texto', tipo_tecnico: 'json:string', preenchido_pct: 0.8, nulo_pct: 99.2, distintos: 1, aprox: true, exemplos: ['***.982.247-**'], sensivel: true },
      { coluna: 'email', tipo: 'Texto', tipo_tecnico: 'json:string', preenchido_pct: 100, nulo_pct: 0, distintos: 1, aprox: true, exemplos: ['f***@exemplo.test'], sensivel: true }] }],
    amostra: (c) => {
      if (c.somente_pendencias) return [200, { tabela: 'pendencias', colunas: ['motivo', 'tabela', 'chave', 'data_carga'], id_coluna: 'chave', cidadao_coluna: null, colunas_sensiveis: [],
        linhas: [{ motivo: 'Óbito com mais de um candidato', motivo_codigo: 'obito_ambiguo', tabela: 'Obitos', chave: 'P0***', data_carga: '2026-10-26T12:00:00Z' }], pagina: 1, por_pagina: 50, tem_anterior: false, tem_proxima: false }]
      if (c.camada === 'ouro') return [200, { tabela: 'cidadao_dados', colunas: ['id', 'cidadao_id', 'campo', 'valor'], id_coluna: 'id', cidadao_coluna: 'cidadao_id', colunas_sensiveis: ['valor'],
        linhas: [{ id: 1, cidadao_id: CID, campo: 'data_obito', valor: '••• (oculto)' }], pagina: 1, por_pagina: 50, tem_anterior: false, tem_proxima: false }]
      const p = Number(c.pagina)
      return [200, { tabela: 'Pacientes', colunas: ['id', 'id_origem', 'cpf', 'nome', 'email'], id_coluna: 'id', cidadao_coluna: null, colunas_sensiveis: ['cpf', 'nome', 'email'],
        linhas: linhas(p), pagina: p, por_pagina: 50, tem_anterior: p > 1, tem_proxima: p < 3 }]
    },
    linhagem: () => [200, { cidadao_id: CID, prata: [], identificadores: [], ouro: [], veio_de: [
      { conector: 'EXSINT', tabela: 'Pacientes', chave: 'P0***', camada: 'bronze', abrir: { camada: 'bronze', tabela: 'Pacientes', filtros: { id: '7' } } }] }],
    revelar: () => [200, { valor: CPF, remascarar_em_segundos: 60 }],
  }
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const rota = url.replace('/api/explorar/', '')
    const corpo = init?.body ? JSON.parse(String(init.body)) : {}
    chamadas.push({ url, corpo })
    expect(init?.method).toBe('POST')
    const [s, b] = (over[rota] ?? padrao[rota] ?? (() => [404, {}]))(corpo)
    return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return chamadas
}
const montar = (perfil: string) => {
  salvaSessao({ token: 't', perfil, nome: 'T' })
  return render(<MemoryRouter initialEntries={['/explorar']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}
const abaAmostra = async (u: ReturnType<typeof userEvent.setup>) => { await u.click(await screen.findByRole('tab', { name: 'Amostra' })) }

beforeEach(() => sessionStorage.clear())
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('Explorar dados', () => {
  it('UX-E8/EX6: operador vê "sem acesso" com Voltar ao painel e nenhuma chamada', async () => {
    const ch = mock()
    montar('operador')
    expect(screen.queryByRole('link', { name: 'Explorar dados' })).toBeNull()
    expect(await screen.findByText('Você não tem acesso a esta área.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Voltar ao painel' })).toHaveAttribute('href', '/dashboard')
    expect(ch.filter((c) => c.url.includes('/explorar/'))).toHaveLength(0)
  })

  it('UX-E7: erro mostra "Tentar de novo" e refaz a mesma chamada', async () => {
    let n = 0
    const ch = mock({ conectores: () => (++n === 1 ? [500, {}] : [200, { conectores: [{ conector: 'EXSINT', estado: EST }], pode_revelar: false }]) })
    montar('admin')
    expect(await screen.findByText(/Não foi possível carregar os dados\./)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Tentar de novo' }))
    expect(await screen.findByRole('tab', { name: /Bronze/ })).toBeInTheDocument()
    expect(ch.filter((c) => c.url.endsWith('/conectores'))).toHaveLength(2)
  })

  it('EX1/UX-E1: funil com rótulo do motivo (código no tooltip), abas com linhas, perfil aprox. e mascarado, rodapé', async () => {
    mock()
    montar('admin')
    const motivo = await screen.findByRole('button', { name: 'Óbito com mais de um candidato' })
    expect(motivo).toHaveAttribute('title', 'obito_ambiguo')
    expect(screen.getByRole('heading', { name: 'Bronze → Prata' })).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: /Bronze.*120 linhas.*última carga 26\/10\/2026/ })).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: /Prata.*1 linha ·/ })).toBeInTheDocument()
    expect((await screen.findAllByText('f***@exemplo.test')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('aprox.').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Texto')[0]).toHaveAttribute('title', 'json:string')
    expect(document.body.textContent).not.toContain(CPF)
    expect(screen.getByText(/Visualização somente leitura · dados do SIGPMF/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Chave nula na origem' })).toHaveAttribute('title', 'em_quarentena')
    expect(screen.getByRole('note')).toHaveTextContent('Reconciliação: origem 122 = bronze 120 + em quarentena 2 · ok com pendências')
    expect(screen.queryByText(/ainda não disponível/)).toBeNull()
    expect(screen.queryByText(/csv|exportar/i)).toBeNull()
  })

  it('UX: cabeçalho da coluna quarentena da Amostra é "Quarentena"', async () => {
    mock({ amostra: () => [200, { tabela: 'Leilao', colunas: ['id', 'id_origem', 'quarentena'], id_coluna: 'id', cidadao_coluna: null, colunas_sensiveis: [],
      linhas: [{ id: 1, id_origem: 'Z1', quarentena: 'chave_nula' }], pagina: 1, por_pagina: 50, tem_anterior: false, tem_proxima: false }] })
    montar('admin')
    const u = userEvent.setup()
    await abaAmostra(u)
    expect(await screen.findByRole('columnheader', { name: 'Quarentena' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'quarentena' })).toBeNull()
  })

  it('UX-E9: abas de camada navegam com as setas', async () => {
    mock()
    montar('admin')
    const bronze = await screen.findByRole('tab', { name: /Bronze/ })
    bronze.focus()
    fireEvent.keyDown(bronze, { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: /Prata/ })).toHaveAttribute('aria-selected', 'true')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: /Prata/ }))
  })

  it('EX2/UX-E2: paginação no servidor, "Página X", filtro exato por POST', async () => {
    const ch = mock()
    montar('admin')
    const u = userEvent.setup()
    await abaAmostra(u)
    await screen.findByText('P0')
    expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled()
    await u.click(screen.getByRole('button', { name: 'Próxima' }))
    await screen.findByText('P50')
    expect(screen.getByText('Página 2', { selector: 'span' })).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: 'Próxima' }))
    await screen.findByText('P100')
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled()
    const form = screen.getByRole('form', { name: 'Filtros' })
    const campo = within(form).getByLabelText('id_origem')
    expect(campo).toHaveAttribute('placeholder', 'valor exato')
    await u.type(campo, 'P7')
    await u.click(within(form).getByRole('button', { name: 'Filtrar' }))
    await waitFor(() => expect(ch.some((c) => (c.corpo.filtros as Record<string, string>)?.id_origem === 'P7')).toBe(true))
    expect(ch.every((c) => !c.url.includes('?') && !c.url.includes('P7'))).toBe(true)
  })

  it('filtro em coluna pessoal: admin vê campo desabilitado com cadeado e tooltip LGPD', async () => {
    mock()
    montar('admin')
    const u = userEvent.setup()
    await abaAmostra(u)
    const form = await screen.findByRole('form', { name: 'Filtros' })
    const cpf = await within(form).findByLabelText(/^cpf/)
    expect(cpf).toHaveAttribute('aria-disabled', 'true')
    expect(cpf).toHaveAttribute('readonly')
    expect(cpf).toHaveAccessibleDescription('Filtro disponível só para o perfil LGPD')
    expect(within(form).getAllByText('Filtro disponível só para o perfil LGPD')).toHaveLength(3) // visível: cpf, nome, email
    await userEvent.setup().type(cpf, '123')
    expect(cpf).toHaveValue('')
    expect(within(form).getByLabelText('id_origem')).not.toHaveAttribute('aria-disabled')
    expect(screen.queryByRole('button', { name: /Revelar/ })).toBeNull()
  })

  it('filtro em coluna pessoal: admin_lgpd precisa de motivo, que vai no POST', async () => {
    const ch = mock({}, true)
    montar('admin_lgpd')
    const u = userEvent.setup()
    await abaAmostra(u)
    const form = await screen.findByRole('form', { name: 'Filtros' })
    await u.type(await within(form).findByLabelText('cpf'), CPF)
    const filtrar = within(form).getByRole('button', { name: 'Filtrar' })
    expect(filtrar).toBeDisabled()
    await u.type(within(form).getByLabelText(/Motivo do filtro/), 'Atendimento chamado 42')
    await u.click(filtrar)
    await waitFor(() => expect(ch.some((c) => c.corpo.motivo_filtro === 'Atendimento chamado 42' && (c.corpo.filtros as Record<string, string>).cpf === CPF)).toBe(true))
  })

  it('UX-E3: admin_lgpd revela com motivo, contagem regressiva visível e remascara em 60 s', async () => {
    const ch = mock({}, true)
    montar('admin_lgpd')
    const u = userEvent.setup()
    await abaAmostra(u)
    const botoes = await screen.findAllByRole('button', { name: 'Revelar cpf' })
    await u.click(botoes[0])
    const dlg = screen.getByRole('dialog')
    const ok = within(dlg).getByRole('button', { name: 'Revelar' })
    await u.type(within(dlg).getByLabelText('Motivo'), 'curto')
    expect(ok).toBeDisabled()
    await u.type(within(dlg).getByLabelText('Motivo'), ' demais chamado 1')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await u.click(ok)
    expect(await screen.findByText(CPF)).toBeInTheDocument()
    expect(screen.getByText(/volta a mascarar em 60s/)).toBeInTheDocument()
    expect(ch.find((c) => c.url.endsWith('/revelar'))?.corpo.motivo).toBe('curto demais chamado 1')
    await act(async () => { vi.advanceTimersByTime(61_000) })
    await waitFor(() => expect(screen.queryByText(CPF)).toBeNull())
  })

  it('EX4/UX-E5: clicar no motivo do funil abre Pendências da Prata com filtro e rótulo', async () => {
    const ch = mock()
    montar('admin')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('button', { name: 'Óbito com mais de um candidato' }))
    expect(await screen.findByRole('tab', { name: 'Pendências' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(ch.some((c) => c.corpo.somente_pendencias === true && (c.corpo.filtros as Record<string, string>).motivo === 'obito_ambiguo')).toBe(true))
    const cel = await screen.findByText('Óbito com mais de um candidato', { selector: 'td span' })
    expect(cel).toHaveAttribute('title', 'obito_ambiguo')
    for (const h of ['Motivo', 'Tabela', 'Chave', 'Data da carga']) expect(screen.getByRole('columnheader', { name: h })).toBeInTheDocument()
    const sel = screen.getByRole('combobox', { name: 'Motivo' })
    expect(sel).toHaveValue('obito_ambiguo')
    expect(within(sel).getByRole('option', { name: 'CPF de terceiro inválido' })).toBeInTheDocument()
    await u.selectOptions(sel, 'em_quarentena')
    await u.click(screen.getByRole('button', { name: 'Filtrar' }))
    await waitFor(() => expect(ch.some((c) => (c.corpo.filtros as Record<string, string>)?.motivo === 'em_quarentena')).toBe(true))
    expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled()
    expect(screen.getByText('26/10/2026 09:00')).toBeInTheDocument()
    expect(document.body.textContent?.toLowerCase()).not.toContain('ignorad')
  })

  it('EX5/UX-E6: Ouro: UUID curto com tooltip, óbito mascarado e "Veio de" leva à linha na Bronze', async () => {
    const ch = mock()
    montar('admin')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('tab', { name: /Ouro/ }))
    await abaAmostra(u)
    const uuid = await screen.findByText('400d13f0…')
    expect(uuid).toHaveAttribute('title', CID)
    expect(screen.getByRole('button', { name: 'Copiar identificador' })).toBeInTheDocument()
    expect(screen.getByText('••• (oculto)')).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: 'origem' }))
    const ver = await screen.findByRole('button', { name: 'Ver linha na Bronze' })
    expect(screen.getByRole('table', { name: 'Veio de' })).toBeInTheDocument()
    await u.click(ver)
    await waitFor(() => expect(ch.some((c) => c.corpo.conector === 'EXSINT' && c.corpo.camada === 'bronze' && (c.corpo.filtros as Record<string, string>)?.id === '7')).toBe(true))
  })

  it('UX-E7: camada vazia; faixa "Processando… etapa X de 3 · parciais" só no funil, não repetida na camada', async () => {
    const PROC = { ...EST, processando: true, etapa: 'prata', etapa_num: 2, etapas_total: 3, atualizado_em: new Date(Date.now() - 5000).toISOString() }
    mock({ tabelas: (c) => c.camada === 'ouro'
      ? [200, { ...TAB('ouro', 'cidadao_dados', 0), estado: PROC }]
      : [200, { ...TAB(String(c.camada)), estado: PROC }],
    funil: () => [200, { bronze: 120, bronze_unicos: 120, quarentena: 0, prata: 1, prata_cidadaos: 1, ouro: 1, estado: PROC, quarentena_disponivel: false,
      reconciliacao: null, descartes: [] }] })
    montar('admin')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('tab', { name: /Ouro/ }))
    expect(await screen.findByText('Esta camada ainda não foi processada para esta tabela.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText(/Processando… etapa 2 de 3 · atualizado há \d+s · números parciais/)).toHaveLength(1))
    expect(within(screen.getByLabelText('Funil de qualidade')).getByText(/Processando… etapa 2 de 3/)).toBeInTheDocument()
  })

  it('formatação: datas dd/mm/aaaa [HH:mm], Sim/Não e plural', () => {
    const f = (v: unknown) => formata(v, (s) => `D:${s}`, (s) => `DH:${s}`)
    expect(f('1990-01-02')).toBe('D:1990-01-02')
    expect(f('2019-04-04T00:00:00-03:00')).toBe('D:2019-04-04')
    expect(f('2026-10-26T15:49:15-03:00')).toBe('DH:2026-10-26T15:49:15-03:00')
    expect(f(true)).toBe('Sim')
    expect(f('**/**/****')).toBe('**/**/****')
    expect(plural(1, 'linha', 'linhas')).toBe('1 linha')
    expect(plural(1200, 'linha', 'linhas')).toBe('1.200 linhas')
  })
})

it('CC9: Pendências da Prata abrem mesmo com a prata vazia (quarentena antes da prata)', async () => {
  const ch = mock({ tabelas: (c) => [200, c.camada === 'prata' ? TAB('prata', 'vinculos', 0) : TAB(String(c.camada))] })
  montar('admin')
  const u = userEvent.setup()
  await u.click(await screen.findByRole('button', { name: 'Chave nula na origem' }))
  expect(await screen.findByText('Esta camada ainda não foi processada para esta tabela.')).toBeInTheDocument()
  expect(await screen.findByRole('tab', { name: 'Pendências' })).toHaveAttribute('aria-selected', 'true')
  await waitFor(() => expect(ch.some((c) => c.corpo.somente_pendencias === true && (c.corpo.filtros as Record<string, string>).motivo === 'em_quarentena')).toBe(true))
})
