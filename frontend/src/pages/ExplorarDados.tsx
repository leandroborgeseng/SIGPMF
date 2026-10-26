import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { dataBR, dataHoraBR, ApiErro } from '../api'
import { useAuth } from '../auth'
import { Esqueleto } from '../componentes'
import {
  type Abrir, type Amostra, CAMADAS, type Camada, type Estado, type Funil, type Linhagem, PERFIS_EXPLORADOR, type Perfil, type Tabelas,
  MOTIVOS_PENDENCIA, UUID, explorar, formata, plural,
} from '../explorar'
import '../explorar.css'

const MOTIVO_MIN = 10
const LGPD = 'admin_lgpd'
const MSG_ERRO = 'Não foi possível carregar os dados.'
const MSG_FILTRO_LGPD = 'Filtro disponível só para o perfil LGPD'
type Aba = 'perfil' | 'amostra' | 'pendencias' | 'origem'
const ROTULO_PEND: Record<string, string> = { motivo: 'Motivo', tabela: 'Tabela', chave: 'Chave', data_carga: 'Data da carga' }
const ROTULO_AMOSTRA: Record<string, string> = { quarentena: 'Quarentena' }
const fmt = (v: unknown) => formata(v, dataBR, dataHoraBR)

/** Hook de carga com estado de erro e "Tentar de novo" que refaz a MESMA chamada (UX-E7). */
function useCarga<T>(rota: string, corpo: object | null) {
  const chave = corpo ? JSON.stringify(corpo) : ''
  const [estado, setEstado] = useState<{ dados: T | null; erro: boolean; carregando: boolean }>({ dados: null, erro: false, carregando: !!corpo })
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!chave) return
    let vivo = true
    setEstado((e) => ({ ...e, erro: false, carregando: true }))
    explorar<T>(rota, JSON.parse(chave)).then((d) => vivo && setEstado({ dados: d, erro: false, carregando: false }))
      .catch(() => vivo && setEstado({ dados: null, erro: true, carregando: false }))
    return () => { vivo = false }
  }, [rota, chave, n])
  return { ...estado, refazer: () => setN((x) => x + 1) }
}

function ErroCarga({ refazer }: { refazer: () => void }) {
  return (
    <div role="alert" className="aviso erro">
      <p>{MSG_ERRO} <button className="btn secundario" onClick={refazer}>Tentar de novo</button></p>
    </div>
  )
}

/** Abas com role=tablist e setas do teclado (UX-E9). */
function Abas<T extends string>({ rotulo, itens, ativa, onChange }: { rotulo: string; itens: { id: T; rotulo: ReactNode }[]; ativa: T; onChange: (id: T) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const tecla = (e: KeyboardEvent, i: number) => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!d) return
    e.preventDefault()
    const j = (i + d + itens.length) % itens.length
    onChange(itens[j].id)
    refs.current[j]?.focus()
  }
  return (
    <div role="tablist" aria-label={rotulo} className="ex-abas">
      {itens.map((it, i) => (
        <button key={it.id} ref={(el) => { refs.current[i] = el }} role="tab" aria-selected={ativa === it.id} tabIndex={ativa === it.id ? 0 : -1}
          className={`ex-aba${ativa === it.id ? ' ativa' : ''}`} onClick={() => onChange(it.id)} onKeyDown={(e) => tecla(e, i)}>{it.rotulo}</button>
      ))}
    </div>
  )
}

