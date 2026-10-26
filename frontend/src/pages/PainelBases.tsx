import { useMemo, useState } from 'react'
import { api, ApiErro, dataHoraBR } from '../api'
import { Erro, Esqueleto } from '../componentes'
import CargaCompleta from '../CargaCompleta'

export type Contagens = { bronze: number; prata: number; ouro: number; pendentes: number }
export type ErroCarga = { classe: string; numero: number | null; tabela: string | null; objeto: string | null; local: string | null; motivo: string }
export type UltimaCarga = { id: number; tipo: string; status: string; iniciado_em: string; finalizado_em: string | null; lidos: number; mensagem: string | null; erro?: ErroCarga | null }
export type Base = {
  id: number; nome: string; descricao: string | null; grupo: string; triagem: string; status: string; prioridade: number | null
  tem_conector: boolean; conector_id?: string | null; pode_carregar: boolean; motivo_bloqueio: string | null; tem_amostra: boolean; com_erro: boolean; origem_vazia?: boolean
  completa_disponivel?: boolean; completa_liberada?: boolean; completa_motivo?: string | null
  contagens: Contagens; ultima_carga: UltimaCarga | null
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
export const milhar = (n: number) => new Intl.NumberFormat('pt-BR').format(n)
export function perda(de: number, para: number): number | null {
  if (de <= 0 || para >= de) return null
  return Math.round(((de - para) / de) * 100)
}

export const CARGA_EM_PREPARACAO = 'Carga real em preparação'
export const MSG_ORIGEM_VAZIA = 'Nenhuma linha lida da origem. Verifique a conexão ou o banco configurado.'

export default function PainelBases({ bases, erro, onRecarregar, ehAdmin, cargaHabilitada = false }: {
  bases: Base[] | null; erro: boolean; onRecarregar: () => void; ehAdmin: boolean; cargaHabilitada?: boolean
}) {
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
  const tot = useMemo(() => visiveis.reduce((a, b) => ({
    bronze: a.bronze + b.contagens.bronze, prata: a.prata + b.contagens.prata, ouro: a.ouro + b.contagens.ouro,
  }), { bronze: 0, prata: 0, ouro: 0 }), [visiveis])
  const ultima = useMemo(() => visiveis.map((b) => b.ultima_carga?.iniciado_em).filter(Boolean).sort().pop() ?? null, [visiveis])

  const executar = async (b: Base, modo: Modo) => {
    setConfirmar(null); setRodando(b.id); setAviso(null)
    try {
      // Puxar amostra = carga real por conector (política de tabelas, admin), liberada também em produção.
      // Carga completa e reprocessar seguem pela rota sintética, só em dev/test.
      const r = modo === 'amostra'
        ? await api<{ status: string; lidos: number; mensagem: string | null }>(`/conectores/${b.conector_id ?? b.id}/amostra`, { method: 'POST' })
        : await api<{ status: string; lidos: number; mensagem: string | null }>(`/conectores/${b.id}/executar`, { method: 'POST', body: JSON.stringify({ modo }) })
      if (r.status === 'falha') setAviso({ texto: `Falha na carga de ${b.nome}: ${r.mensagem ?? 'erro'}.`, erro: true })
      else setAviso({ texto: `${ROTULO_MODO[modo]} de ${b.nome} concluída (${r.status === 'sem_novidades' ? 'sem novidades' : 'sucesso'}). Contagens atualizadas abaixo.`, erro: false })
      onRecarregar()
    } catch (e) {
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
              <li className="cartao"><span className="cartao-rotulo">Bronze</span> <span className="cartao-valor">{milhar(tot.bronze)}</span></li>
              <li className="cartao"><span className="cartao-rotulo">Prata</span> <span className="cartao-valor">{milhar(tot.prata)}</span></li>
              <li className="cartao"><span className="cartao-rotulo">Ouro (cidadãos únicos)</span> <span className="cartao-valor">{milhar(tot.ouro)}</span></li>
            </ul>
            <p>Última carga: {ultima ? dataHoraBR(ultima) : 'Nunca carregado'}</p>
            <div className="filtros" role="group" aria-label="Filtrar bases">
              {FILTROS.map((f) => (
                <button key={f.id} className={`chip${filtro === f.id ? ' ativo' : ''}`} aria-pressed={filtro === f.id} onClick={() => setFiltro(f.id)}>{f.rotulo}</button>
              ))}
              <label htmlFor="busca-base" className="sr-only">Buscar base pelo nome</label>
              <input id="busca-base" type="search" placeholder="Buscar base" value={busca} onChange={(e) => setBusca(e.target.value)} />
            </div>
            <div aria-live="polite">{aviso && <p role={aviso.erro ? 'alert' : undefined} className={`aviso${aviso.erro ? ' erro' : ''}`}>{aviso.texto}</p>}</div>
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
                {visiveis.map((b) => <CartaoBase key={b.id} b={b} ehAdmin={ehAdmin} habilitada={cargaHabilitada} rodando={rodando} onAcao={pedir} onFim={onRecarregar} />)}
              </ul>
            )}
          </>
        )}
    </section>
  )
}

