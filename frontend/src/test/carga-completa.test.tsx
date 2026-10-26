import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

const Z = { bronze: 0, prata: 0, ouro: 0, pendentes: 0 }
const SAUDE = {
  id: 1, nome: 'Saude', descricao: null, grupo: 'painel_cidadao', triagem: 'schema', status: 'implementado', prioridade: 1,
  conector_id: 'saude', tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: true, com_erro: false,
  contagens: { ...Z, bronze: 13 }, ultima_carga: null, completa_disponivel: true, completa_liberada: true, completa_motivo: null,
}
const resp = (b: object) => ({ conectores: [], execucoes: [], bases: [{ ...SAUDE, ...b }], totais: {}, carga_habilitada: false })
const EX = { id: 5, status: 'em_andamento', usuario: 'admin@aion.local', iniciado_em: '2026-10-26T15:00:00Z', finalizado_em: null,
  lidos: 4, novos: 4, atualizados: 0, ignorados: 0, lotes: 2, gravados: 4, total_origem: 13, processados: 4,
  tabela_atual: 'pacientes', pausa_motivo: null, cancelamento_pedido: false, mensagem: null, erro: null }
const sit = (o: object = {}) => ({ conector: 'saude', liberada: true, motivo: null, execucao: null,
  camadas: { bronze: 13, prata: 9, pendencias: 1 }, reconciliacao: null, ...o })

type Rota = (url: string, init?: RequestInit) => [number, unknown]
function mock(rota: Rota) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const [s, c] = rota(url, init)
    return new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
