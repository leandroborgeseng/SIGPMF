import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Erro, Esqueleto } from '../componentes'
import {
  ATUALIZA_MS, STATUS, ariaFase, dataHoraBRT, duracao, estimativa, horaBRT, num, segundosDesde,
  semResultadosAinda, sinalDeVida, textoErro, textoFases, textoProgresso, type Carga, type RespAndamento, type Status,
} from '../cargas'
import '../cargas.css'

function Selo({ status }: { status: Status }) {
  const s = STATUS[status] ?? STATUS.na_fila
  return <span className={`selo ${s.classe}`}><span aria-hidden="true">{s.icone}</span> {s.texto}</span>
}

function Fases({ etapa }: { etapa: string | null }) {
  return <p className="cg-fases" role="img" aria-label={ariaFase(etapa)}>{textoFases(etapa)}</p>
}

function CardCarga({ c, agora, limite }: { c: Carga; agora: number; limite: number }) {
  const a = c.atual
  const rodando = a.status === 'rodando'
  const prog = textoProgresso(a)
  const vida = sinalDeVida(a, agora, limite)
  const est = estimativa(a, agora)
  return (
    <article className="cartao cg-card" aria-label={c.fonte}>
      <header className="cg-topo">
        <h2>{c.fonte}</h2>
        <div className="cg-selos">
          <Selo status={a.status} />
          <span className="selo neutro">{a.tipo}</span>
          {c.agenda_ativa === false && <span className="selo atencao">Agenda desligada</span>}
          {vida?.travada && <span className="selo atencao" role="status"><span aria-hidden="true">⚠</span> Possível travamento</span>}
        </div>
      </header>
      <Fases etapa={a.etapa} />
      {rodando && (
        <div className="cg-agora">
          <p className="cg-subetapa"><strong>{a.subetapa ?? 'Preparando a etapa'}</strong></p>
          {prog.pct != null
            ? <div className="barra" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={prog.pct} aria-label="Progresso da subetapa"><span style={{ width: `${prog.pct}%` }} /></div>
            : null}
          <p>{prog.texto}</p>
          {semResultadosAinda(a, agora, limite) && <p className="cg-sem-resultado">Rodando, ainda sem resultados</p>}
          {a.gravados === 0 && <p className="cg-nota">0 linhas gravadas ainda.</p>}
          {est && <p className="cg-nota">{est}</p>}
          {vida && <p className={vida.travada ? 'aviso-ambar' : 'cg-nota'}>{vida.texto}</p>}
        </div>
      )}
      {a.status === 'erro' && (
        <p className="cg-erro">
          {textoErro(a.motivo)}{' '}
          <Link to={`/conectores#fonte-${c.sistema.toLowerCase()}`}>Abrir {c.fonte} em Conectores</Link>
        </p>
      )}
      {a.status === 'parcial' && (
        <p className="aviso-ambar">{a.motivo ?? 'Carga parcial.'}{a.proxima_acao ? ` Próxima ação: ${a.proxima_acao}.` : ''}</p>
      )}
      <dl className="cg-fatos">
        <div><dt>Lidas nesta execução</dt><dd>{num(a.lidos)}</dd></div>
        <div><dt>Gravadas nesta execução</dt><dd>{num(a.gravados)}</dd></div>
        <div><dt>Quarentena</dt><dd>{num(a.quarentena)}</dd></div>
        <div><dt>Início</dt><dd>{dataHoraBRT(a.iniciado_em)}</dd></div>
        <div><dt>Última completa</dt><dd>{dataHoraBRT(c.ultima_completa_em)}</dd></div>
        <div><dt>Última incremental</dt><dd>{dataHoraBRT(c.ultima_incremental_em)}</dd></div>
        <div><dt>Próxima na agenda</dt><dd>{c.agenda_horario ? `${c.agenda_horario} BRT` : 'Sem agendamento'}</dd></div>
      </dl>
      <details>
        <summary>Últimas {c.log.length} subetapas</summary>
        {c.log.length === 0 ? <p className="cg-nota">Nenhuma subetapa registrada.</p> : (
          <ol className="cg-log">
            {c.log.map((s, i) => (
              <li key={i}><span className="mono">{horaBRT(s.iniciada_em)}</span> · {s.subetapa ?? s.etapa} · {duracao(s.duracao_s ?? segundosDesde(s.iniciada_em, agora))}{s.contador != null ? ` · ${num(s.contador)}${s.total ? ` de ${num(s.total)}` : ''}` : ''}</li>
            ))}
          </ol>
        )}
      </details>
      <details>
        <summary>Histórico (últimas {c.historico.length} execuções)</summary>
        <div className="cg-tabela-wrap">
          <table className="cg-tabela">
            <thead><tr><th scope="col">Data</th><th scope="col">Tipo</th><th scope="col">Duração</th><th scope="col">Linhas</th><th scope="col">Status</th></tr></thead>
            <tbody>
              {c.historico.map((h) => (
                <tr key={h.id}><td>{dataHoraBRT(h.iniciado_em)}</td><td>{h.tipo}</td><td>{duracao(h.duracao_s)}</td><td>{num(h.gravados)}</td><td><Selo status={h.status} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </article>
  )
}

export default function Cargas() {
  const [d, setD] = useState<RespAndamento | null>(null)
  const [erro, setErro] = useState(false)
  const [recebido, setRecebido] = useState(0)
  const [agora, setAgora] = useState(() => Date.now())
  const [filtro, setFiltro] = useState<'' | Status>('')
  const vivo = useRef(true)

  const carregar = useCallback(() => {
    api<RespAndamento>('/cargas/andamento')
      .then((r) => { if (vivo.current) { setD(r); setErro(false); setRecebido(Date.now()) } })
      .catch(() => { if (vivo.current) setErro(true) })
  }, [])
  useEffect(() => {
    vivo.current = true
    carregar()
    const t = setInterval(carregar, ATUALIZA_MS)  // CT9/UXC4: a cada 10 s
    const r = setInterval(() => setAgora(Date.now()), 1000)
    return () => { vivo.current = false; clearInterval(t); clearInterval(r) }
  }, [carregar])

  if (erro && !d) return <Erro texto="Não foi possível carregar as cargas. Tente de novo." onRetry={carregar} />
  if (!d) return <Esqueleto linhas={3} texto="Carregando as cargas…" />
  const cargas = d.cargas.filter((c) => !filtro || c.atual.status === filtro)
  const desde = Math.max(0, Math.round((agora - recebido) / 1000))
  return (
    <section className="cg" lang="pt-BR">
      <header className="cg-cabecalho">
        <h1>Conectores e cargas</h1>
        <p className="cg-nota">Atualizado há {desde} s · atualiza sozinho a cada 10 s</p>
        <label>Status{' '}
          <select value={filtro} onChange={(e) => setFiltro(e.target.value as '' | Status)}>
            <option value="">Todos</option>
            {Object.entries(STATUS).map(([k, s]) => <option key={k} value={k}>{s.texto}</option>)}
          </select>
        </label>
      </header>
      <div className="sr-only" aria-live="polite">{d.cargas.map((c) => `${c.fonte}: ${STATUS[c.atual.status]?.texto ?? ''}`).join('; ')}</div>
      {d.cargas.length === 0
        ? <p>Nenhuma carga executada ainda. Rode a amostra para começar.</p>
        : <div className="cg-lista">{cargas.map((c) => <CardCarga key={c.sistema} c={c} agora={agora} limite={d.sem_sinal_limite_s} />)}</div>}
    </section>
  )
}
