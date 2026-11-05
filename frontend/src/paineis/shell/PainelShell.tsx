import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, ApiErro, dataHoraBR } from '../../api'
import { ErrorBoundary } from '../../componentes'
import { KpiCartao } from './KpiCartao'
import { calcula, valorFmt } from './formato'
import type { KpiDado, RespPainel } from './tipos'
import './shell.css'

type Estado = { d: RespPainel | null; erro: string | null; carregando: boolean }

/** Lê o painel com os filtros da URL; erro vira estado (nunca lança, P04). */
export function usePainel(chave: string, qs: string) {
  const [e, setE] = useState<Estado>({ d: null, erro: null, carregando: true })
  const carregar = useCallback(() => {
    setE((x) => ({ ...x, erro: null, carregando: true }))
    api<RespPainel>(`/paineis/${chave}${qs ? `?${qs}` : ''}`)
      .then((d) => setE({ d, erro: null, carregando: false }))
      .catch((err) => setE({ d: null, erro: err instanceof ApiErro && err.status === 403 ? 'Sem acesso a dados desta secretaria.' : 'Não foi possível carregar este indicador.', carregando: false }))
  }, [chave, qs])
  useEffect(carregar, [carregar])
  return { ...e, carregar }
}

function CartaoSeguro({ k, ativo, aoClicar }: { k: KpiDado; ativo: boolean; aoClicar: () => void }) {
  // Erro de render num cartão não derruba os outros (P04/UXD7)
  return (
    <ErrorBoundary titulo="Não foi possível carregar este indicador." chave={k}>
      <KpiCartao k={k} ativo={ativo} aoClicar={aoClicar} />
    </ErrorBoundary>
  )
}

export function EsqueletoKpis() {
  return (
    <div className="sk-kpis" aria-busy="true" aria-live="polite">
      <span className="sr-only">Carregando indicadores…</span>
      {Array.from({ length: 5 }, (_, i) => <div key={i} className="cartao sk-kpi sk-esq"><div /><div /><div /></div>)}
    </div>
  )
}

