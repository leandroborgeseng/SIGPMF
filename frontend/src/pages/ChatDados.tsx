import { useState } from 'react'
import { ApiErro, api, dataHoraBR } from '../api'
import { Erro, Esqueleto } from '../componentes'

type Numeros = Record<string, number>
type Resp = {
  resposta: string
  estado: 'ok' | 'sem_carga' | 'negado'
  consulta: string | null
  fonte: string | null
  filtros: Record<string, string>
  carga_em: string | null
  status_carga: string | null
  numeros: Numeros | null
}

const ROTULO: Record<string, string> = {
  lidos: 'Lidos', novos: 'Novos', atualizados: 'Atualizados', ignorados: 'Ignorados',
  empresas: 'Empresas', bronze: 'Bronze', prata: 'Prata', ouro: 'Ouro', pendentes: 'Pendentes',
}
const MSG_LLM = 'O serviço de linguagem não está disponível agora. Tente de novo em instantes.'

function filtrosTexto(f: Record<string, string> | null | undefined) {
  const itens = Object.entries(f ?? {})
  return itens.length ? itens.map(([k, v]) => `${k}: ${v}`).join(' · ') : '—'
}

export default function ChatDados() {
  const [pergunta, setPergunta] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [falhaLlm, setFalhaLlm] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const [resp, setResp] = useState<Resp | null>(null)

  async function consultar(texto: string) {
    const p = texto.trim()
    if (!p || enviando) return
    setEnviando(true)
    setFalhaLlm(false)
    setErro(null)
    setResp(null)
    try {
      setResp(await api<Resp>('/chat', { method: 'POST', body: JSON.stringify({ pergunta: p }) }))
    } catch (ex) {
      if (ex instanceof ApiErro && ex.status === 503) setFalhaLlm(true)
      else setErro(ex instanceof ApiErro ? ex.message : 'Erro inesperado.')
    } finally {
      setEnviando(false)
    }
  }

  return (
    <section aria-labelledby="t-chat">
      <header className="page-head">
        <p className="kicker">Consulta</p>
        <h1 id="t-chat">Chat de dados</h1>
        <p className="sutil">Perguntas sobre cargas e contagens agregadas. A resposta traz a fonte, os filtros e a data da carga.</p>
      </header>
      <form className="form" onSubmit={(e) => { e.preventDefault(); void consultar(pergunta) }}>
        <label htmlFor="pergunta-chat">Pergunta</label>
        <textarea id="pergunta-chat" rows={3} maxLength={500} value={pergunta}
          placeholder="Ex.: qual foi a última carga da SIL?"
          onChange={(e) => setPergunta(e.target.value)} />
        <div className="acoes">
          <button className="btn" type="submit" disabled={enviando || !pergunta.trim()}>Perguntar</button>
        </div>
      </form>
      <div aria-live="polite">
        {enviando && <Esqueleto linhas={3} texto="Consultando os dados…" />}
        {falhaLlm && <Erro texto={MSG_LLM} onRetry={() => void consultar(pergunta)} />}
        {erro && <div role="alert" className="aviso erro"><p>{erro}</p></div>}
        {resp && (
          <>
            {resp.estado === 'sem_carga' && <div className="vazio"><p>Não há carga concluída desta fonte.</p></div>}
            {resp.estado === 'negado'
              ? <div role="alert" className="aviso erro"><p>{resp.resposta}</p></div>
              : <p className="ultima">{resp.resposta}</p>}
            <dl className="camadas">
              <div><dt>Fonte</dt><dd>{resp.fonte ?? '—'}</dd></div>
              <div><dt>Filtros</dt><dd>{filtrosTexto(resp.filtros)}</dd></div>
              <div><dt>Data da carga</dt><dd>{dataHoraBR(resp.carga_em)}</dd></div>
            </dl>
            {resp.numeros && (
              <p className="sutil">
                {Object.entries(resp.numeros).map(([k, v]) => `${ROTULO[k] ?? k} ${v.toLocaleString('pt-BR')}`).join(' · ')}
                {resp.status_carga ? ` · ${resp.status_carga}` : ''}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  )
}
