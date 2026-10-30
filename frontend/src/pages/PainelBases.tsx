import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ApiErro, dataHoraBR } from '../api'
import { Erro, Esqueleto } from '../componentes'
import CargaCompleta from '../CargaCompleta'
import ExecucaoPendente from '../ExecucaoPendente'
import PainelAmostra, { estadoAmostra, type EstadoAmostra, type ResumoAmostra, SELO_AMOSTRA } from '../PainelAmostra'

export type Contagens = { bronze: number | null; prata: number | null; ouro: number | null; pendentes: number | null }  // null = cache frio (#56)
export type ErroCarga = { classe: string; numero: number | null; tabela: string | null; objeto: string | null; local: string | null; motivo: string }
export type UltimaCarga = { id: number; tipo: string; status: string; iniciado_em: string; finalizado_em: string | null; lidos: number; mensagem: string | null; erro?: ErroCarga | null
  tipo_texto?: string; texto_limite?: string | null }
export type Base = {
  id: number; nome: string; descricao: string | null; grupo: string; triagem: string; status: string; prioridade: number | null
  tem_conector: boolean; conector_id?: string | null; pode_carregar: boolean; motivo_bloqueio: string | null; tem_amostra: boolean; com_erro: boolean; origem_vazia?: boolean
  completa_disponivel?: boolean; completa_liberada?: boolean; completa_motivo?: string | null
  completa_em_andamento?: boolean
  exige_amostra?: boolean; amostra?: ResumoAmostra | null; amostra_limite?: number
  bloqueio?: Bloqueio | null
  contagens: Contagens; ultima_carga: UltimaCarga | null
}
/** CC12: execução (amostra ou completa) que segura o conector. */
export type Bloqueio = {
  execucao_id: number; tipo: 'amostra' | 'completa'; tipo_rotulo: string; iniciado_em: string; atualizado_em: string | null
  viva: boolean; parada_min: number; alerta_min: number; parada: boolean; mensagem: string
}
type Modo = 'amostra' | 'completa' | 'reprocessar'

const STATUS: Record<string, { rotulo: string; classe: string }> = {
  implementado: { rotulo: 'Pronto', classe: 'pronto' },
  mapeado: { rotulo: 'Mapeado', classe: 'mapeado' },
  triado: { rotulo: 'Só triado', classe: 'triado' },
  excluido: { rotulo: 'Descartado', classe: 'descartado' },
}
const GRUPO: Record<string, string> = { painel_cidadao: 'Cidadão', indicadores: 'Indicadores', descartavel: 'Descartável' }
const FILTROS = [
  { id: 'todas', rotulo: 'Todas' }, { id: 'implementado', rotulo: 'Prontas' }, { id: 'mapeado', rotulo: 'Mapeadas' },
  { id: 'triado', rotulo: 'Só triadas' }, { id: 'erro', rotulo: 'Com erro' }, { id: 'excluido', rotulo: 'Descartadas' },
] as const
const TIPO: Record<string, string> = { carga_demo_amostra: 'Amostra', amostra_conector: 'Amostra', carga_municipe_amostra: 'Amostra', carga_demo_completa: 'Completa', reprocessamento: 'Reprocessamento' }
const ROTULO_MODO: Record<Modo, string> = { amostra: 'Puxar amostra', completa: 'Puxar carga completa', reprocessar: 'Reprocessar' }

/** Contagem com concordância: 1 → singular; 0 ou ≥ 2 → plural. */
export const plural = (n: number, um: string, varios: string) => `${new Intl.NumberFormat('pt-BR').format(n)} ${n === 1 ? um : varios}`
export const milhar = (n: number | null | undefined) => (n == null ? '—' : new Intl.NumberFormat('pt-BR').format(n))
export function perda(de: number | null, para: number | null): number | null {
  if (de == null || para == null || de <= 0 || para >= de) return null
  const p = Math.round(((de - para) / de) * 100)
  return p > 0 ? p : null  // nunca "−0%"
}

