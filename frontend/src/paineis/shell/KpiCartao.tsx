import { tom, valorFmt, variacaoTexto } from './formato'
import type { KpiDado, PontoSerie, Variacao } from './tipos'

/** Sparkline 12 meses. Mês sem carga = falha na linha + marca tracejada cinza com título "Sem carga neste mês" (P02/UXD4). */
export function Sparkline({ serie, rotulo }: { serie: PontoSerie[]; rotulo: string }) {
  const vals = serie.map((p) => p.valor).filter((v): v is number => v != null)
  if (serie.length < 2) return null
  const topo = Math.max(...vals, 0), base = Math.min(...vals, topo)
  const faixa = Math.max(topo - base, Math.abs(topo) * 0.1) || 1
  const x = (i: number) => (i / (serie.length - 1)) * 100
  const y = (v: number) => 28 - ((v - base) / faixa) * 24
  const segs: string[][] = [[]]
  serie.forEach((p, i) => {
    if (p.valor == null) { if (segs[segs.length - 1].length) segs.push([]); return }
    segs[segs.length - 1].push(`${x(i)},${y(p.valor)}`)
  })
  const lacunas = serie.flatMap((p, i) => (p.valor == null ? [{ p, i }] : []))
  const desc = `${rotulo}, últimos 12 meses: ` + serie.map((p) => `${p.rotulo} ${p.valor == null ? (p.sem_carga ? 'sem carga' : 'oculto') : 'com valor'}`).join('; ')
  return (
    <svg className="sk-spark" viewBox="0 0 100 30" preserveAspectRatio="none" role="img" aria-label={desc}>
      {segs.filter((s) => s.length).map((s, i) => (s.length === 1
        ? <circle key={i} cx={s[0].split(',')[0]} cy={s[0].split(',')[1]} r="1.2" className="sk-ponto" />
        : <polyline key={i} points={s.join(' ')} vectorEffect="non-scaling-stroke" />))}
      {lacunas.map(({ p, i }) => (
        <line key={p.competencia} data-lacuna={p.competencia} className="sk-lacuna" x1={x(i)} x2={x(i)} y1="2" y2="28" vectorEffect="non-scaling-stroke">
          <title>{p.sem_carga ? `Sem carga neste mês (${p.rotulo})` : `Grupo pequeno, oculto (${p.rotulo})`}</title>
        </line>
      ))}
    </svg>
  )
}

function Var({ nome, v, k }: { nome: string; v: Variacao; k: KpiDado }) {
  const t = variacaoTexto(v, k.formato)
  return (
    <li className={`sk-var sk-${tom(v, k.alta_e_boa)}`} aria-label={`${nome}: ${t.leitura}`}>
      <span aria-hidden="true">{t.seta ? `${t.seta} ` : ''}{t.texto}</span>
      <span className="sk-var-ref" aria-hidden="true"> {nome}</span>
    </li>
  )
}

/** Cartão de KPI único (P01/UXD2): valor, Δ mês anterior e Δ mesmo mês do ano anterior, sparkline 12 meses. */
export function KpiCartao({ k, ativo, aoClicar }: { k: KpiDado; ativo?: boolean; aoClicar?: () => void }) {
  return (
    <article className={`cartao sk-kpi${ativo ? ' ativo' : ''}`} aria-label={k.rotulo}>
      <div className="sk-kpi-cab">
        <h3 className="cartao-rotulo">{k.rotulo}</h3>
        <span className="sk-ajuda" tabIndex={0} role="note" aria-label={`Como é calculado: ${k.ajuda}`} title={k.ajuda}>?</span>
      </div>
      {k.suprimido
        ? <p className="sk-privacidade">Grupo pequeno, oculto por privacidade</p>
        : <p className="sk-valor">{valorFmt(k.valor, k.formato)}</p>}
      <ul className="sk-vars">
        <Var nome={`vs. ${k.mom.referencia}`} v={k.mom} k={k} />
        <Var nome={`vs. ${k.yoy.referencia}`} v={k.yoy} k={k} />
      </ul>
      <Sparkline serie={k.serie} rotulo={k.rotulo} />
      {aoClicar && <button type="button" className="sk-kpi-acao" onClick={aoClicar} aria-pressed={!!ativo}>Ver detalhe</button>}
    </article>
  )
}