const montar = (perfil: string, rota = '/conectores') => {
  salvaSessao({ token: 't', perfil, nome: 'T' })
  return render(<MemoryRouter initialEntries={[rota]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}
const cartao = async () => (await screen.findByRole('heading', { name: /^Saude/ })).closest('li') as HTMLElement

beforeEach(() => sessionStorage.clear())
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('carga completa (CC1-CC6)', () => {
  it('CC1: habilitada para admin com amostra ok; desabilitada com motivo visível e tooltip', async () => {
    mock((u) => [200, u.endsWith('/conectores') ? resp({}) : sit()])
    const { unmount } = montar('admin')
    const b = within(await cartao()).getByRole('button', { name: 'Puxar carga completa de Saúde' })
    await waitFor(() => expect(b).toBeEnabled())
    unmount()
    const motivo = 'Rode uma amostra com sucesso primeiro'
    mock((u) => [200, u.endsWith('/conectores') ? resp({ completa_liberada: false, completa_motivo: motivo }) : sit({ liberada: false, motivo })])
    montar('admin')
    const c = await cartao()
    const b2 = within(c).getByRole('button', { name: 'Puxar carga completa de Saúde' })
    await waitFor(() => expect(b2).toBeDisabled())
    expect(b2).toHaveAttribute('title', motivo)
    expect(b2).toHaveAccessibleDescription(motivo)
    expect(within(c).getByText(motivo)).toBeVisible()
  })

  it('CC5: perfis não admin não veem o botão', async () => {
    mock((u) => [200, u.endsWith('/conectores') ? resp({}) : sit()])
    montar('operador')
    const c = await cartao()
    expect(within(c).queryByRole('button', { name: /carga completa/i })).toBeNull()
  })

  it('CC2/CC3/CC6: confirmação com estimativa, progresso com contagem, cancelar, card final e toast', async () => {
    let estado: object = sit()
    const f = mock((u, init) => {
      if (u.endsWith('/conectores')) return [200, resp({})]
      if (u.endsWith('/estimativa')) return [200, { tabelas: { pacientes: 7, Pacientes_CNS: 4, ESUS_Paciente_Inconsistencia: 2 }, total: 13 }]
      if (u.endsWith('/completa') && init?.method === 'POST') { estado = sit({ execucao: EX }); return [202, estado] }
      if (u.endsWith('/cancelar')) { estado = sit({ execucao: { ...EX, cancelamento_pedido: true } }); return [200, estado] }
      return [200, estado]
    })
    const { container } = montar('admin')
    const c = await cartao()
    await userEvent.click(within(c).getByRole('button', { name: 'Puxar carga completa de Saúde' }))
    const d = await screen.findByRole('dialog', { name: 'Carregar todos os registros de Saúde? Isso pode levar alguns minutos.' })
    expect(await within(d).findByText('13')).toBeInTheDocument()
    expect(within(d).getByRole('button', { name: 'Cancelar' })).toBeInTheDocument()
    expect((await axe.run(container, { runOnly: ['wcag2a', 'wcag2aa'] })).violations).toEqual([])
    await userEvent.click(within(d).getByRole('button', { name: 'Iniciar carga completa' }))
    expect(f.mock.calls.some(([u, i]) => String(u).endsWith('/conectores/saude/completa') && i?.method === 'POST')).toBe(true)
    const st = await within(c).findByRole('status', { name: 'Andamento da carga completa de Saúde' })
    expect(st).toHaveTextContent('Leitura: 4 de 13')
    await userEvent.click(within(c).getByRole('button', { name: 'Cancelar carga' }))
    expect(await within(c).findByRole('button', { name: 'Cancelando…' })).toBeDisabled()
    // fim: card final com lidos, bronze, prata, pendências, reconciliação e toast
    estado = sit({ execucao: { ...EX, status: 'sucesso', lidos: 13, gravados: 13, processados: 13, finalizado_em: '2026-10-26T15:02:00Z' },
      reconciliacao: { pacientes: { origem: 7, bronze: 7, ok: true } } })
    await act(async () => { await new Promise((r) => setTimeout(r, 5100)) })
    const fim = await within(c).findByLabelText('Resultado da carga completa de Saúde')
    for (const t of ['Lidos', 'Gravados', 'Bronze', 'Prata', 'Pendências']) expect(within(fim).getByText(t)).toBeInTheDocument()
    expect(within(fim).getByText('Bronze bate com a origem')).toBeInTheDocument()
    expect(await screen.findByText(/Carga completa de Saúde concluída: 13 registros lidos/)).toBeInTheDocument()
  }, 15000)

  it('erro ao iniciar mostra o motivo e "Tentar de novo"; pausa mostra motivo e "Retomar carga"', async () => {
    let posts = 0
    mock((u, init) => {
      if (u.endsWith('/conectores')) return [200, resp({})]
      if (u.endsWith('/estimativa')) return [200, { tabelas: { pacientes: 7 }, total: 7 }]
      if (u.endsWith('/completa') && init?.method === 'POST') { posts++; return [409, { detail: 'Disco do servidor com 88% usado (limite 85%).' }] }
      return [200, sit({ execucao: { ...EX, status: 'pausada', pausa_motivo: 'Disco do servidor com 95% usado (limite 90%).' } })]
    })
    montar('admin')
    const c = await cartao()
    expect(await within(c).findByText(/Carga pausada: Disco do servidor com 95% usado/)).toBeInTheDocument()
    await userEvent.click(within(c).getByRole('button', { name: 'Retomar carga' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Iniciar carga completa' }))
    const al = await within(c).findByRole('alert')
    expect(al).toHaveTextContent('88% usado')
    await userEvent.click(within(al).getByRole('button', { name: 'Tentar de novo' }))
    expect(posts).toBe(1)  // "Tentar de novo" abre a confirmação, não dispara direto
    await userEvent.click(await screen.findByRole('button', { name: 'Iniciar carga completa' }))
    await waitFor(() => expect(posts).toBe(2))
  })

  it('UX: modal com foco no Cancelar e "Iniciar" desabilitado enquanto conta as linhas', async () => {
    let solta: (v: unknown) => void = () => {}
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.endsWith('/estimativa')) return new Promise((r) => { solta = r })
      const c = url.endsWith('/conectores') ? resp({}) : sit()
      return Promise.resolve(new Response(JSON.stringify(c), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))
    montar('admin')
    const c = await cartao()
    const b = within(c).getByRole('button', { name: 'Puxar carga completa de Saúde' })
    await waitFor(() => expect(b).toBeEnabled())
    await userEvent.click(b)
    const d = await screen.findByRole('dialog')
    expect(within(d).getByRole('button', { name: 'Cancelar' })).toHaveFocus()
    expect(within(d).getByText('Contando as linhas…')).toBeInTheDocument()
    expect(within(d).getByRole('button', { name: 'Iniciar carga completa' })).toBeDisabled()
    await act(async () => { solta(new Response(JSON.stringify({ tabelas: { pacientes: 7 }, total: 7 }), { status: 200, headers: { 'Content-Type': 'application/json' } })) })
    await waitFor(() => expect(within(d).getByRole('button', { name: 'Iniciar carga completa' })).toBeEnabled())
    expect(within(d).queryByText('Contando as linhas…')).toBeNull()
  })

  it('UX: falha da carga -> "Tentar de novo" abre a confirmação (sem POST)', async () => {
    const f = mock((u) => {
      if (u.endsWith('/conectores')) return [200, resp({})]
      if (u.endsWith('/estimativa')) return [200, { tabelas: { pacientes: 7 }, total: 7 }]
      return [200, sit({ execucao: { ...EX, status: 'falha', mensagem: 'Falha na carga completa de Saúde: timeout.' } })]
    })
    montar('admin')
    const c = await cartao()
    const al = await within(c).findByRole('alert')
    await userEvent.click(within(al).getByRole('button', { name: 'Tentar de novo' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(f.mock.calls.some(([, i]) => i?.method === 'POST')).toBe(false)
  })

  it('UX: estimativa 409 mostra o motivo; outra falha mostra a mensagem genérica', async () => {
    const motivo = 'Tabela pacientes: chave (Matricula) não é única na origem (8 linhas, 7 chaves distintas); a carga completa exige chave única.'
    let resposta: [number, unknown] = [409, { detail: motivo }]
    mock((u) => (u.endsWith('/conectores') ? [200, resp({})] : u.endsWith('/estimativa') ? resposta : [200, sit()]))
    montar('admin')
    const c = await cartao()
    const b = within(c).getByRole('button', { name: 'Puxar carga completa de Saúde' })
    await waitFor(() => expect(b).toBeEnabled())
    await userEvent.click(b)
    let d = await screen.findByRole('dialog')
    expect(await within(d).findByText(motivo)).toBeInTheDocument()
    expect(within(d).getByRole('button', { name: 'Iniciar carga completa' })).toBeDisabled()
    await userEvent.click(within(d).getByRole('button', { name: 'Cancelar' }))
    resposta = [502, { detail: 'Não foi possível contar as linhas da origem: timeout' }]
    await userEvent.click(b)
    d = await screen.findByRole('dialog')
    expect(await within(d).findByText('Não foi possível estimar as linhas da origem. Feche e tente de novo.')).toBeInTheDocument()
    expect(within(d).queryByText(/timeout/)).toBeNull()
  })

  it('UX: pausa sem motivo mostra só "Carga pausada"', async () => {
    mock((u) => [200, u.endsWith('/conectores') ? resp({}) : sit({ execucao: { ...EX, status: 'pausada', pausa_motivo: null } })])
    montar('admin')
    const c = await cartao()
    const st = await within(c).findByText(/^Carga pausada/)
    expect(st.textContent).toBe('Carga pausada')
  })
})

describe('indicador de progresso (CC7)', () => {
  const AGORA = new Date().toISOString()
  const PRATA = { ...EX, etapa: 'prata', etapa_nome: 'Tratando (prata)', processadas_etapa: 5000, total_etapa: 921378,
    atualizado_em: AGORA, sem_progresso: false, alerta_min: 5, status_texto: 'Em andamento · Carga completa', processados: 13 }
  const rodandoResp = (ex: object, base: object = {}) =>
    mock((u) => [200, u.endsWith('/conectores') ? resp({ completa_em_andamento: true, contagens: { ...Z, bronze: 921378, prata: 1000, pendentes: 1000 }, ...base }) : sit({ execucao: ex })])

  it('CC7.1/CC7.2: etapas visíveis, atual destacada, concluída com ✓, contador da prata e "Leitura concluída"', async () => {
    rodandoResp(PRATA)
    const { container } = montar('admin')
    const c = await cartao()
    const st = await within(c).findByRole('status', { name: 'Andamento da carga completa de Saúde' })
    const passos = within(st).getAllByRole('listitem').map((li) => li.textContent)
    expect(passos).toEqual(['✓ Lendo da origem (concluída)', 'Tratando (prata)', 'Unificando cidadãos (ouro)', 'Concluída'])
    expect(within(st).getByText('Tratando (prata)')).toHaveAttribute('aria-current', 'step')
    expect(st).toHaveTextContent('Prata: 5.000 de 921.378')
    expect(st).toHaveTextContent('Leitura concluída · tratando os dados')
    expect(st).toHaveTextContent('Em andamento · Carga completa')
    expect(st.textContent).not.toMatch(/Carregando… 13 de 13/)
    expect((await axe.run(container, { runOnly: ['wcag2a', 'wcag2aa'] })).violations).toEqual([])
  })

  it('CC7.4: em andamento não mostra percentual nem pendências (números parciais); nunca "−0%"', async () => {
    rodandoResp(PRATA)
    montar('admin')
    const c = await cartao()
    await within(c).findByRole('status', { name: 'Andamento da carga completa de Saúde' })
    expect(within(c).getAllByText('Em processamento, números parciais').length).toBeGreaterThan(0)
    expect(within(c).queryByText(/Pendências:/)).toBeNull()
    expect(within(c).queryByText(/%/)).toBeNull()
    expect(within(c).queryByRole('progressbar')).toBeNull()
  })

  it('CC7.3: sem progresso mostra aviso âmbar "Sem progresso desde HH:MM" com a última atividade', async () => {
    rodandoResp({ ...PRATA, sem_progresso: true, atualizado_em: '2026-10-26T17:32:00Z' })
    montar('admin')
    const c = await cartao()
    const al = await within(c).findByText(/Sem progresso desde/)
    expect(al).toHaveClass('aviso-ambar')
    expect(al).toHaveTextContent('Sem progresso desde 14:32 (mais de 5 min). Última atividade: Tratando (prata).')
    expect(within(c).getByText(/Última atividade: 14:32/)).toBeInTheDocument()
  })

  it('CC7.5: atualiza a cada 5 s sem recarregar, mostra decorrido e o erro de atualização', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let n = 0
    let falha = false
    const f = mock((u) => {
      if (u.endsWith('/conectores')) return [200, resp({ completa_em_andamento: true })]
      n++
      if (falha) return [500, { detail: 'erro interno' }]
      return [200, sit({ execucao: { ...PRATA, processadas_etapa: 5000 * n } })]
    })
    montar('admin')
    const c = await cartao()
    const st = await within(c).findByRole('status', { name: 'Andamento da carga completa de Saúde' })
    expect(st).toHaveTextContent(/Decorrido: .+ · Última atividade: \d\d:\d\d/)
    const antes = n
    await act(async () => { await vi.advanceTimersByTimeAsync(4900) })
    expect(n).toBe(antes)
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(n).toBe(antes + 1)
    await waitFor(() => expect(st).toHaveTextContent(`Prata: ${new Intl.NumberFormat('pt-BR').format(5000 * n)} de 921.378`))
    falha = true
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(await within(c).findByText(/Não foi possível atualizar o andamento da carga/)).toHaveAttribute('role', 'alert')
    expect(f).toHaveBeenCalled()
  })

  it('carga interrompida: mostra o aviso e "Retomar prata e ouro" chama só a retomada', async () => {
    let estado: object = sit({ execucao: { ...PRATA, status: 'interrompida', status_texto: 'Carga interrompida', finalizado_em: AGORA,
      mensagem: 'Carga interrompida: o servidor parou durante a carga.', pode_retomar_camadas: true } })
    const f = mock((u, init) => {
      if (u.endsWith('/conectores')) return [200, resp({})]
      if (u.endsWith('/retomar-camadas') && init?.method === 'POST') { estado = sit({ execucao: PRATA }); return [202, estado] }
      return [200, estado]
    })
    montar('admin')
    const c = await cartao()
    expect(await within(c).findByText('Carga interrompida')).toBeInTheDocument()
    await userEvent.click(within(c).getByRole('button', { name: 'Retomar prata e ouro' }))
    const d = await screen.findByRole('dialog', { name: 'Reprocessar prata e ouro a partir da bronze já carregada? A origem não será consultada.' })
    await userEvent.click(within(d).getByRole('button', { name: 'Reprocessar prata e ouro' }))
    expect(await within(c).findByRole('status', { name: 'Andamento da carga completa de Saúde' })).toHaveTextContent('Prata: 5.000 de 921.378')
    const posts = f.mock.calls.filter(([, i]) => i?.method === 'POST').map(([u]) => String(u))
    expect(posts).toEqual([expect.stringMatching(/\/conectores\/saude\/completa\/retomar-camadas$/)])
  })
})

describe('retomar prata e ouro (UX Designer)', () => {
  const INT = { ...EX, status: 'interrompida', status_texto: 'Carga interrompida', finalizado_em: '2026-10-26T18:00:00Z',
    mensagem: 'Carga interrompida: o servidor parou durante a carga.', pode_retomar_camadas: true, etapa: 'prata', etapa_nome: 'Tratando (prata)' }

  it('abre a confirmação; Cancelar não dispara nada', async () => {
    const f = mock((u) => [200, u.endsWith('/conectores') ? resp({}) : sit({ execucao: INT })])
    const { container } = montar('admin')
    const c = await cartao()
    await userEvent.click(await within(c).findByRole('button', { name: 'Retomar prata e ouro' }))
    const d = await screen.findByRole('dialog', { name: 'Reprocessar prata e ouro a partir da bronze já carregada? A origem não será consultada.' })
    expect(within(d).getByRole('button', { name: 'Cancelar' })).toHaveFocus()
    expect((await axe.run(container, { runOnly: ['wcag2a', 'wcag2aa'] })).violations).toEqual([])
    await userEvent.click(within(d).getByRole('button', { name: 'Cancelar' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(f.mock.calls.some(([, i]) => i?.method === 'POST')).toBe(false)
  })

  it('enquanto o pedido roda, o botão fica desabilitado com "Retomando…"', async () => {
    let solta: (v: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith('/retomar-camadas') && init?.method === 'POST') return new Promise<Response>((r) => { solta = r })
      const corpo = url.endsWith('/conectores') ? resp({}) : sit({ execucao: INT })
      return Promise.resolve(new Response(JSON.stringify(corpo), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))
    montar('admin')
    const c = await cartao()
    await userEvent.click(await within(c).findByRole('button', { name: 'Retomar prata e ouro' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Reprocessar prata e ouro' }))
    const b = await within(c).findByRole('button', { name: 'Retomando…' })
    expect(b).toBeDisabled()
    await act(async () => { solta(new Response(JSON.stringify(sit({ execucao: { ...INT, status: 'em_andamento' } })), { status: 202, headers: { 'Content-Type': 'application/json' } })) })
    await waitFor(() => expect(within(c).queryByRole('button', { name: 'Retomando…' })).toBeNull())
  })
})

describe('quarentena de chave nula (CC9)', () => {
  it('card: "Concluída com N linha(s) em quarentena (chave nula)", ok com pendências e detalhe sem valores da linha', async () => {
    const FIM = { ...EX, status: 'sucesso', lidos: 8, gravados: 7, processados: 8, finalizado_em: '2026-10-26T18:40:00Z' }
    const f = mock((u) => {
      if (u.endsWith('/conectores')) return [200, resp({})]
      if (u.endsWith('/quarentena')) return [200, { linhas: [{ id: 9, tabela: 'Leilao', motivo: 'Chave nula na origem',
        campos_nulos: ['DataVenda'], referencia: 'chave_nula:abc123', extraido_em: null }] }]
      return [200, sit({ execucao: FIM, quarentena: 1,
        reconciliacao: { Leilao: { origem: 8, bronze: 7, quarentena: 1, ok: true, status: 'ok_com_pendencias' } } })]
    })
    const { container } = montar('admin')
    const c = await cartao()
    const fim = await within(c).findByLabelText('Resultado da carga completa de Saúde')
    expect(within(fim).getByText('Concluída com 1 linha(s) em quarentena (chave nula)')).toBeInTheDocument()
    expect(within(fim).getByText('Bronze + quarentena batem com a origem (ok com pendências)')).toBeInTheDocument()
    expect(within(fim).queryByText(/ignorad/i)).toBeNull()
    await userEvent.click(within(fim).getByRole('button', { name: 'Ver detalhes da quarentena' }))
    const tab = await within(fim).findByRole('table', { name: 'Linhas em quarentena de Saúde' })
    expect(within(tab).getByText('DataVenda')).toBeInTheDocument()
    expect(within(tab).queryByText(/cpf/i)).toBeNull()
    expect(f.mock.calls.some(([u]) => String(u).endsWith('/conectores/saude/completa/quarentena'))).toBe(true)
    expect((await axe.run(container, { runOnly: ['wcag2a', 'wcag2aa'] })).violations).toEqual([])
  })
})

describe('recursos do servidor (RS1/RS2/RS5)', () => {
  const R = { lido_em: '2026-10-26T16:05:00Z', faixas: { amarelo: 70, vermelho: 85 }, limites: { inicio_carga: 85, pausa_carga: 90 },
    disco: { total_gb: 100, livre_gb: 12, usado_pct: 88, status: 'critico' },
    memoria: { total_gb: 16, livre_gb: 6.5, usado_pct: 59.4, status: 'ok' },
    banco_mb: { bronze: 812.4, silver: 120, gold: 33.3, controle: 2.1 } }
  const DASH = { sistemas: 0, bronze: { registros: 0 }, silver: { cidadaos: 0, pendentes: 0 }, ultima_execucao: null }

  it('admin vê barras com texto + %, GB, tamanho por camada e horário; atualiza a cada 30 s', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const f = mock((u) => [200, u.endsWith('/sistema/recursos') ? R : DASH])
    const { container } = montar('admin', '/dashboard')
    const card = (await screen.findByRole('heading', { name: 'Recursos do servidor' })).closest('section') as HTMLElement
    expect(await within(card).findByText(/Crítico/)).toBeInTheDocument()
    expect(within(card).getByText('Normal', { exact: false })).toBeInTheDocument()
    expect(within(card).getByText('12,0 GB livres de 100,0 GB no volume')).toBeInTheDocument()
    expect(within(card).getByText('6,5 GB livres de 16,0 GB no servidor')).toBeInTheDocument()
    expect(within(card).queryByText(/no container/)).toBeNull()
    expect(within(card).getByText('812,4 MB')).toHaveClass('num')
    expect(within(card).getByText(/Atualizado às/)).toHaveTextContent('Atualizado às 13:05')
    expect(within(card).getByRole('meter', { name: /Disco do banco: 88,0% usado/ })).toBeInTheDocument()
    expect((await axe.run(container, { runOnly: ['wcag2a', 'wcag2aa'] })).violations).toEqual([])
    const n = f.mock.calls.filter(([u]) => String(u).endsWith('/sistema/recursos')).length
    await act(async () => { vi.advanceTimersByTime(30000) })
    await waitFor(() => expect(f.mock.calls.filter(([u]) => String(u).endsWith('/sistema/recursos')).length).toBe(n + 1))
  })

  it('sem leitura: "Sem leitura do servidor" em cinza, nunca 0%; falha da rota idem', async () => {
    mock((u) => [200, u.endsWith('/sistema/recursos') ? { ...R, disco: null, memoria: null, banco_mb: null } : DASH])
    const { unmount } = montar('admin', '/dashboard')
    const card = (await screen.findByRole('heading', { name: 'Recursos do servidor' })).closest('section') as HTMLElement
    await waitFor(() => expect(within(card).getAllByText('Sem leitura do servidor').length).toBe(3))
    expect(card.textContent).not.toMatch(/0%|0,0%/)
    unmount()
    mock((u) => (u.endsWith('/sistema/recursos') ? [500, {}] : [200, DASH]))
    montar('admin', '/dashboard')
    const c2 = (await screen.findByRole('heading', { name: 'Recursos do servidor' })).closest('section') as HTMLElement
    await waitFor(() => expect(within(c2).getAllByText('Sem leitura do servidor').length).toBe(4))
  })

  it('não admin não vê o card', async () => {
    mock((u) => [200, u.endsWith('/sistema/recursos') ? R : DASH])
    montar('operador', '/dashboard')
    await screen.findByRole('heading', { name: 'Dashboard' })
    expect(screen.queryByRole('heading', { name: 'Recursos do servidor' })).toBeNull()
  })
})