export const CARGA_EM_PREPARACAO = 'Carga real em preparação'
export type ExecAmostra = {
  id: number; status: string; lidos: number; mensagem: string | null; em_andamento?: boolean
  etapa?: string | null; processadas_etapa?: number | null; total_etapa?: number | null
}
export const INTERVALO_AMOSTRA_MS = 2000
const ETAPA_AMOSTRA: Record<string, string> = { leitura: 'lendo da origem', prata: 'tratando (prata)', ouro: 'unificando cidadãos (ouro)', concluida: 'concluindo' }
const espera = (ms: number) => new Promise((ok) => setTimeout(ok, ms))
export function textoAndamento(nome: string, r: ExecAmostra): string {
  const etapa = ETAPA_AMOSTRA[r.etapa ?? 'leitura'] ?? 'em andamento'
  const cont = r.total_etapa ? ` (${milhar(r.processadas_etapa ?? 0)} de ${milhar(r.total_etapa)})` : ''
  return `Amostra de ${nome} em andamento: ${etapa}${cont}.`
}
export const MAX_FALHAS_ACOMPANHAMENTO = 3
export const MSG_SEM_ACOMPANHAMENTO = 'Não foi possível acompanhar a amostra agora. Ela continua no servidor; atualize a página em instantes.'
/** Falha só na consulta de status (rede ou 5xx): não é falha da carga (Designer UX, TL 27/10). */
const falhaDeAcompanhamento = (e: unknown) => !(e instanceof ApiErro) || e.status >= 500
class SemAcompanhamento extends Error {}
export const MSG_ORIGEM_VAZIA = 'Nenhuma linha lida da origem. Verifique a conexão ou o banco configurado.'

export const ATUALIZANDO_CONTAGENS = 'Atualizando contagens…'

