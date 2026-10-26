import { api } from './api'

/** Explorar dados (EX1–EX7). Tudo por POST: nada de filtro, tabela ou id na URL (EX6). */
export const PERFIS_EXPLORADOR = ['admin', 'admin_lgpd']
export const CAMADAS = [
  { id: 'bronze', rotulo: 'Bronze' },
  { id: 'prata', rotulo: 'Prata' },
  { id: 'ouro', rotulo: 'Ouro' },
] as const
export type Camada = (typeof CAMADAS)[number]['id']

export type Estado = { processando: boolean; etapa: string | null; mensagem: string | null; ultima_carga: string | null }
export type Tabela = { tabela: string; linhas: number; ultima_carga: string | null }
export type Tabelas = { camada: Camada; tabelas: Tabela[]; total: number; estado: Estado; vazia: boolean; mensagem_vazia: string | null }
export type ColunaPerfil = { coluna: string; tipo: string; preenchido_pct: number; nulo_pct: number; distintos: number; exemplos: unknown[]; sensivel: boolean }
export type Perfil = { tabela: string; linhas: number; colunas: ColunaPerfil[] }
export type Amostra = {
  tabela: string; colunas: string[]; id_coluna: string; cidadao_coluna: string | null; linhas: Record<string, unknown>[]
  total: number; pagina: number; por_pagina: number; tem_anterior: boolean; tem_proxima: boolean; quarentena_disponivel?: boolean
}
export type Funil = {
  bronze: number; bronze_unicos: number; quarentena: number; prata: number; prata_cidadaos: number; ouro: number
  descartes: { etapa: string; motivo: string; quantidade: number }[]; estado: Estado; quarentena_disponivel: boolean
}
export type Linhagem = {
  cidadao_id: string
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
