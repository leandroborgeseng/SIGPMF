import { api } from './api'

/** Explorar dados (EX1–EX7). Tudo por POST: nada de filtro, tabela ou id na URL (EX6). */
export const PERFIS_EXPLORADOR = ['admin', 'admin_lgpd']
export const CAMADAS = [
  { id: 'bronze', rotulo: 'Bronze' },
  { id: 'prata', rotulo: 'Prata' },
  { id: 'ouro', rotulo: 'Ouro' },
] as const
export type Camada = (typeof CAMADAS)[number]['id']

export type Estado = { processando: boolean; etapa: string | null; etapa_num?: number | null; etapas_total?: number; atualizado_em?: string | null; parcial?: boolean; mensagem: string | null; ultima_carga: string | null }
export type Tabela = { tabela: string; linhas: number; ultima_carga: string | null }
export type Tabelas = { camada: Camada; tabelas: Tabela[]; total: number; estado: Estado; vazia: boolean; mensagem_vazia: string | null }
export type ColunaPerfil = { coluna: string; tipo: string; tipo_tecnico: string; preenchido_pct: number; nulo_pct: number; distintos: number; aprox: boolean; exemplos: unknown[]; sensivel: boolean }
export type Perfil = { tabela: string; linhas: number; aprox: boolean; amostra_perfil: number; colunas: ColunaPerfil[] }
export type Amostra = {
  tabela: string; colunas: string[]; id_coluna: string; cidadao_coluna: string | null; colunas_sensiveis: string[]; linhas: Record<string, unknown>[]
  pagina: number; por_pagina: number; tem_anterior: boolean; tem_proxima: boolean; quarentena_disponivel?: boolean
}
export type Funil = {
  bronze: number; bronze_unicos: number; quarentena: number; prata: number; prata_cidadaos: number; ouro: number
  descartes: { etapa: string; motivo: string; rotulo: string; quantidade: number }[]; estado: Estado; quarentena_disponivel: boolean
}
export type Abrir = { camada: Camada; tabela: string; filtros: Record<string, string> }
export type Linhagem = {
  cidadao_id: string
  veio_de: { conector: string; tabela: string; chave: string; camada: Camada; abrir: Abrir }[]
  prata: { origem: string; tabela: string; chave: string; bronze_id: number; metodo: string; confianca: number; bronze: { origem: string; tabela: string; chave: string } | null }[]
  identificadores: { tipo: string; valor: string }[]
  ouro: { dominio: string; campo: string; sistema: string; quantidade: number }[]
}

export const explorar = <T,>(rota: string, corpo: object = {}) =>
  api<T>(`/explorar/${rota}`, { method: 'POST', body: JSON.stringify(corpo) })

export function texto(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export const plural = (n: number, um: string, varios: string) => `${n.toLocaleString('pt-BR')} ${n === 1 ? um : varios}`
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const DATA = /^\d{4}-\d{2}-\d{2}$/
const DATA_HORA = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/
/** Data só (dd/mm/aaaa) ou data-hora (dd/mm/aaaa HH:mm); demais valores como texto. */
export function formata(v: unknown, dataBR: (s: string) => string, dataHoraBR: (s: string) => string): string {
  if (typeof v === 'string') {
    if (DATA.test(v)) return dataBR(v)
    if (DATA_HORA.test(v)) return /T00:00:00(\.0+)?([+-]\d{2}:\d{2}|Z)?$/.test(v) ? dataBR(v.slice(0, 10)) : dataHoraBR(v)
  }
  if (typeof v === 'boolean') return v ? 'Sim' : 'Não'
  return texto(v)
}