export default function PainelBases({ bases, erro, onRecarregar, ehAdmin, cargaHabilitada = false, contagensAtualizando = false }: {
  bases: Base[] | null; erro: boolean; onRecarregar: () => void; ehAdmin: boolean; cargaHabilitada?: boolean; contagensAtualizando?: boolean
}) {
  // #56: sem retrato das contagens ainda, a tela diz que está atualizando em vez de mostrar zeros
  const [pendentes, setPendentes] = useState<Set<string>>(new Set())
  const lerAgenda = useCallback(() => {
    api<unknown>('/agenda').then((r) => {
      const itens = (r as { itens?: { conector: string; forcar?: boolean }[] })?.itens
      setPendentes(new Set(Array.isArray(itens) ? itens.filter((i) => i.forcar === true).map((i) => i.conector) : []))
    }).catch(() => setPendentes(new Set()))
  }, [])
  useEffect(lerAgenda, [lerAgenda, bases])
  const tv = (v: number | null) => (contagensAtualizando ? ATUALIZANDO_CONTAGENS : milhar(v))
  const [filtro, setFiltro] = useState<string>('todas')
  const [busca, setBusca] = useState('')
  const [rodando, setRodando] = useState<number | null>(null)
  const [confirmar, setConfirmar] = useState<{ base: Base; modo: Modo } | null>(null)
  const [aviso, setAviso] = useState<{ texto: string; erro: boolean } | null>(null)

  const visiveis = useMemo(() => (bases ?? []).filter((b) => {
    if (busca && !b.nome.toLowerCase().includes(busca.toLowerCase())) return false
    if (filtro === 'todas') return b.status !== 'excluido'
    if (filtro === 'erro') return b.com_erro
    return b.status === filtro
  }), [bases, filtro, busca])
  const soma = (a: number | null, v: number | null) => (a == null || v == null ? null : a + v)  // null (cache frio) mostra "—"
  const tot = useMemo(() => visiveis.reduce<{ bronze: number | null; prata: number | null; ouro: number | null }>((a, b) => ({
    bronze: soma(a.bronze, b.contagens.bronze), prata: soma(a.prata, b.contagens.prata), ouro: soma(a.ouro, b.contagens.ouro),
  }), { bronze: 0, prata: 0, ouro: 0 }), [visiveis])
  const ultima = useMemo(() => visiveis.map((b) => b.ultima_carga?.iniciado_em).filter(Boolean).sort().pop() ?? null, [visiveis])

  const executar = async (b: Base, modo: Modo) => {
    setConfirmar(null); setRodando(b.id); setAviso(null)
    try {
      // Puxar amostra = carga real por conector (política de tabelas, admin), liberada também em produção.
      // Responde 202 e roda em background (TL 27/10): a tela acompanha etapa e sinal de vida até terminar.
      // Carga completa e reprocessar seguem pela rota sintética, só em dev/test.
      let r: ExecAmostra
      if (modo === 'amostra') {
        const id = b.conector_id ?? b.id
        r = await api<ExecAmostra>(`/conectores/${id}/amostra`, { method: 'POST' })
        while (r.em_andamento) {
          setAviso({ texto: textoAndamento(b.nome, r), erro: false })
          let falhas = 0
          for (;;) {
            await espera(INTERVALO_AMOSTRA_MS)
            try {
              r = await api<ExecAmostra>(`/conectores/${id}/amostra/${r.id}`)
              break
            } catch (e) {
              if (!falhaDeAcompanhamento(e)) throw e
              falhas += 1
              if (falhas >= MAX_FALHAS_ACOMPANHAMENTO) throw new SemAcompanhamento()
            }
          }
        }
      } else {
        r = await api<ExecAmostra>(`/conectores/${b.id}/executar`, { method: 'POST', body: JSON.stringify({ modo }) })
      }
      if (r.status === 'falha') setAviso({ texto: `Falha na carga de ${b.nome}: ${r.mensagem ?? 'erro'}.`, erro: true })
      else setAviso({ texto: `${ROTULO_MODO[modo]} de ${b.nome} concluída (${r.status === 'sem_novidades' ? 'sem novidades' : 'sucesso'}). Contagens atualizadas abaixo.`, erro: false })
      onRecarregar()
    } catch (e) {
      if (e instanceof SemAcompanhamento) { setAviso({ texto: MSG_SEM_ACOMPANHAMENTO, erro: false }); return }
      setAviso({ texto: `Falha na carga de ${b.nome}: ${e instanceof ApiErro ? e.message : 'erro inesperado'}`, erro: true })
    } finally {
      setRodando(null)
    }
  }
  const pedir = (b: Base, modo: Modo) => (modo === 'amostra' ? executar(b, modo) : setConfirmar({ base: b, modo }))

  const todas = bases ?? []
  const conta = (s: string) => todas.filter((b) => b.status === s).length

  return (
    <section aria-labelledby="t-bases" className="painel-bases">
      <h2 id="t-bases">Bases de origem</h2>
      {erro ? <Erro texto="Não foi possível carregar os conectores." onRetry={onRecarregar} />
        : !bases ? <Esqueleto linhas={4} />
        : bases.length === 0 ? <p className="vazio">Nenhuma base cadastrada.</p>
        : (
          <>
            <p className="subtitulo">{plural(todas.length, 'base cadastrada', 'bases cadastradas')} · {plural(conta('implementado'), 'pronta', 'prontas')} · {plural(conta('mapeado'), 'mapeada', 'mapeadas')} · {plural(conta('triado'), 'triada', 'triadas')} · {plural(conta('excluido'), 'descartada', 'descartadas')}</p>
            <ul className="totais" aria-label="Totais por camada das bases visíveis">
              <li className="cartao"><span className="cartao-rotulo">Bronze</span> <span className="cartao-valor">{tv(tot.bronze)}</span></li>
              <li className="cartao"><span className="cartao-rotulo">Prata</span> <span className="cartao-valor">{tv(tot.prata)}</span></li>
              <li className="cartao"><span className="cartao-rotulo">Ouro (cidadãos únicos)</span> <span className="cartao-valor">{tv(tot.ouro)}</span></li>
            </ul>
            <p>Última carga: {ultima ? dataHoraBR(ultima) : 'Nunca carregado'}</p>
            <div className="filtros" role="group" aria-label="Filtrar bases">
              {FILTROS.map((f) => (
                <button key={f.id} className={`chip${filtro === f.id ? ' ativo' : ''}`} aria-pressed={filtro === f.id} onClick={() => setFiltro(f.id)}>{f.rotulo}</button>
              ))}
              <label htmlFor="busca-base" className="sr-only">Buscar base pelo nome</label>
              <input id="busca-base" type="search" placeholder="Buscar base" value={busca} onChange={(e) => setBusca(e.target.value)} />
            </div>
            <div aria-live="polite">{aviso && <p role={aviso.erro ? 'alert' : undefined} aria-live="polite" className={`aviso${aviso.erro ? ' erro' : ''}`}>{aviso.texto}</p>}</div>
            {confirmar && (
              <div role="dialog" aria-modal="false" aria-labelledby="t-conf" className="cartao confirmar">
                <p id="t-conf">{confirmar.modo === 'completa'
                  ? `Puxar carga completa de ${confirmar.base.nome}? Isso pode levar vários minutos e não apaga dados.`
                  : `Reprocessar prata e ouro de ${confirmar.base.nome} a partir do bronze? Não apaga dados da origem.`}</p>
                <button className="btn secundario" onClick={() => setConfirmar(null)}>Cancelar</button>{' '}
                <button className="btn" onClick={() => executar(confirmar.base, confirmar.modo)}>{ROTULO_MODO[confirmar.modo]}</button>
              </div>
            )}
            {visiveis.length === 0 ? <p className="vazio">Nenhuma base neste filtro.</p> : (
              <ul className="lista-cartoes bases" aria-label="Bases">
                {visiveis.map((b) => <CartaoBase atualizando={contagensAtualizando} pendente={!!b.conector_id && pendentes.has(b.conector_id)} onAgenda={lerAgenda} key={b.id} b={b} ehAdmin={ehAdmin} habilitada={cargaHabilitada} rodando={rodando} onAcao={pedir} onFim={onRecarregar} />)}
              </ul>
            )}
          </>
        )}
    </section>
  )
}

