import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

type Uso = { total_gb: number; livre_gb: number; usado_pct: number; status: string }
export type RespRecursos = {
  lido_em: string; faixas: { amarelo: number; vermelho: number }
  disco: Uso | null; memoria: Uso | null; banco_mb: Record<string, number> | null
}
export const RECURSOS_INTERVALO_MS = 30000
const TEXTO: Record<string, string> = { ok: 'Normal', atencao: 'Atenção', critico: 'Crítico' }
const CAMADAS: [string, string][] = [['bronze', 'Bronze'], ['silver', 'Prata (silver)'], ['gold', 'Ouro (gold)'], ['controle', 'Controle']]
const n1 = (v: number) => new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(v)
const hora = (iso: string) => new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso))
const valido = (u: unknown): u is Uso => !!u && typeof (u as Uso).usado_pct === 'number' && typeof (u as Uso).total_gb === 'number'

function Barra({ rotulo, uso, unidade }: { rotulo: string; uso: unknown; unidade: string }) {
  if (!valido(uso)) {
    return (
      <div className="recurso sem-leitura">
        <div className="recurso-topo"><span>{rotulo}</span><span className="recurso-status">Sem leitura do servidor</span></div>
        <div className="barra" aria-hidden="true"><span style={{ width: '100%' }} /></div>
      </div>
    )
  }
  const pct = Math.max(0, Math.min(100, uso.usado_pct))
  return (
    <div className={`recurso ${uso.status}`}>
      <div className="recurso-topo">
        <span>{rotulo}</span>
        <span className="recurso-status">{TEXTO[uso.status] ?? uso.status} · <span className="num">{n1(pct)}%</span> usado</span>
      </div>
      <div className="barra" role="meter" aria-label={`${rotulo}: ${n1(pct)}% usado, ${TEXTO[uso.status] ?? ''}`}
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><span style={{ width: `${pct}%` }} /></div>
      <p className="recurso-meta num">{n1(uso.livre_gb)} GB livres de {n1(uso.total_gb)} GB {unidade}</p>
    </div>
  )
}

/** RS1/RS2/RS5: card "Recursos do servidor" (só admin), atualiza a cada 30 s. */
export default function Recursos() {
  const [r, setR] = useState<RespRecursos | null>(null)
  const [falhou, setFalhou] = useState(false)
  const ler = useCallback(() => {
    api<RespRecursos>('/sistema/recursos').then((x) => { setR(x); setFalhou(false) }).catch(() => setFalhou(true))
  }, [])
  useEffect(() => {
    ler()
    const t = setInterval(ler, RECURSOS_INTERVALO_MS)
    return () => clearInterval(t)
  }, [ler])
  const dados = falhou ? null : r
  const banco = dados?.banco_mb && typeof dados.banco_mb === 'object' ? dados.banco_mb : null
  return (
    <section className="cartao recursos" aria-labelledby="t-recursos">
      <div className="recursos-cab">
        <h2 id="t-recursos">Recursos do servidor</h2>
        <span className="sutil">{dados?.lido_em ? <>Atualizado às <span className="num">{hora(dados.lido_em)}</span></> : 'Sem leitura do servidor'}</span>
      </div>
      <div className="recursos-grade">
        <Barra rotulo="Disco do banco" uso={dados?.disco} unidade="no volume" />
        <Barra rotulo="Memória" uso={dados?.memoria} unidade="no container" />
        <div className="recurso">
          <div className="recurso-topo"><span>Tamanho do banco</span></div>
          {banco ? (
            <dl className="banco-camadas">
              {CAMADAS.map(([k, rot]) => <div key={k}><dt>{rot}</dt><dd className="num">{typeof banco[k] === 'number' ? `${n1(banco[k])} MB` : '—'}</dd></div>)}
            </dl>
          ) : <p className="recurso-status sem-leitura">Sem leitura do servidor</p>}
        </div>
      </div>
    </section>
  )
}