/** Select padrão PMF (mesma borda, raio e altura dos campos do SIGPMF). */
function SelectPMF({ rotulo, valor, opcoes, onChange, id }: { rotulo: string; valor: string; opcoes: string[]; onChange: (v: string) => void; id: string }) {
  return (
    <div className="pmf-campo">
      <label htmlFor={id}>{rotulo}</label>
      <select id={id} className="pmf-select" value={valor} onChange={(e) => onChange(e.target.value)}>
        {opcoes.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}

function Uuid({ v }: { v: string }) {
  const [ok, setOk] = useState(false)
  return (
    <span className="uuid">
      <code title={v}>{v.slice(0, 8)}…</code>
      <button className="icone" aria-label="Copiar identificador" title="Copiar"
        onClick={() => { void navigator.clipboard?.writeText(v); setOk(true); window.setTimeout(() => setOk(false), 1500) }}>{ok ? '✓' : '📋'}</button>
    </span>
  )
}

function Valor({ v }: { v: unknown }) {
  if (typeof v === 'string' && UUID.test(v)) return <Uuid v={v} />
  return <>{fmt(v)}</>
}

function haQuanto(iso?: string | null) {
  if (!iso) return null
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  return Number.isNaN(s) ? null : s
}
function FaixaProcessando({ e }: { e: Estado }) {
  if (!e.processando) return null
  const s = haQuanto(e.atualizado_em)
  const etapa = e.etapa_num ? `etapa ${e.etapa_num} de ${e.etapas_total ?? 3}` : e.etapa ? `etapa ${e.etapa}` : null
  return (
    <p role="status" className="faixa-proc">
      Processando…{etapa ? ` ${etapa}` : ''}{s !== null ? ` · atualizado há ${s}s` : ''} · números parciais
    </p>
  )
}

export default function ExplorarDados() {
  const { sessao } = useAuth()
  const permitido = !!sessao && PERFIS_EXPLORADOR.includes(sessao.perfil)
  if (!permitido) {
    return (
      <section className="explorar sem-acesso">
        <h1>Explorar dados</h1>
        <p role="alert">Você não tem acesso a esta área.</p>
        <Link className="btn" to="/dashboard">Voltar ao painel</Link>
      </section>
    )
  }
  return <Explorador lgpd={sessao.perfil === LGPD} />
}

function Explorador({ lgpd }: { lgpd: boolean }) {
  const cs = useCarga<{ conectores: { conector: string }[]; pode_revelar: boolean }>('conectores', {})
  const [conector, setConector] = useState('')
  const [camada, setCamada] = useState<Camada>('bronze')
  const [tabela, setTabela] = useState('')
  const [aba, setAba] = useState<Aba>('perfil')
  const [inicial, setInicial] = useState<{ filtros: Record<string, string>; n: number }>({ filtros: {}, n: 0 })
  const [cidadao, setCidadao] = useState('')
  const lista = cs.dados?.conectores.map((c) => c.conector) ?? []
  const atual = conector || lista[0] || ''

  const abrir = (a: Abrir & { conector?: string }) => {
    if (a.conector) setConector(a.conector)
    setCamada(a.camada); setTabela(a.tabela); setAba('amostra'); setInicial((i) => ({ filtros: a.filtros, n: i.n + 1 }))
  }
  const verPendencia = (motivo: string) => {
    setCamada('prata'); setAba('pendencias'); setInicial((i) => ({ filtros: { motivo }, n: i.n + 1 }))
  }
  const verOrigem = (cid: string) => { setCidadao(cid); setAba('origem') }

  return (
    <section className="explorar">
      <h1>Explorar dados</h1>
      {cs.erro && <ErroCarga refazer={cs.refazer} />}
      {cs.carregando && <Esqueleto linhas={5} texto="Carregando conectores…" />}
      {cs.dados && lista.length === 0 && <p className="vazio">Nenhum conector com dados carregados ainda.</p>}
      {cs.dados && lista.length > 0 && (
        <>
          <FunilQualidade conector={atual} verPendencia={verPendencia} />
          <CamadaEscolha key={atual} conectores={lista} conector={atual} setConector={(c) => { setConector(c); setTabela(''); setAba('perfil') }}
            camada={camada} setCamada={(c) => { setCamada(c); setTabela(''); setAba('perfil'); setInicial((i) => ({ filtros: {}, n: i.n + 1 })) }}
            tabela={tabela} setTabela={(t) => { setTabela(t); setInicial((i) => ({ filtros: {}, n: i.n + 1 })) }}>
            {(t) => (
              <>
                <Abas rotulo="Visão da tabela" ativa={aba} onChange={setAba} itens={[
                  { id: 'perfil' as Aba, rotulo: 'Perfil' }, { id: 'amostra' as Aba, rotulo: 'Amostra' },
                  ...(camada === 'prata' ? [{ id: 'pendencias' as Aba, rotulo: 'Pendências' }] : []),
                  ...(camada === 'ouro' ? [{ id: 'origem' as Aba, rotulo: 'Origem' }] : []),
                ]} />
                <div role="tabpanel">
                  {aba === 'perfil' && <PerfilTabela conector={atual} camada={camada} tabela={t} />}
                  {aba === 'amostra' && <AmostraTabela key={`${t}:${inicial.n}`} conector={atual} camada={camada} tabela={t} lgpd={lgpd}
                    filtrosIniciais={inicial.filtros} verOrigem={camada === 'ouro' ? verOrigem : undefined} />}
                  {aba === 'pendencias' && camada === 'prata' && <AmostraTabela key={`pend:${inicial.n}`} conector={atual} camada="prata" tabela={t}
                    lgpd={lgpd} pendencias filtrosIniciais={inicial.filtros} />}
                  {aba === 'origem' && camada === 'ouro' && <Origem cidadao={cidadao} setCidadao={setCidadao} abrir={abrir} />}
                </div>
              </>
            )}
          </CamadaEscolha>
        </>
      )}
      <footer className="ex-rodape">Visualização somente leitura · dados do SIGPMF, a origem da prefeitura não é consultada</footer>
    </section>
  )
}

function FunilQualidade({ conector, verPendencia }: { conector: string; verPendencia: (m: string) => void }) {
  const f = useCarga<Funil>('funil', { conector })
  if (f.erro) return <ErroCarga refazer={f.refazer} />
  if (!f.dados) return <Esqueleto linhas={5} texto="Carregando funil…" />
  const d = f.dados
  const etapas = [...new Set(d.descartes.map((x) => x.etapa))]
  return (
    <div className="cartao funil" aria-label="Funil de qualidade">
      <h2>Funil de qualidade</h2>
      <FaixaProcessando e={d.estado} />
      <ol className="funil-etapas">
        <li>Bronze <strong className="num">{d.bronze.toLocaleString('pt-BR')}</strong> <small>{plural(d.bronze_unicos, 'única', 'únicas')}</small></li>
        <li aria-hidden="true">→</li>
        <li>Prata <strong className="num">{d.prata.toLocaleString('pt-BR')}</strong> <small>{plural(d.prata_cidadaos, 'cidadão', 'cidadãos')}</small></li>
        <li aria-hidden="true">→</li>
        <li>Ouro <strong className="num">{d.ouro.toLocaleString('pt-BR')}</strong> <small>{d.ouro === 1 ? 'cidadão' : 'cidadãos'}</small></li>
      </ol>
      {etapas.length === 0 && <p>Sem descartes.</p>}
      {etapas.map((et) => (
        <div key={et} className="funil-grupo">
          <h3>{et}</h3>
          <ul>
            {d.descartes.filter((x) => x.etapa === et).map((x) => (
              <li key={x.motivo}>
                {et.startsWith('Bronze') && x.motivo !== 'versoes_duplicadas'
                  ? <button className="link" title={x.motivo} onClick={() => verPendencia(x.motivo)}>{x.rotulo}</button>
                  : <span title={x.motivo}>{x.rotulo}</span>}
                {' '}<span className="num">{x.quantidade.toLocaleString('pt-BR')}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {d.reconciliacao && (
        <p className={`reconc ${d.reconciliacao.ok ? 'ok' : 'divergente'}`} role="note">
          Reconciliação: origem <span className="num">{d.reconciliacao.origem?.toLocaleString('pt-BR') ?? '—'}</span> = bronze{' '}
          <span className="num">{d.reconciliacao.bronze.toLocaleString('pt-BR')}</span> + em quarentena{' '}
          <span className="num">{d.reconciliacao.quarentena.toLocaleString('pt-BR')}</span> ·{' '}
          <strong>{d.reconciliacao.ok ? (d.reconciliacao.quarentena ? 'ok com pendências' : 'ok') : 'divergente'}</strong>
        </p>
      )}
    </div>
  )
}

function CamadaEscolha({ conectores, conector, setConector, camada, setCamada, tabela, setTabela, children }: {
  conectores: string[]; conector: string; setConector: (c: string) => void; camada: Camada; setCamada: (c: Camada) => void
  tabela: string; setTabela: (t: string) => void; children: (t: string) => ReactNode
}) {
  const b = useCarga<Tabelas>('tabelas', { conector, camada: 'bronze' })
  const p = useCarga<Tabelas>('tabelas', { conector, camada: 'prata' })
  const o = useCarga<Tabelas>('tabelas', { conector, camada: 'ouro' })
  const por = { bronze: b, prata: p, ouro: o }
  const [busca, setBusca] = useState('')
  const atual = por[camada]
  const tabs = atual.dados?.tabelas ?? []
  const filtradas = tabs.filter((t) => t.tabela.toLowerCase().includes(busca.toLowerCase()))
  const escolhida = tabela && tabs.some((t) => t.tabela === tabela) ? tabela : (tabs.find((t) => t.linhas > 0)?.tabela ?? tabs[0]?.tabela ?? '')
  const info = tabs.find((t) => t.tabela === escolhida)
  const ultima = (c: typeof b) => c.dados ? [...c.dados.tabelas].map((t) => t.ultima_carga).filter(Boolean).sort().pop() ?? null : null
  return (
    <>
      <div className="ex-barra">
        <SelectPMF id="ex-conector" rotulo="Conector" valor={conector} opcoes={conectores} onChange={setConector} />
        <span className="sep" aria-hidden="true">▸</span>
        <div className="pmf-campo">
          <label htmlFor="ex-busca">Tabela</label>
          <input id="ex-busca" type="search" placeholder="Buscar tabela" value={busca} onChange={(e) => setBusca(e.target.value)} autoComplete="off" />
          <select aria-label="Tabela" className="pmf-select" value={escolhida} onChange={(e) => setTabela(e.target.value)}>
            {filtradas.map((t) => <option key={t.tabela} value={t.tabela}>{t.tabela} ({plural(t.linhas, 'linha', 'linhas')})</option>)}
          </select>
        </div>
      </div>
      <Abas rotulo="Camada" ativa={camada} onChange={setCamada} itens={CAMADAS.map((c) => ({
        id: c.id, rotulo: <span><strong>{c.rotulo}</strong><small> {por[c.id].dados ? plural(por[c.id].dados!.total, 'linha', 'linhas') : '…'} · última carga {dataHoraBR(ultima(por[c.id]))}</small></span>,
      }))} />
      {atual.erro && <ErroCarga refazer={atual.refazer} />}
      {atual.carregando && <Esqueleto linhas={5} texto="Carregando tabelas…" />}
      {atual.dados && (
        <>
          {/* UX: o aviso "Processando… etapa N de 3" fica só no funil, não repetido na camada */}
          {(atual.dados.vazia || !info || info.linhas === 0) && <p className="vazio">Esta camada ainda não foi processada para esta tabela.</p>}
          {camada === 'prata' && escolhida && (atual.dados.vazia || !info || info.linhas === 0) && (
            <div className="detalhe">{children(escolhida)}</div>  /* Pendências (ex.: quarentena CC9) existem antes da prata */
          )}
          {atual.dados.vazia || !info || info.linhas === 0
            ? null
            : (
              <div className="detalhe">
                <h2>{escolhida} <small>({plural(info.linhas, 'linha', 'linhas')}{atual.dados.estado.processando ? ', parcial' : ''})</small></h2>
                {children(escolhida)}
              </div>
            )}
        </>
      )}
    </>
  )
}

function PerfilTabela({ conector, camada, tabela }: { conector: string; camada: Camada; tabela: string }) {
  const r = useCarga<Perfil>('perfil', { conector, camada, tabela })
  if (r.erro) return <ErroCarga refazer={r.refazer} />
  if (!r.dados) return <Esqueleto linhas={5} texto="Carregando perfil…" />
  const p = r.dados
  return (
    <>
      {p.aprox && <p className="nota">% preenchido e distintos são aproximados (amostra de {plural(p.amostra_perfil, 'linha', 'linhas')}).</p>}
      <table>
        <caption>Perfil das colunas</caption>
        <thead><tr><th scope="col">Coluna</th><th scope="col">Tipo</th><th scope="col" className="num">% preenchido</th>
          <th scope="col" className="num">Distintos</th><th scope="col">Exemplos</th></tr></thead>
        <tbody>{p.colunas.map((c) => (
          <tr key={c.coluna}>
            <th scope="row">{c.coluna}{c.sensivel && <span className="cadeado" title="Dado pessoal, mascarado" aria-label="dado pessoal"> 🔒</span>}</th>
            <td><span title={c.tipo_tecnico}>{c.tipo}</span></td>
            <td className="num">{c.preenchido_pct.toLocaleString('pt-BR')}%{c.aprox && <small> aprox.</small>}</td>
            <td className="num">{c.aprox ? '≈ ' : ''}{c.distintos.toLocaleString('pt-BR')}{c.aprox && <small> aprox.</small>}</td>
            <td>{c.exemplos.length ? c.exemplos.map((e, i) => <span key={i}>{i > 0 && ' · '}<Valor v={e} /></span>) : '—'}</td>
          </tr>))}
        </tbody>
      </table>
    </>
  )
}

function AmostraTabela({ conector, camada, tabela, lgpd, pendencias = false, filtrosIniciais, verOrigem }: {
  conector: string; camada: Camada; tabela: string; lgpd: boolean; pendencias?: boolean; filtrosIniciais: Record<string, string>; verOrigem?: (cid: string) => void
}) {
  const [pagina, setPagina] = useState(1)
  const [rascunho, setRascunho] = useState<Record<string, string>>(filtrosIniciais)
  const [filtros, setFiltros] = useState<Record<string, string>>(filtrosIniciais)
  const [motivoFiltro, setMotivoFiltro] = useState('')
  const [motivoAplicado, setMotivoAplicado] = useState('')
  const [revelados, setRevelados] = useState<Record<string, { v: unknown; ate: number }>>({})
  const [revelar, setRevelar] = useState<{ id: unknown; coluna: string } | null>(null)
  const [agora, setAgora] = useState(Date.now())
  const r = useCarga<Amostra>('amostra', { conector, camada, tabela, pagina, filtros, somente_pendencias: pendencias, ...(motivoAplicado ? { motivo_filtro: motivoAplicado } : {}) })
  const [ultima, setUltima] = useState<Amostra | null>(null)
  useEffect(() => { if (r.dados) setUltima(r.dados) }, [r.dados])
  const am = r.dados ?? ultima
  useEffect(() => {
    if (!Object.keys(revelados).length) return
    const t = window.setInterval(() => {
      const n = Date.now()
      setAgora(n)
      setRevelados((rv) => Object.fromEntries(Object.entries(rv).filter(([, x]) => x.ate > n)))
    }, 1000)
    return () => window.clearInterval(t)
  }, [revelados])

  const sens = new Set(am?.colunas_sensiveis ?? [])
  // P1: na bronze (sem catálogo) qualquer filtro além de id é só do perfil LGPD, com motivo
  const restrito = (c: string) => sens.has(c) || (camada === 'bronze' && !pendencias && c !== 'id')
  const colsFiltro = pendencias ? ['motivo'] : (am?.colunas ?? []).filter((c) => !['extraido_em', 'quarentena'].includes(c)).slice(0, 6)
  const temSensivel = Object.entries(rascunho).some(([k, v]) => v && restrito(k))
  const aplicar = (e: React.FormEvent) => {
    e.preventDefault()
    if (temSensivel && motivoFiltro.trim().length < MOTIVO_MIN) return
    setPagina(1); setFiltros(Object.fromEntries(Object.entries(rascunho).filter(([, v]) => v)))
    setMotivoAplicado(temSensivel ? motivoFiltro.trim() : '')
  }
  const chave = (id: unknown, c: string) => `${String(id)}:${c}`

  return (
    <div>
      <form onSubmit={aplicar} className="ex-filtros" aria-label="Filtros">
        {colsFiltro.map((c) => {
          const bloqueado = restrito(c) && !lgpd && !pendencias
          if (pendencias && c === 'motivo') {
            return (
              <div key={c} className="pmf-campo">
                <label htmlFor="f-motivo-pend">Motivo</label>
                <select id="f-motivo-pend" className="pmf-select" value={rascunho.motivo ?? ''} onChange={(e) => setRascunho({ ...rascunho, motivo: e.target.value })}>
                  <option value="">Todos os motivos</option>
                  {MOTIVOS_PENDENCIA.map((m) => <option key={m.codigo} value={m.codigo}>{m.rotulo}</option>)}
                </select>
              </div>)
          }
          return (
            <div key={c} className="pmf-campo">
              <label htmlFor={`f-${c}`}>{c}{bloqueado && <span className="cadeado" aria-hidden="true"> 🔒</span>}</label>
              {/* campo bloqueado: readOnly + aria-disabled para continuar focável e anunciar o motivo (aria-describedby) */}
              <input id={`f-${c}`} value={rascunho[c] ?? ''} readOnly={bloqueado} aria-disabled={bloqueado || undefined}
                className={bloqueado ? 'bloqueado' : undefined} title={bloqueado ? MSG_FILTRO_LGPD : 'Busca por valor exato'}
                aria-describedby={bloqueado ? `f-${c}-lgpd` : undefined}
                onChange={(e) => { if (!bloqueado) setRascunho({ ...rascunho, [c]: e.target.value }) }} placeholder="valor exato" autoComplete="off" />
              {bloqueado && <small id={`f-${c}-lgpd`} className="dica-lgpd">{MSG_FILTRO_LGPD}</small>}
            </div>)
        })}
        {lgpd && temSensivel && (
          <div className="pmf-campo motivo">
            <label htmlFor="f-motivo">Motivo do filtro em dado pessoal</label>
            <input id="f-motivo" value={motivoFiltro} onChange={(e) => setMotivoFiltro(e.target.value)} placeholder={`mínimo ${MOTIVO_MIN} caracteres`} />
          </div>
        )}
        <button className="btn" type="submit" disabled={temSensivel && motivoFiltro.trim().length < MOTIVO_MIN}>Filtrar</button>
      </form>
      {r.erro && <ErroCarga refazer={r.refazer} />}
      {!am && r.carregando && <Esqueleto linhas={5} texto="Carregando amostra…" />}
      {am && (
        <>
          <div className="tabela-rolagem">
            <table>
              <caption>{pendencias ? 'Pendências' : 'Amostra'} · Página {am.pagina}</caption>
              <thead><tr>{am.colunas.map((c) => <th key={c} scope="col">{pendencias ? (ROTULO_PEND[c] ?? c) : (ROTULO_AMOSTRA[c] ?? c)}</th>)}</tr></thead>
              <tbody>
                {am.linhas.length === 0 && <tr><td colSpan={am.colunas.length}>Nenhuma linha encontrada.</td></tr>}
                {am.linhas.map((l, i) => {
                  const id = l[am.id_coluna]
                  return (
                    <tr key={String(id ?? i) + i}>{am.colunas.map((c) => {
                      const k = chave(id, c)
                      const rv = revelados[k]
                      const v = rv ? rv.v : l[c]
                      return (
                        <td key={c} className={typeof v === 'number' ? 'num' : undefined}>
                          {c === 'motivo' && pendencias ? <span title={String(l.motivo_codigo ?? '')}>{String(v)}</span>
                            : am.cidadao_coluna === c && verOrigem && typeof l[c] === 'string'
                              ? <><Valor v={v} /> <button className="link" onClick={() => verOrigem(String(l[c]))}>origem</button></>
                              : <Valor v={v} />}
                          {rv && <small className="contagem" aria-live="polite"> (volta a mascarar em {Math.max(0, Math.ceil((rv.ate - agora) / 1000))}s)</small>}
                          {lgpd && !pendencias && sens.has(c) && l[c] != null && !rv && (
                            <button className="link revelar" aria-label={`Revelar ${c}`} onClick={() => setRevelar({ id, coluna: c })}>Revelar</button>)}
                        </td>)
                    })}</tr>)
                })}
              </tbody>
            </table>
          </div>
          <nav className="paginacao" aria-label="Paginação">
            <button className="btn secundario" disabled={!am.tem_anterior} onClick={() => setPagina(pagina - 1)}>Anterior</button>
            <span>Página {am.pagina}</span>
            <button className="btn secundario" disabled={!am.tem_proxima} onClick={() => setPagina(pagina + 1)}>Próxima</button>
          </nav>
        </>
      )}
      {revelar && <DialogoRevelar conector={conector} camada={camada} tabela={tabela} alvo={revelar} fechar={() => setRevelar(null)}
        revelado={(v, s) => { const n = Date.now(); setAgora(n); setRevelados((x) => ({ ...x, [chave(revelar.id, revelar.coluna)]: { v, ate: n + s * 1000 } })); setRevelar(null) }} />}
    </div>
  )
}

function DialogoRevelar({ conector, camada, tabela, alvo, fechar, revelado }: {
  conector: string; camada: Camada; tabela: string; alvo: { id: unknown; coluna: string }; fechar: () => void; revelado: (v: unknown, s: number) => void
}) {
  const [motivo, setMotivo] = useState('')
  const [erro, setErro] = useState<string | null>(null)
  const enviar = async () => {
    try {
      const r = await explorar<{ valor: unknown; remascarar_em_segundos: number }>('revelar', { conector, camada, tabela, linha_id: alvo.id, coluna: alvo.coluna, motivo })
      revelado(r.valor, r.remascarar_em_segundos)
    } catch (e) {
      setErro(e instanceof ApiErro && e.status === 400 ? `Informe o motivo (mínimo ${MOTIVO_MIN} caracteres).` : 'Não foi possível revelar.')
    }
  }
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="t-rev" className="dialogo">
      <h2 id="t-rev">Revelar {alvo.coluna}</h2>
      <label htmlFor="rev-motivo">Motivo</label>
      <textarea id="rev-motivo" value={motivo} onChange={(e) => setMotivo(e.target.value)} />
      {erro && <p role="alert">{erro}</p>}
      <p className="nota">O valor volta a ficar mascarado depois de 60 segundos. A revelação fica registrada na auditoria.</p>
      <button className="btn" onClick={enviar} disabled={motivo.trim().length < MOTIVO_MIN}>Revelar</button>
      <button className="btn secundario" onClick={fechar}>Cancelar</button>
    </div>
  )
}

function Origem({ cidadao, setCidadao, abrir }: { cidadao: string; setCidadao: (c: string) => void; abrir: (a: Abrir & { conector?: string }) => void }) {
  const [rasc, setRasc] = useState(cidadao)
  const r = useCarga<Linhagem>('linhagem', cidadao ? { cidadao_id: cidadao } : null)
  return (
    <div className="linhagem">
      <form className="ex-filtros" aria-label="Escolher cidadão" onSubmit={(e) => { e.preventDefault(); setCidadao(rasc.trim()) }}>
        <div className="pmf-campo"><label htmlFor="o-cid">cidadao_id</label>
          <input id="o-cid" value={rasc} onChange={(e) => setRasc(e.target.value)} placeholder="valor exato" autoComplete="off" /></div>
        <button className="btn" type="submit">Ver origem</button>
      </form>
      {!cidadao && <p className="nota">Escolha um cidadão na aba Amostra (link "origem") ou informe o identificador.</p>}
      {r.erro && <ErroCarga refazer={r.refazer} />}
      {cidadao && r.carregando && <Esqueleto linhas={5} texto="Carregando origem…" />}
      {r.dados && (
        <table>
          <caption>Veio de</caption>
          <thead><tr><th scope="col">Conector</th><th scope="col">Tabela</th><th scope="col">Chave</th><th scope="col">Camada</th><th scope="col"><span className="sr-only">Abrir</span></th></tr></thead>
          <tbody>{r.dados.veio_de.map((v, i) => (
            <tr key={i}><td>{v.conector}</td><td>{v.tabela}</td><td>{v.chave}</td><td>{v.camada === 'bronze' ? 'Bronze' : 'Prata'}</td>
              <td><button className="link" onClick={() => abrir({ ...v.abrir, conector: v.conector })}>Ver linha na {v.camada === 'bronze' ? 'Bronze' : 'Prata'}</button></td></tr>))}
          </tbody>
        </table>
      )}
    </div>
  )
}