function CartaoBase({ b, ehAdmin, habilitada, rodando, onAcao, onFim, atualizando = false, pendente = false, onAgenda }: { b: Base; ehAdmin: boolean; habilitada: boolean; rodando: number | null; onAcao: (b: Base, m: Modo) => void; onFim: () => void; atualizando?: boolean; pendente?: boolean; onAgenda?: () => void }) {
  const seloAmostra = b.tem_conector && b.exige_amostra === true && !!b.conector_id
  const exige = ehAdmin && seloAmostra
  const [estAm, setEstAm] = useState<EstadoAmostra>(estadoAmostra(b.amostra ?? null, false))
  // M1: fonte que exige amostra: o selo segue o estado da amostra; o verde só depois da aprovação
  const st = seloAmostra ? SELO_AMOSTRA[estAm] : (STATUS[b.status] ?? { rotulo: b.status, classe: 'triado' })
  const c = b.contagens
  const n = (v: number | null) => (!b.tem_conector ? '—' : atualizando ? ATUALIZANDO_CONTAGENS : milhar(v))
  const pPrata = b.tem_conector ? perda(c.bronze, c.prata) : null
  const rodandoAlgo = rodando !== null
  const ocupado = rodandoAlgo || !habilitada
  const u = b.ultima_carga
  const [ccRodando, setCcRodando] = useState(b.completa_em_andamento === true)
  const bl = b.bloqueio ?? null
  const travada = bl !== null && !bl.parada  // viva e com atividade recente: não dá para puxar outra
  return (
    <li className="cartao base" aria-labelledby={`b-${b.id}`} id={b.conector_id ? `fonte-${b.conector_id}` : undefined}>
      <h3 id={`b-${b.id}`}>{b.nome} <span className="sutil">· {GRUPO[b.grupo] ?? b.grupo}{b.prioridade ? ` · fila ${b.prioridade}` : ''}</span></h3>
      <p>
        <span className={`selo ${st.classe}`}><i aria-hidden="true" />{st.rotulo}</span>
        {b.com_erro && <> <span className="selo falha"><i aria-hidden="true" />Última carga falhou</span></>}
      </p>
      {b.descricao && <p className="sutil">{b.descricao}</p>}
      {bl && <AvisoBloqueio bl={bl} nome={b.nome} conector={b.conector_id ?? String(b.id)} ehAdmin={ehAdmin} onFim={onFim} />}
      <p>Última carga: {u ? `${dataHoraBR(u.iniciado_em)} · ${u.tipo_texto ?? TIPO[u.tipo] ?? u.tipo} · ${u.status === 'origem_vazia' ? 'origem vazia' : u.status}` : 'Nunca carregado'}</p>
      {u?.texto_limite && <p className="aviso-ambar" role="status">{u.texto_limite}</p>}
      {u?.status === 'origem_vazia' && <p className="aviso-ambar" role="status">{MSG_ORIGEM_VAZIA}</p>}
      {u?.status === 'falha' && u.erro && (
        <div className="motivo-falha">
          <p title={u.erro.motivo}>Motivo: {u.erro.motivo}</p>
          <details>
            <summary>Ver detalhes</summary>
            <dl>
              <dt>Erro</dt><dd>{u.erro.classe}{u.erro.numero != null ? ` · SQL Server ${u.erro.numero}` : ''}</dd>
              {u.erro.tabela && <><dt>Tabela</dt><dd>{u.erro.tabela}</dd></>}
              {u.erro.objeto && <><dt>Objeto</dt><dd>{u.erro.objeto}</dd></>}
              {u.erro.local && <><dt>Onde</dt><dd><code>{u.erro.local}</code></dd></>}
            </dl>
          </details>
        </div>
      )}
      <dl className="camadas">
        <div><dt>Bronze</dt><dd>{n(c.bronze)}</dd></div>
        <div><dt>Prata</dt><dd>{n(c.prata)}</dd></div>
        <div><dt>Ouro <span className="sutil">(cidadãos únicos)</span></dt><dd>{n(c.ouro)}</dd></div>
      </dl>
      {ccRodando && <p className="sutil parciais">Em processamento, números parciais</p>}
      {pPrata !== null && !ccRodando && (
        <p className="perda" title="Diferença bronze → prata: descartes, duplicatas e pendências de correspondência">
          −{pPrata}% na prata
          <span className="sr-only"> (descartes, duplicatas e pendências de correspondência)</span>
        </p>
      )}
      {!ccRodando && <p>Pendências: {b.tem_conector ? `${milhar(c.pendentes)} sem correspondência` : '—'}</p>}
      {!b.tem_conector && <p className="sutil">{b.status === 'excluido' ? 'Base descartada na triagem' : 'Conector ainda não implementado'}</p>}
      {b.conector_id && ehAdmin && <ExecucaoPendente conector={b.conector_id} nome={b.nome} pendente={pendente} onMudou={() => onAgenda?.()} />}
      {exige && b.conector_id && (
        <div className="acoes" id={`acoes-${b.conector_id}`}>
          <PainelAmostra conector={b.conector_id} nome={b.nome} limite={b.amostra_limite ?? 1000} inicial={b.amostra ?? null}
            onMudou={onFim} desabilitado={rodandoAlgo || travada} onEstado={setEstAm} pendente={pendente} />
          {travada && bl && <p className="sutil motivo-trava" id={`motivo-trava-${b.id}`}>Indisponível: {bl.mensagem}</p>}
          <CargaCompleta key={`${b.conector_id}-${b.completa_liberada === true}`} conector={b.conector_id} liberada={b.completa_liberada === true}
            motivo={b.completa_motivo ?? null} onFim={onFim} onRodando={setCcRodando} />
          <details className="menu-acoes">
            <summary>Mais ações de {b.nome}</summary>
            <button className="btn outlined" disabled={ocupado || !b.pode_carregar} aria-label={`Reprocessar ${b.nome}`} onClick={() => onAcao(b, 'reprocessar')}>Reprocessar</button>
          </details>
        </div>
      )}
      {ehAdmin && b.tem_conector && !(b.exige_amostra && b.conector_id) && (
        <div className="acoes">
          <button className="btn" disabled={rodandoAlgo || !b.pode_carregar || travada} aria-label={`Puxar amostra de ${b.nome}`}
            aria-describedby={travada ? `motivo-trava-${b.id}` : undefined} onClick={() => onAcao(b, 'amostra')}>
            {rodando === b.id ? 'Carregando…' : 'Puxar amostra'}
          </button>
          {!b.pode_carregar && <span className="sutil"> {b.motivo_bloqueio}</span>}
          {b.pode_carregar && travada && bl && (
            <p className="sutil motivo-trava" id={`motivo-trava-${b.id}`}>Indisponível: {bl.mensagem}</p>
          )}
          {b.completa_disponivel && b.conector_id && (
            <CargaCompleta conector={b.conector_id} liberada={b.completa_liberada === true} motivo={b.completa_motivo ?? null} onFim={onFim}
              onRodando={setCcRodando} />
          )}
          <details className="menu-acoes">
            <summary>Mais ações de {b.nome}</summary>
            {b.completa_disponivel && b.conector_id ? null : <>
              <button className="btn outlined" disabled={ocupado || !b.pode_carregar || !b.tem_amostra} aria-label={`Puxar carga completa de ${b.nome}`} onClick={() => onAcao(b, 'completa')}>Carga completa</button>
              {!habilitada ? <span className="sutil"> {CARGA_EM_PREPARACAO}</span>
                : !b.tem_amostra && <span className="sutil"> Libera depois da conferência da amostra</span>}
            </>}
            <button className="btn outlined" disabled={ocupado || !b.pode_carregar} aria-label={`Reprocessar ${b.nome}`} onClick={() => onAcao(b, 'reprocessar')}>Reprocessar</button>
          </details>
        </div>
      )}
    </li>
  )
}

