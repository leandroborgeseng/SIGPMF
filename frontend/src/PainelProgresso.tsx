import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Esqueleto } from './componentes'

/** PP1–PP11 / UXP1–UXP8: progresso por fonte, só leitura (cancelar, histórico e alertas ficam para os PRs 2 e 3). */
export type Tabela = { tabela: string; linhas_processadas: number; total_origem: number | null; estado: 'concluida' | 'lendo' | 'na_fila' }
export type Progresso = {
  execucao_id: number; modo: string; lote: number; lotes_total: number | null; percentual: number | null
  total_origem: number | null; origem_total: string | null; linhas_processadas: number; iniciado_em: string
  decorrido_s: number; calculando: boolean; previsao_termino: string | null; previsao_termino_brt: string | null
  ultimo_sinal: string; sem_sinal_min: number; orfa_limite_min: number; possivel_travada: boolean
}
export type FonteProgresso = {
  id: string; nome: string; status: string; status_texto: string; motivo_codigo: string | null; motivo: string | null
  falta: string[] | null; aguardando_em: string | null; aguardando_em_brt: string | null; progresso: Progresso | null
  ultima_execucao: { execucao_id: number; finalizado_em: string | null; lidas: number; gravadas: number; lotes: number; modo: string } | null
  tabelas: Tabela[]
}
export type RespProgresso = { agora: string; rodando: boolean; atualizar_a_cada_s: number; fontes: FonteProgresso[] }

const ICONE: Record<string, string> = {
  rodando: '⟳', falhou: '✕', pausada: '⏸', aguardando: '◷', concluida: '✓', cancelada: '⊘', nunca_rodou: '–', nao_liberada: '🔒',
}
const CLASSE: Record<string, string> = { rodando: 'em_andamento', falhou: 'falha', concluida: 'sucesso', nao_liberada: 'falha' }
const ESTADO_TAB: Record<Tabela['estado'], string> = { concluida: '✓ Concluída', lendo: '⟳ Lendo', na_fila: '· Na fila' }
const n = (x: number) => x.toLocaleString('pt-BR')
const hora = (d: Date) => d.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo' })

export function duracao(s: number): string {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h} h ${m} min` : `${m} min`
}
export function textoLote(p: Progresso): string {
  if (p.lotes_total == null || p.percentual == null) return `Lote ${n(p.lote)} · total desconhecido`
  return `Lote ${n(p.lote)} de ~${n(p.lotes_total)} (${p.percentual}%)`
}

function Card({ f }: { f: FonteProgresso }) {
  const p = f.progresso
  const titulo = `pp-${f.id}`
  return (
    <article className={`pp-card${p?.possivel_travada ? ' travada' : ''}`} aria-labelledby={titulo}>
      <header className="pp-cab">
        <h3 id={titulo}>{f.nome}</h3>
        <span className={`selo ${CLASSE[f.status] ?? ''}`}><span aria-hidden="true">{ICONE[f.status]} </span>{f.status_texto}
          {f.status === 'aguardando' && f.aguardando_em_brt ? ` · próxima às ${f.aguardando_em_brt} (BRT)` : ''}</span>
      </header>
      {p && (<>
        <p className="sutil">{p.modo}</p>
        <div className="pp-barra" role="progressbar" aria-label={`Progresso de ${f.nome}`} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={p.percentual ?? undefined} aria-valuetext={textoLote(p)}>
          <div className={p.percentual == null ? 'pp-indeterminada' : ''} style={p.percentual == null ? undefined : { width: `${p.percentual}%` }} />
        </div>
        <p className="pp-lote">{textoLote(p)}</p>
        {p.origem_total && <p className="sutil">total {p.origem_total}: {n(p.total_origem ?? 0)} linhas</p>}
        <p>Rodando há {duracao(p.decorrido_s)} · {p.calculando ? 'término previsto: calculando…'
          : p.previsao_termino_brt ? `término previsto ~${p.previsao_termino_brt} (BRT)` : 'término previsto: sem total conhecido'}</p>
        <p>Último sinal há {p.sem_sinal_min} min</p>
        {p.possivel_travada && <p role="alert" className="aviso-ambar">Possível execução travada: sem sinal há {p.sem_sinal_min} min.</p>}
      </>)}
      {f.motivo && <p className="pp-motivo">Motivo: {f.motivo}</p>}
      {f.falta && <p className="pp-motivo">Não liberada — falta: {f.falta.join(' / ')}</p>}
      {!p && f.ultima_execucao && <p className="sutil">Última execução ({f.ultima_execucao.modo}): {n(f.ultima_execucao.lidas)} lidas,
        {' '}{n(f.ultima_execucao.gravadas)} gravadas</p>}
      {f.tabelas.length > 0 && (
        <details>
          <summary>Ver tabelas ({f.tabelas.length})</summary>
          <ul className="pp-tabelas">
            {f.tabelas.map((t) => (
              <li key={t.tabela}><span className="pp-nome">{t.tabela}</span>
                <span>Linhas processadas: {t.linhas_processadas === 0 ? '0' : n(t.linhas_processadas)}</span>
                <span>{ESTADO_TAB[t.estado]}</span></li>
            ))}
          </ul>
        </details>
      )}
    </article>
  )
}

export default function PainelProgresso() {
  const [d, setD] = useState<RespProgresso | null>(null)
  const [erro, setErro] = useState(false)
  const [quando, setQuando] = useState<Date | null>(null)
  const vivo = useRef(true)
  const carregar = useCallback(async () => {
    try {
      const r = await api<RespProgresso>('/fontes/progresso')
      if (!vivo.current) return
      if (!r || !Array.isArray(r.fontes)) throw new Error('resposta inválida')
      setD(r); setErro(false); setQuando(new Date())
    } catch {
      if (vivo.current) setErro(true)
    }
  }, [])
  useEffect(() => { vivo.current = true; void carregar(); return () => { vivo.current = false } }, [carregar])
  useEffect(() => {  // PP8: atualiza a cada 30 s enquanto houver carga rodando
    if (!d?.rodando) return
    const t = window.setInterval(() => { void carregar() }, (d.atualizar_a_cada_s || 30) * 1000)
    return () => window.clearInterval(t)
  }, [d?.rodando, d?.atualizar_a_cada_s, carregar])

  return (
    <section aria-labelledby="t-pp" className="pp">
      <div className="pp-topo">
        <h2 id="t-pp">Progresso das cargas por fonte</h2>
        {quando && <span className="sutil">atualizado às {hora(quando)}</span>}
        <button className="btn" onClick={() => void carregar()}>Atualizar</button>
      </div>
      {erro && (d
        ? <p role="alert" className="aviso erro">Não foi possível atualizar; últimos dados de {quando ? hora(quando).slice(0, 5) : '—'}.</p>
        : <div role="alert" className="aviso erro"><p>Não foi possível carregar o progresso.</p>
          <button className="btn" onClick={() => void carregar()}>Tentar de novo</button></div>)}
      {!d && !erro && <Esqueleto linhas={4} texto="Carregando progresso das cargas…" />}
      {d && (
        <>
          <p className="sr-only" aria-live="polite">{d.fontes.map((f) => `${f.nome}: ${f.status_texto}`).join('; ')}</p>
          <div className="pp-grade">{d.fontes.map((f) => <Card key={f.id} f={f} />)}</div>
        </>
      )}
    </section>
  )
}
