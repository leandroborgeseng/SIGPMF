import { act, render, screen, within } from '@testing-library/react'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PainelProgresso, { textoLote, type FonteProgresso, type Progresso, type RespProgresso } from '../PainelProgresso'
import { salvaSessao } from '../api'

const prog = (o: Partial<Progresso> = {}): Progresso => ({
  execucao_id: 1, modo: 'Carga completa', lote: 81, lotes_total: 240, percentual: 33, total_origem: 240000,
  origem_total: 'estimado pela última carga', linhas_processadas: 81000, iniciado_em: '2026-10-29T16:00:00Z',
  decorrido_s: 8040, calculando: false, previsao_termino: '2026-10-29T22:40:00Z', previsao_termino_brt: '19:40',
  ultimo_sinal: '2026-10-29T18:13:00Z', sem_sinal_min: 1, orfa_limite_min: 30, possivel_travada: false, ...o,
})
const fonte = (o: Partial<FonteProgresso> = {}): FonteProgresso => ({
  id: 'educacao', nome: 'Educação', status: 'rodando', status_texto: 'Rodando', motivo_codigo: null, motivo: null, falta: null,
  aguardando_em: null, aguardando_em_brt: null, progresso: prog(), ultima_execucao: null,
  tabelas: [{ tabela: 'ACA_Aluno', linhas_processadas: 92567, total_origem: null, estado: 'concluida' },
    { tabela: 'ZZTESTE_Vazia', linhas_processadas: 0, total_origem: null, estado: 'na_fila' }], ...o,
})
const resp = (fontes: FonteProgresso[], rodando = true): RespProgresso => ({ agora: '2026-10-29T18:14:00Z', rodando, atualizar_a_cada_s: 30, fontes })

