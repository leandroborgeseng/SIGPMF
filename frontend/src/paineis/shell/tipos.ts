/** Contrato de GET /api/paineis/{chave} (shell de painéis, onda 0). Só agregados; nenhuma pessoa. */
export type Variacao = { referencia: string; delta: number | null; pct: number | null; sentido: 'alta' | 'queda' | 'estavel' | null }
export type PontoSerie = { competencia: string; rotulo: string; valor: number | null; sem_carga: boolean }
export type KpiDado = {
  chave: string; rotulo: string; formato: 'brl' | 'pct' | 'n'; calculo: 'm1' | 'm2' | 'n' | 'razao_m2_m1' | 'media_m1_n'; alta_e_boa: boolean; ajuda: string
  valor: number | null; suprimido: boolean; mom: Variacao; yoy: Variacao; serie: PontoSerie[]
}
export type ItemDrill = { rotulo: string; chave: string; m1: number; m2: number; n: number; valor: number | null; outros: boolean; abre: boolean }
export type Migalha = { rotulo: string; secretaria: string | null; caminho: string[] }
export type Drill = {
  nivel: string; indice: number; medida: { chave: string; rotulo: string; formato: KpiDado['formato'] }
  migalhas: Migalha[]; itens: ItemDrill[]; ocultos: number; suprimido: boolean
  total: { m1: number; m2: number; n: number; valor: number | null } | null
}
export type RespPainel = {
  painel: string; titulo: string; exemplo: boolean; k: number; niveis: string[]
  estado: 'ok' | 'vazio' | 'sem_fonte'
  fonte: { sistema: string; rotulo: string; execucao_id: number; carga_em: string | null } | null
  filtros: { competencia: string | null; secretaria: string | null; caminho: string[] }
  secretarias: string[]; competencias: { valor: string; rotulo: string }[]
  ultima_competencia?: { valor: string; rotulo: string }
  kpis: KpiDado[]; drill: Drill | null
}
