// Tela de Conectores e Cargas (PR-B: CT5–CT12, CT16–CT22; UXC1–UXC13). Só textos e contas: nenhum dado pessoal.
export type Status = 'na_fila' | 'rodando' | 'concluida' | 'parcial' | 'erro' | 'cancelada' | 'pausada'
export type Execucao = {
  id: number; tipo: string; status: Status; etapa: string | null
  iniciado_em: string | null; finalizado_em: string | null; duracao_s: number | null
  lidos: number; gravados: number; quarentena: number
  processadas_etapa: number | null; total_etapa: number | null
  subetapa: string | null; subetapa_contador: number | null; subetapa_total: number | null
  subetapa_iniciada_em: string | null; ultimo_sinal_em: string | null; sem_sinal_s: number | null
  motivo: string | null; proxima_acao: string | null; duracao_tipica_s?: number | null
}
export type Subetapa = { etapa: string; subetapa: string | null; contador: number | null; total: number | null; iniciada_em: string | null; finalizada_em: string | null; duracao_s: number | null }
export type Historico = Pick<Execucao, 'id' | 'tipo' | 'status' | 'iniciado_em' | 'duracao_s' | 'lidos' | 'gravados'>
export type Carga = {
  sistema: string; fonte: string; atual: Execucao; log: Subetapa[]; historico: Historico[]
  ultima_completa_em: string | null; ultima_incremental_em: string | null
  agenda_ativa: boolean | null; agenda_horario: string | null
}
export type RespAndamento = { agora: string; sem_sinal_limite_s: number; cargas: Carga[] }

export const ATUALIZA_MS = 10_000
export const STATUS: Record<Status, { texto: string; icone: string; classe: string }> = {
  na_fila: { texto: 'Na fila', icone: '⏳', classe: 'neutro' },
  rodando: { texto: 'Rodando', icone: '▶', classe: 'em_andamento' },
  concluida: { texto: 'Concluída', icone: '✓', classe: 'sucesso' },
  parcial: { texto: 'Parcial', icone: '◐', classe: 'atencao' },
  erro: { texto: 'Com erro', icone: '✕', classe: 'falha' },
  cancelada: { texto: 'Cancelada', icone: '■', classe: 'neutro' },
  pausada: { texto: 'Pausada', icone: '❚❚', classe: 'atencao' },
}
const TZ = 'America/Sao_Paulo'