function mock(seq: Array<[number, unknown]>) {
  let i = 0
  const f = vi.fn(async () => {
    const [s, c] = seq[Math.min(i++, seq.length - 1)]
    return new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('painel de progresso por fonte (PP1–PP11)', () => {
  it('PP1/PP2/PP3/PP5/PP6/PP4: card rodando com lote, origem do total, tempo, sinal e tabelas com 0', async () => {
    mock([[200, resp([fonte()])]])
    render(<PainelProgresso />)
    const card = await screen.findByRole('article', { name: 'Educação' })
    expect(within(card).getByText('Rodando')).toBeVisible()
    const barra = within(card).getByRole('progressbar')
    expect(barra).toHaveAttribute('aria-valuenow', '33')
    expect(within(card).getByText('Lote 81 de ~240 (33%)')).toBeVisible()
    expect(within(card).getByText('Total estimado: ~240.000 linhas (estimado pela última carga)')).toBeVisible()
    expect(within(card).getByText(/Rodando há 2 h 14 min · término previsto ~19:40 \(BRT\)/)).toBeVisible()
    expect(within(card).getByText('Último sinal há 1 min')).toBeVisible()
    expect(within(card).getByText('Ver tabelas (2)')).toBeVisible()
    expect(within(card).getByText('Linhas processadas: 0')).toBeInTheDocument()
    expect(screen.getByText(/atualizado às/)).toBeVisible()
  })

  it('PP2: total desconhecido -> barra indeterminada, sem percentual', async () => {
    mock([[200, resp([fonte({ progresso: prog({ lotes_total: null, percentual: null, origem_total: null, total_origem: null, previsao_termino_brt: null }) })])]])
    render(<PainelProgresso />)
    const card = await screen.findByRole('article', { name: 'Educação' })
    expect(within(card).getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
    expect(within(card).getByText('Lote 81 · total desconhecido')).toBeVisible()
    expect(card.textContent).not.toMatch(/%/)
  })

  it('PP5: antes do 3º lote mostra calculando…', async () => {
    mock([[200, resp([fonte({ progresso: prog({ lote: 2, calculando: true, previsao_termino_brt: null }) })])]])
    render(<PainelProgresso />)
    expect(await screen.findByText(/término previsto: calculando…/)).toBeVisible()
  })

  it('PP6: possível execução travada em destaque, status continua Rodando', async () => {
    mock([[200, resp([fonte({ progresso: prog({ sem_sinal_min: 34, possivel_travada: true }) })])]])
    render(<PainelProgresso />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Possível execução travada: sem sinal há 34 min.')
    expect(screen.getByText('Rodando')).toBeVisible()
  })

  it('PP7/PP10: motivo traduzido e SEI não liberada', async () => {
    mock([[200, resp([
      fonte({ id: 'sil', nome: 'SIL', status: 'pausada', status_texto: 'Pausada', motivo_codigo: 'agenda_desligada',
        motivo: 'Agenda desligada', progresso: null, tabelas: [] }),
      fonte({ id: 'sei', nome: 'SEI', status: 'nao_liberada', status_texto: 'Não liberada', progresso: null, tabelas: [],
        falta: ['Token do SEI não configurado', 'Reclassificação incremental pendente'] }),
      fonte({ id: 'saude', nome: 'Saúde', status: 'aguardando', status_texto: 'Aguardando', aguardando_em_brt: '01:00', progresso: null, tabelas: [] }),
      fonte({ id: 'iptu', nome: 'IPTU', status: 'nunca_rodou', status_texto: 'Nunca rodou', progresso: null, tabelas: [] }),
    ], false)]])
    render(<PainelProgresso />)
    expect(await screen.findByText('Motivo: Agenda desligada')).toBeVisible()
    expect(screen.getByText('Não liberada — falta: Token do SEI não configurado / Reclassificação incremental pendente')).toBeVisible()
    expect(screen.getByText(/Aguardando · próxima às 01:00 \(BRT\)/)).toBeVisible()
    expect(screen.getByText('Nunca rodou')).toBeVisible()
  })

  it('PP8: atualiza sozinho a cada 30 s só enquanto há carga rodando', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const f = mock([[200, resp([fonte()])], [200, resp([fonte({ status: 'concluida', status_texto: 'Concluída', progresso: null })], false)]])
    render(<PainelProgresso />)
    await screen.findByText('Rodando')
    expect(f).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(f).toHaveBeenCalledTimes(2)
    await screen.findByText('Concluída')
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000) })
    expect(f).toHaveBeenCalledTimes(2)  // nada rodando: para de consultar
  })

  it('PP11: carregando, erro inicial e erro mantendo os últimos dados', async () => {
    mock([[500, { detail: 'x' }]])
    const { unmount } = render(<PainelProgresso />)
    expect(screen.getByText('Carregando progresso das cargas…')).toBeInTheDocument()
    expect(await screen.findByText('Não foi possível carregar o progresso.')).toBeVisible()
    unmount()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mock([[200, resp([fonte()])], [500, { detail: 'Traceback senha=1' }]])
    render(<PainelProgresso />)
    await screen.findByText('Rodando')
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(await screen.findByText(/Não foi possível atualizar; últimos dados de \d{2}:\d{2}/)).toBeVisible()
    expect(screen.getByText('Lote 81 de ~240 (33%)')).toBeVisible()
    expect(document.body.textContent).not.toMatch(/Traceback|senha/)
  })

  it('UXP1: acessibilidade sem violações (axe)', async () => {
    mock([[200, resp([fonte(), fonte({ id: 'sil', nome: 'SIL', status: 'falhou', status_texto: 'Falhou', motivo: 'Falha ao ler a origem', progresso: null })])]])
    const { container } = render(<PainelProgresso />)
    await screen.findByRole('article', { name: 'SIL' })
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations).toEqual([])
  })

  it('#42: concluída com aviso de tabela vazia, em texto (não só cor)', async () => {
    mock([[200, resp([fonte({ status: 'concluida', status_texto: 'Concluída com aviso', progresso: null,
      ultima_execucao: { execucao_id: 9, finalizado_em: '2026-10-29T10:00:00Z', lidas: 10, gravadas: 10, lotes: 1, modo: 'Carga completa',
        alerta: { codigo: 'tabela_vazia', tabelas: ['PES_PessoaDocumento'], texto: 'Atenção: 1 tabela(s)…' } } })], false)]])
    render(<PainelProgresso />)
    const card = await screen.findByRole('article', { name: 'Educação' })
    expect(within(card).getByText('Concluída com aviso')).toBeVisible()
    expect(within(card).getByText('Tabela vazia: PES_PessoaDocumento')).toBeVisible()
    expect(within(card).getByText('Última execução (Carga completa): 10 linhas processadas')).toBeVisible()  // M1
    expect(card.textContent).not.toMatch(/lidas|gravadas/)
    const selo = within(card).getByText('Concluída com aviso').closest('.selo')
    expect(selo).toHaveClass('aviso-selo')
    expect(selo).not.toHaveClass('sucesso')  // âmbar, distinto do verde de Concluída
    expect(selo?.textContent).toContain('⚠')
    // texto do alerta logo abaixo do selo, sem recolher (fora de <details>)
    expect(within(card).getByText('Tabela vazia: PES_PessoaDocumento').closest('details')).toBeNull()
  })

  it('#42: na lista de tabelas, a tabela do alerta com 0 linhas aparece como "0 · vazia"', async () => {
    mock([[200, resp([fonte({ status: 'concluida', status_texto: 'Concluída com aviso', progresso: null,
      tabelas: [{ tabela: 'ZZTESTE_Notas', linhas_processadas: 4200, total_origem: null, estado: 'concluida' },
        { tabela: 'PES_PessoaDocumento', linhas_processadas: 0, total_origem: null, estado: 'concluida' },
        { tabela: 'ZZTESTE_Outra', linhas_processadas: 0, total_origem: null, estado: 'na_fila' }],
      ultima_execucao: { execucao_id: 9, finalizado_em: null, lidas: 4200, gravadas: 4200, lotes: 5, modo: 'Carga completa',
        alerta: { codigo: 'tabela_vazia', tabelas: ['PES_PessoaDocumento'] } } })], false)]])
    render(<PainelProgresso />)
    const card = await screen.findByRole('article', { name: 'Educação' })
    expect(within(card).getByText('Linhas processadas: 0 · vazia')).toBeInTheDocument()
    expect(within(card).getAllByText('Linhas processadas: 0')).toHaveLength(1)  // a outra tabela com 0 não é do alerta
  })

  it('#42 opcional: API sem o campo alerta (main antiga) e status_texto antigo continuam funcionando', async () => {
    mock([[200, resp([fonte({ status: 'concluida', status_texto: 'Concluída', progresso: null,
      ultima_execucao: { execucao_id: 9, finalizado_em: null, lidas: 10, gravadas: 10, lotes: 1, modo: 'Carga completa' } }),
    fonte({ id: 'iss', nome: 'ISS', status: 'concluida', status_texto: 'Concluída', progresso: null, tabelas: [],
      ultima_execucao: { execucao_id: 8, finalizado_em: null, lidas: 1, gravadas: 1, lotes: 1, modo: 'Carga completa',
        alerta: { codigo: 'tabela_vazia', tabelas: ['ZZTESTE_T'] } } })], false)]])
    render(<PainelProgresso />)
    const edu = await screen.findByRole('article', { name: 'Educação' })
    expect(within(edu).getByText('Concluída')).toBeVisible()
    expect(within(edu).queryByText(/Tabela vazia/)).toBeNull()
    const iss = screen.getByRole('article', { name: 'ISS' })
    expect(within(iss).getByText('Concluída com aviso')).toBeVisible()  // derivado no front mesmo com status_texto antigo
    expect(within(iss).getByText('Tabela vazia: ZZTESTE_T')).toBeVisible()
  })

  it('textoLote', () => {
    expect(textoLote(prog({ lote: 1000, lotes_total: 2400, percentual: 41 }))).toBe('Lote 1.000 de ~2.400 (41%)')
  })
})