/** Shell padrão de painel (P01–P08 / UXD1–UXD10): cabeçalho com filtros na URL e selo de fonte, KPIs, drill-down em migalhas. */
export function PainelShell({ chave, kicker, sub }: { chave: string; kicker: string; sub: string }) {
  const [sp, setSp] = useSearchParams()
  const qs = (() => { const c = new URLSearchParams(sp); c.delete('kpi'); return c.toString() })()
  const { d, erro, carregando, carregar } = usePainel(chave, qs)
  const kpiSel = sp.get('kpi')
  const muda = (alt: Record<string, string | string[] | null>) => {
    const n = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(alt)) {
      n.delete(k)
      if (Array.isArray(v)) v.forEach((x) => n.append(k, x))
      else if (v) n.set(k, v)
    }
    setSp(n)
  }
  const titulo = d?.titulo ?? 'Painel'
  return (
    <section className="painel sk-painel" aria-labelledby="sk-titulo" lang="pt-BR">
      <header className="sk-cab">
        <div className="sk-cab-txt">
          <p className="kicker">{kicker}</p>
          <h1 id="sk-titulo">{titulo}</h1>
          <p className="sutil">{sub}</p>
        </div>
        {d?.exemplo && <p className="sk-exemplo" role="note"><strong>EXEMPLO</strong> · números sintéticos, sem carga real</p>}
        <div className="sk-filtros" role="group" aria-label="Filtros do painel">
          <label>Competência
            <select value={d?.filtros.competencia ?? sp.get('competencia') ?? ''} onChange={(e) => muda({ competencia: e.target.value || null })} disabled={!d?.competencias.length}>
              {!d?.competencias.length && <option value="">—</option>}
              {[...(d?.competencias ?? [])].reverse().map((c) => <option key={c.valor} value={c.valor}>{c.rotulo}</option>)}
            </select>
          </label>
          <label>Secretaria
            <select value={d?.filtros.secretaria ?? ''} onChange={(e) => muda({ secretaria: e.target.value || null, caminho: [] })}
              disabled={!d || (d.secretarias.length <= 1 && !!d.filtros.secretaria)}>
              {(!d || d.secretarias.length > 1) && <option value="">Todas</option>}
              {(d?.secretarias ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <span className="selo neutro sk-fonte" data-testid="selo-fonte">
            <i aria-hidden="true" />
            {d?.fonte ? `Fonte: ${d.fonte.rotulo} · carga de ${d.fonte.carga_em ? dataHoraBR(d.fonte.carga_em) : '—'}` : 'Fonte: sem carga'}
          </span>
        </div>
      </header>

      {carregando && !d && <EsqueletoKpis />}
      {erro && (
        <div role="alert" className="aviso erro"><p>{erro}</p><button className="btn" onClick={carregar}>Tentar de novo</button></div>
      )}
      {d?.estado === 'sem_fonte' && <div className="cartao sk-estado" role="status"><p>Esta área ainda não tem carga de dados.</p></div>}
      {d?.estado === 'vazio' && (
        <div className="cartao sk-estado" role="status">
          <p>Sem dados para esta competência.</p>
          {d.ultima_competencia && <button type="button" className="link-btn" onClick={() => muda({ competencia: d.ultima_competencia!.valor })}>Ver última competência carregada ({d.ultima_competencia.rotulo})</button>}
        </div>
      )}
      {d?.estado === 'ok' && (
        <>
          <h2 className="sr-only">Indicadores</h2>
          <div className="sk-kpis">
            {d.kpis.map((k) => <CartaoSeguro key={k.chave} k={k} ativo={(kpiSel ?? d.drill?.medida.chave) === k.chave} aoClicar={() => muda({ kpi: k.chave })} />)}
          </div>
          {d.drill && d.kpis.length > 0 && (
            <DrillMigalhas d={d} kpi={d.kpis.find((k) => k.chave === kpiSel) ?? d.kpis.find((k) => k.chave === d.drill!.medida.chave) ?? d.kpis[0]} muda={muda} />
          )}
        </>
      )}
    </section>
  )
}

function DrillMigalhas({ d, kpi, muda }: { d: RespPainel; kpi: KpiDado; muda: (a: Record<string, string | string[] | null>) => void }) {
  const dr = d.drill!
  const v = (m1: number, m2: number, n: number) => valorFmt(calcula(kpi.calculo, m1, m2, n), kpi.formato)
  const abrir = (chaveItem: string) => {
    if (dr.indice === 0) muda({ secretaria: chaveItem, caminho: [] })
    else muda({ caminho: [...d.filtros.caminho, chaveItem] })
  }
  return (
    <section className="cartao sk-drill" aria-labelledby="sk-drill-t">
      <nav aria-label="Migalhas do detalhamento" className="sk-migalhas">
        <ol>
          {dr.migalhas.map((m, i) => (
            <li key={i}>
              {i === dr.migalhas.length - 1
                ? <span aria-current="page">{m.rotulo}</span>
                : <button type="button" className="link-btn" onClick={() => muda({ secretaria: m.secretaria, caminho: m.caminho })}>{m.rotulo}</button>}
            </li>
          ))}
        </ol>
      </nav>
      <h2 id="sk-drill-t">{kpi.rotulo} por {dr.nivel.toLowerCase()}</h2>
      {dr.suprimido ? <p className="sk-privacidade">Grupo pequeno, oculto por privacidade</p> : (
        <>
          <ul className="sk-lista">
            {dr.itens.map((i) => (
              <li key={i.chave} className={i.outros ? 'outros' : ''}>
                {i.abre
                  ? <button type="button" className="sk-item" onClick={() => abrir(i.chave)}><span>{i.rotulo}</span><span className="num">{v(i.m1, i.m2, i.n)}</span></button>
                  : <div className="sk-item"><span>{i.rotulo}{i.outros && <small> · {i.n} servidores em grupos com menos de {d.k}</small>}</span><span className="num">{v(i.m1, i.m2, i.n)}</span></div>}
              </li>
            ))}
          </ul>
          {dr.total && (
            <p className="sk-total" data-testid="soma-total">
              <span>Soma = total de {dr.migalhas[dr.migalhas.length - 1].rotulo}</span>
              <span className="num">{v(dr.total.m1, dr.total.m2, dr.total.n)}</span>
            </p>
          )}
        </>
      )}
    </section>
  )
}