function CartaoBase({ b, ehAdmin, habilitada, rodando, onAcao, onFim }: { b: Base; ehAdmin: boolean; habilitada: boolean; rodando: number | null; onAcao: (b: Base, m: Modo) => void; onFim: () => void }) {
  const st = STATUS[b.status] ?? { rotulo: b.status, classe: 'triado' }
  const c = b.contagens
  const n = (v: number) => (b.tem_conector ? milhar(v) : '—')
  const pPrata = b.tem_conector ? perda(c.bronze, c.prata) : null
  const rodandoAlgo = rodando !== null
  const ocupado = rodandoAlgo || !habilitada
  const u = b.ultima_carga
  return (
    <li className="cartao base" aria-labelledby={`b-${b.id}`}>
      <h3 id={`b-${b.id}`}>{b.nome} <span className="sutil">· {GRUPO[b.grupo] ?? b.grupo}{b.prioridade ? ` · fila ${b.prioridade}` : ''}</span></h3>
      <p>
        <span className={`selo ${st.classe}`}><i aria-hidden="true" />{st.rotulo}</span>
        {b.com_erro && <> <span className="selo falha"><i aria-hidden="true" />Última carga falhou</span></>}
      </p>
      {b.descricao && <p className="sutil">{b.descricao}</p>}
      <p>Última carga: {u ? `${dataHoraBR(u.iniciado_em)} · ${TIPO[u.tipo] ?? u.tipo} · ${u.status === 'origem_vazia' ? 'origem vazia' : u.status}` : 'Nunca carregado'}</p>
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
      {pPrata !== null && (
        <p className="perda" title="Diferença bronze → prata: descartes, duplicatas e pendências de correspondência">
          −{pPrata}% na prata
          <span className="sr-only"> (descartes, duplicatas e pendências de correspondência)</span>
        </p>
      )}
      <p>Pendências: {b.tem_conector ? `${milhar(c.pendentes)} sem correspondência` : '—'}</p>
      {!b.tem_conector && <p className="sutil">{b.status === 'excluido' ? 'Base descartada na triagem' : 'Conector ainda não implementado'}</p>}
      {ehAdmin && b.tem_conector && (
        <div className="acoes">
          <button className="btn" disabled={rodandoAlgo || !b.pode_carregar} aria-label={`Puxar amostra de ${b.nome}`} onClick={() => onAcao(b, 'amostra')}>
            {rodando === b.id ? 'Carregando…' : 'Puxar amostra'}
          </button>
          {!b.pode_carregar && <span className="sutil"> {b.motivo_bloqueio}</span>}
          {b.completa_disponivel && b.conector_id && (
            <CargaCompleta conector={b.conector_id} liberada={b.completa_liberada === true} motivo={b.completa_motivo ?? null} onFim={onFim} />
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
