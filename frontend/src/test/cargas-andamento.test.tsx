import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { MemoryRouter } from 'react-router-dom'
import CargasPagina from '../pages/Cargas'
import { ariaFase, partesFases, textoFases, estimativa, sinalDeVida, textoProgresso, type Carga, type Execucao, type RespAndamento } from '../cargas'
import { salvaSessao } from '../api'

const AGORA = new Date('2026-10-29T23:00:00Z') // 20:00 BRT
const iso = (segAtras: number) => new Date(AGORA.getTime() - segAtras * 1000).toISOString()
const ex = (o: Partial<Execucao> = {}): Execucao => ({
  id: 1, tipo: 'Carga completa', status: 'rodando', etapa: 'prata', iniciado_em: iso(1800), finalizado_em: null,
  duracao_s: 1800, lidos: 6_012_000, gravados: 0, quarentena: 0, processadas_etapa: 0, total_etapa: null,
  subetapa: 'Prata: buscando duplicados por CPF', subetapa_contador: 0, subetapa_total: null,
  subetapa_iniciada_em: iso(900), ultimo_sinal_em: iso(12), sem_sinal_s: 12, motivo: null, proxima_acao: null,
  duracao_tipica_s: null, ...o,
})
const carga = (o: Partial<Carga> = {}, a: Partial<Execucao> = {}): Carga => ({
  sistema: 'ZZTESTE', fonte: 'ZZTESTE Fonte', atual: ex(a),
  log: [{ etapa: 'prata', subetapa: 'Prata: buscando duplicados por CPF', contador: 0, total: null, iniciada_em: iso(900), finalizada_em: null, duracao_s: null },
    { etapa: 'prata', subetapa: 'Prata: lendo registros da bronze', contador: null, total: null, iniciada_em: iso(1500), finalizada_em: iso(900), duracao_s: 600 }],
  historico: Array.from({ length: 30 }, (_, i) => ({ id: 100 - i, tipo: 'Incremental', status: 'concluida' as const, iniciado_em: iso(86400 * (i + 1)), duracao_s: 720, lidos: 1000, gravados: 1000 })),
  ultima_completa_em: iso(86400), ultima_incremental_em: iso(3600), agenda_ativa: true, agenda_horario: '02:00', ...o,
})
const Cargas = () => <MemoryRouter><CargasPagina /></MemoryRouter>
const resp = (cargas: Carga[]): RespAndamento => ({ agora: AGORA.toISOString(), sem_sinal_limite_s: 300, cargas })

function mock(r: RespAndamento) {
  const f = vi.fn<(url: string) => Promise<Response>>(async () => new Response(JSON.stringify(r), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', f)
  return f
}

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }); vi.useFakeTimers({ shouldAdvanceTime: true }); vi.setSystemTime(AGORA) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('textos da carga (CT6, CT7, CT17, CT18)', () => {
  it('total desconhecido: nunca 0%', () => {
    const p = textoProgresso(ex({ subetapa_contador: 1_200_000 }))
    expect(p.pct).toBeNull()
    expect(p.texto).toBe('1,2 mi linhas processadas · em andamento, sem total ainda')
  })
  it('com total mostra X de Y e percentual', () => {
    expect(textoProgresso(ex({ subetapa_contador: 1_200_000, subetapa_total: 6_000_000 })).texto).toBe('1,2 mi de 6 mi linhas (20%)')
  })
  it('CT17: consulta sem contagem parcial', () => {
    expect(textoProgresso(ex({ subetapa_contador: null, processadas_etapa: null })).texto).toBe('Executando consulta: Prata: buscando duplicados por CPF, sem contagem parcial')
  })
  it('CT7: estimativa pela média e rótulo "estimativa"', () => {
    expect(estimativa(ex({ subetapa_contador: 3_000_000, subetapa_total: 6_000_000 }), AGORA.getTime())).toBe('Faltam ~15 min (estimativa)')
    expect(estimativa(ex({ subetapa_total: null }), AGORA.getTime())).toBe('Primeira execução, sem estimativa')
  })
  it('CT18: até 5 min ativo; depois possível travamento', () => {
    expect(sinalDeVida(ex(), AGORA.getTime())?.texto).toBe('Ativo · última atividade há 12 s')
    expect(sinalDeVida(ex({ ultimo_sinal_em: iso(420) }), AGORA.getTime())).toEqual({ travada: true, texto: 'Sem sinal há 7 min: possível travamento' })
  })
})