export function num(n: number): string {
  if (n >= 1_000_000) return `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(n / 1_000_000)} mi`
  return new Intl.NumberFormat('pt-BR').format(n)
}
export function horaBRT(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(d)} BRT`
}
export function dataHoraBRT(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const f = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, ...o }).format(d)
  return `${f({ day: '2-digit', month: '2-digit', year: 'numeric' })} ${f({ hour: '2-digit', minute: '2-digit', hour12: false })} BRT`
}
export function duracao(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.round(s / 60)} min`
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`
}

// TL: 3 fases. A leitura da origem grava direto na bronze: aparece como "Bronze · lendo origem".
export const FASES = ['Bronze', 'Prata', 'Ouro'] as const
export function indiceFase(etapa: string | null): number {
  if (etapa === 'concluida') return FASES.length
  if (etapa === 'prata') return 1
  if (etapa === 'ouro') return 2
  return 0
}
export function rotuloFase(etapa: string | null, i: number): string {
  return i === 0 && (etapa === 'leitura' || !etapa) ? 'Bronze · lendo origem' : FASES[i]
}
export function ariaFase(etapa: string | null): string {
  if (etapa === 'concluida') return 'Fases concluídas: Bronze, Prata e Ouro'
  const i = indiceFase(etapa)
  return `Fase ${i + 1} de ${FASES.length}: ${rotuloFase(etapa, i)}`
}
export type ParteFase = { nome: string; marca: '✓' | '●' | '✕' | ''; apagada: boolean }
/** "Bronze ✓ · Prata ● · Ouro"; concluída: todas ✓ e sem atual; erro: a fase da falha com ✕ e as seguintes apagadas. */
export function partesFases(etapa: string | null, status?: string): ParteFase[] {
  const atual = status === 'concluida' ? FASES.length : indiceFase(etapa)
  return FASES.map((_, i) => {
    const nome = i === atual && status !== 'erro' ? rotuloFase(etapa, i) : FASES[i]
    if (i < atual) return { nome, marca: '✓', apagada: false }
    if (i === atual) return status === 'erro' ? { nome, marca: '✕', apagada: false } : { nome, marca: '●', apagada: false }
    return { nome, marca: '', apagada: status === 'erro' }
  })
}
export function textoFases(etapa: string | null, status?: string): string {
  return partesFases(etapa, status).map((p) => (p.marca ? `${p.nome} ${p.marca}` : p.nome)).join(' · ')
}
export function ariaFases(etapa: string | null, status?: string): string {
  if (status === 'concluida' || etapa === 'concluida') return 'Fases concluídas: Bronze, Prata e Ouro'
  const i = indiceFase(etapa)
  return status === 'erro' ? `Fase ${i + 1} de ${FASES.length}: ${FASES[i]}, com erro` : ariaFase(etapa)
}
export function textoErro(motivo: string | null): string {
  return `${(motivo ?? 'A carga falhou').replace(/\.$/, '')}. Para tentar de novo, use Rodar de novo em Conectores.`
}

/** CT6/CT17/UXC2: progresso da subetapa. Sem total: nunca percentual. Sem contador: consulta sem contagem parcial. */
export function textoProgresso(e: Execucao): { texto: string; pct: number | null } {
  const c = e.subetapa_contador ?? e.processadas_etapa
  const t = e.subetapa_contador != null ? e.subetapa_total : e.total_etapa
  if (c == null) return { texto: `Executando consulta: ${e.subetapa ?? 'sem descrição'}, sem contagem parcial`, pct: null }
  if (!t) return { texto: `${num(c)} linhas processadas · em andamento, sem total ainda`, pct: null }
  const pct = Math.min(100, Math.floor((c / t) * 100))
  return { texto: `${num(c)} de ${num(t)} linhas (${pct}%)`, pct }
}

export function segundosDesde(iso: string | null, agoraMs: number): number | null {
  return iso ? Math.max(0, Math.round((agoraMs - new Date(iso).getTime()) / 1000)) : null
}

/** CT18/UXC11: sinal de vida; > limite sem sinal = possível travamento (separado do erro, CT20). */
export function sinalDeVida(e: Execucao, agoraMs: number, limiteS = 300): { travada: boolean; texto: string } | null {
  if (e.status !== 'rodando') return null
  const s = segundosDesde(e.ultimo_sinal_em, agoraMs)
  if (s == null) return { travada: false, texto: 'Ativo · sem sinal registrado ainda' }
  if (s > limiteS) return { travada: true, texto: `Sem sinal há ${Math.floor(s / 60)} min: possível travamento` }
  return { travada: false, texto: `Ativo · última atividade há ${s} s` }
}

/** CT20: contagem 0 e sinal recente. */
export function semResultadosAinda(e: Execucao, agoraMs: number, limiteS = 300): boolean {
  const v = sinalDeVida(e, agoraMs, limiteS)
  const c = e.subetapa_contador ?? e.processadas_etapa ?? 0
  return !!v && !v.travada && e.gravados === 0 && c === 0
}

/** CT7/UXC3/UXC12: estimativa de término (média de linhas/min) ou duração típica da subetapa. */
export function estimativa(e: Execucao, agoraMs: number): string | null {
  if (e.status !== 'rodando') return null
  const c = e.subetapa_contador ?? e.processadas_etapa
  const t = e.subetapa_contador != null ? e.subetapa_total : e.total_etapa
  const desde = segundosDesde(e.subetapa_iniciada_em ?? e.iniciado_em, agoraMs) ?? 0
  if (t && c != null) {
    if (c <= 0 || (desde < 60 && c / t < 0.05)) return 'Calculando tempo restante…'
    const restante = ((t - c) / c) * desde
    return `Faltam ~${duracao(restante)} (estimativa)`
  }
  if (e.duracao_tipica_s) return `Esta subetapa costuma levar ~${duracao(e.duracao_tipica_s)} (estimativa)`
  return 'Primeira execução, sem estimativa'
}