/** CC12 (Designer UX): aviso âmbar com tipo, início e última atividade; "Interromper" só para admin, com confirmação. */
export function AvisoBloqueio({ bl, nome, conector, ehAdmin, onFim }: { bl: Bloqueio; nome: string; conector: string; ehAdmin: boolean; onFim: () => void }) {
  const [abrir, setAbrir] = useState(false)
  const [motivo, setMotivo] = useState('')
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)
  const tipo = bl.tipo === 'completa' ? 'Carga completa' : 'Amostra'
  const interromper = async () => {
    setEnviando(true); setErro(null)
    try {
      await api(`/conectores/${conector}/execucoes/${bl.execucao_id}/interromper`, { method: 'POST', body: JSON.stringify({ motivo: motivo.trim() }) })
      setAbrir(false); setMotivo(''); onFim()
    } catch (e) {
      setErro(e instanceof ApiErro ? e.message : 'Não foi possível interromper.')
    } finally {
      setEnviando(false)
    }
  }
  return (
    <div className="aviso-ambar bloqueio" role="status">
      <p><strong>{tipo} de {nome} em andamento</strong> · iniciada em {dataHoraBR(bl.iniciado_em)}
        {' '}· última atividade {dataHoraBR(bl.atualizado_em ?? bl.iniciado_em)}</p>
      {bl.parada && <p>Sem atividade há {Math.round(bl.parada_min)} min (limite {bl.alerta_min} min){bl.viva ? '' : ' e sem processo ativo'}.
        {' '}A próxima carga marca esta execução como interrompida, sem apagar a bronze.</p>}
      {ehAdmin && (
        <>
          <button className="btn secundario" disabled={!bl.parada} aria-describedby={`int-${bl.execucao_id}`} onClick={() => setAbrir(true)}>Interromper</button>
          {!bl.parada && <span className="sutil" id={`int-${bl.execucao_id}`}> Disponível depois de {bl.alerta_min} min sem atividade</span>}
        </>
      )}
      {abrir && (
        <div role="dialog" aria-modal="false" aria-labelledby={`t-int-${bl.execucao_id}`} className="cartao confirmar">
          <p id={`t-int-${bl.execucao_id}`}>Interromper a {tipo.toLowerCase()} de {nome}? A execução fica como interrompida
            {' '}e nada é apagado. O motivo fica na auditoria.</p>
          <label htmlFor={`motivo-${bl.execucao_id}`}>Motivo</label>
          <input id={`motivo-${bl.execucao_id}`} value={motivo} onChange={(e) => setMotivo(e.target.value)} maxLength={300} />
          {erro && <p role="alert" className="aviso erro">{erro}</p>}
          <button className="btn secundario" onClick={() => setAbrir(false)}>Cancelar</button>{' '}
          <button className="btn" disabled={enviando || motivo.trim().length < 5} onClick={interromper}>Confirmar interrupção</button>
        </div>
      )}
    </div>
  )
}