describe('tela de cargas (CT5, CT9, CT10, CT20, CT21, CT22)', () => {
  it('CT22: subetapa longa sem linhas gravadas mostra os textos de CT18 e CT20', async () => {
    mock(resp([carga()]))
    render(<Cargas />)
    const card = await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByText('Rodando')).toBeVisible()
    expect(within(card).getByText('Prata: buscando duplicados por CPF', { selector: 'strong' })).toBeVisible()
    expect(within(card).getByText('Rodando, ainda sem resultados')).toBeVisible()
    expect(within(card).getByText('Ativo · última atividade há 12 s')).toBeVisible()
    expect(within(card).getByText('0 linhas processadas · em andamento, sem total ainda')).toBeVisible()
    expect(within(card).queryByText(/0%/)).toBeNull()
    expect(within(card).queryByText(/possível travamento/i)).toBeNull()
  })
  it('CT18/CT20: sem sinal há mais de 5 min mostra aviso de travamento, separado do erro', async () => {
    mock(resp([carga({}, { ultimo_sinal_em: iso(420) })]))
    render(<Cargas />)
    const card = await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByText('Sem sinal há 7 min: possível travamento')).toBeVisible()
    expect(within(card).getByText('Possível travamento')).toBeVisible()
    expect(within(card).queryByText('Com erro')).toBeNull()
    expect(within(card).queryByText('Rodando, ainda sem resultados')).toBeNull()
  })
  it('CT10: erro mostra motivo e próxima ação', async () => {
    mock(resp([carga({}, { status: 'erro', etapa: 'leitura', motivo: 'Falha ao ler a origem', proxima_acao: 'Rodar de novo' })]))
    render(<Cargas />)
    const card = await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByText('Com erro')).toBeVisible()
    expect(within(card).getByText(/^Falha ao ler a origem\. Para tentar de novo, use Rodar de novo em Conectores\./)).toBeVisible()
    expect(within(card).getByRole('link', { name: 'Abrir ZZTESTE Fonte em Conectores' })).toHaveAttribute('href', '/conectores#fonte-zzteste')
  })
  it('CT21/UXC8: log das subetapas em BRT e histórico de 30', async () => {
    mock(resp([carga()]))
    render(<Cargas />)
    const card = await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByText('Últimas 2 subetapas')).toBeInTheDocument()
    expect(within(card).getByText(/19:35:00 BRT/)).toBeInTheDocument()
    expect(within(card).getAllByRole('row')).toHaveLength(31)
  })
  it('CT9: atualiza sozinha a cada 10 s', async () => {
    const f = mock(resp([carga()]))
    render(<Cargas />)
    await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(f).toHaveBeenCalledTimes(1)
    await act(async () => { vi.advanceTimersByTime(10_000) })
    expect(f).toHaveBeenCalledTimes(2)
    expect(f.mock.calls[0][0]).toBe('/api/cargas/andamento')
  })
})

describe('ciclo 2 (TL fases, UX M1–M4)', () => {
  it('TL: 3 fases com ✓ nas concluídas e ● na atual; leitura = "Bronze · lendo origem"', () => {
    expect(textoFases('prata')).toBe('Bronze ✓ · Prata ● · Ouro')
    expect(textoFases('ouro')).toBe('Bronze ✓ · Prata ✓ · Ouro ●')
    expect(textoFases('leitura')).toBe('Bronze · lendo origem ● · Prata · Ouro')
    expect(textoFases('bronze')).toBe('Bronze ● · Prata · Ouro')
    expect(textoFases('prata', 'erro')).toBe('Bronze ✓ · Prata ✕ · Ouro')
    expect(partesFases('prata', 'erro').map((p) => p.apagada)).toEqual([false, false, true])
    expect(ariaFase('prata')).toBe('Fase 2 de 3: Prata')
    expect(ariaFase('leitura')).toBe('Fase 1 de 3: Bronze · lendo origem')
  })
  it('M1: a fase aparece uma vez só (sem chip duplicado) e com aria-label', async () => {
    mock(resp([carga()]))
    render(<Cargas />)
    const card = await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByRole('img', { name: 'Fase 2 de 3: Prata' })).toHaveTextContent('Bronze ✓ · Prata ● · Ouro')
    expect(within(card).queryByText(/Etapa \d de/)).toBeNull()
    expect(within(card).queryAllByText(/Fase \d de 3/)).toHaveLength(0)
  })
  it('D2: carga concluída mostra as 3 fases com ✓', async () => {
    expect(textoFases('ouro', 'concluida')).toBe('Bronze ✓ · Prata ✓ · Ouro ✓')
    mock(resp([carga({}, { status: 'concluida', etapa: 'ouro', finalizado_em: iso(60) })]))
    render(<Cargas />)
    const card = await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByRole('img', { name: 'Fases concluídas: Bronze, Prata e Ouro' })).toHaveTextContent('Bronze ✓ · Prata ✓ · Ouro ✓')
    expect(within(card).getByText(/^Concluída em 29\/10\/2026 19:59 \(BRT\) · 30 min$/)).toBeVisible()
    expect(within(card).queryByText(/●/)).toBeNull()
  })
  it('erro: fase com ✕ e as seguintes apagadas; H1 igual ao menu', async () => {
    mock(resp([carga({}, { status: 'erro', etapa: 'prata', motivo: 'Falha ao ler a origem' })]))
    render(<Cargas />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Andamento das cargas' })).toBeVisible()
    const card = screen.getByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByRole('img', { name: 'Fase 2 de 3: Prata, com erro' })).toHaveTextContent('Bronze ✓ · Prata ✕ · Ouro')
    expect(within(card).getByText(/Ouro/, { selector: '.cg-fase-apagada' })).toBeInTheDocument()
  })
  it('M2: rótulos "nesta execução"', async () => {
    mock(resp([carga()]))
    render(<Cargas />)
    const card = await screen.findByRole('article', { name: 'ZZTESTE Fonte' })
    expect(within(card).getByText('Lidas nesta execução')).toBeVisible()
    expect(within(card).getByText('Gravadas nesta execução')).toBeVisible()
    expect(within(card).queryByText('Lidas')).toBeNull()
  })
  it('M4: padding-bottom para a barra inferior fixa', () => {
    const css = readFileSync(resolve(__dirname, '../cargas.css'), 'utf8')
    expect(css).toMatch(/\.cg \{ padding-bottom: calc\(64px/)
  })
})
