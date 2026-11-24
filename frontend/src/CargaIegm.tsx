import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { api, ApiErro, dataHoraBR } from './api'

type Estado = { anos: number[]; ultima_execucao: { status: string; mensagem: string | null; finalizado_em: string | null } | null }
const estadoOk = (r: unknown): r is Estado => !!r && Array.isArray((r as Estado).anos)

/** IEG-M (TCE-SP) na tela Conectores: anos carregados e importação anual do arquivo (só admin). */
export default function CargaIegm({ ehAdmin }: { ehAdmin: boolean }) {
  const [d, setD] = useState<Estado | null>(null)
  const [erro, setErro] = useState(false)
  const [aviso, setAviso] = useState<{ texto: string; erro?: boolean } | null>(null)
  const [enviando, setEnviando] = useState(false)
  const arquivo = useRef<HTMLInputElement>(null)
  const carregar = useCallback(() => {
    api<unknown>('/iegm/estado').then((r) => { if (estadoOk(r)) { setD(r); setErro(false) } else setErro(true) }).catch(() => setErro(true))
  }, [])
  useEffect(carregar, [carregar])

  const enviar = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setEnviando(true); setAviso(null)
    try {
      const r = await api<{ mensagem: string }>('/iegm/arquivo', { method: 'POST', body: f, headers: { 'Content-Type': 'text/csv' } })
      setAviso({ texto: r.mensagem }); carregar()
    } catch (er) {
      setAviso({ texto: er instanceof ApiErro ? er.message : 'Não foi possível enviar o arquivo.', erro: true })
    } finally {
      setEnviando(false)
      if (arquivo.current) arquivo.current.value = ''
    }
  }

  return (
    <section className="cartao audesp" id="fonte-iegm" aria-labelledby="t-iegm">
      <div className="ph-audesp">
        <div>
          <h2 id="t-iegm">IEG-M (TCE-SP)</h2>
          <p className="sutil">Índice de Efetividade da Gestão Municipal. O TCE publica uma vez por ano; a importação é por arquivo (CSV).</p>
        </div>
        {d?.ultima_execucao && (
          <span className={`selo ${d.ultima_execucao.status === 'sucesso' ? 'sucesso' : 'falha'}`}><i aria-hidden="true" />
            {d.ultima_execucao.status === 'sucesso' ? 'Concluída' : 'Falhou'} · {dataHoraBR(d.ultima_execucao.finalizado_em)}</span>
        )}
      </div>
      {erro ? <p className="sutil">Não foi possível carregar o estado do IEG-M.{' '}<button className="link-btn" type="button" onClick={carregar}>Tentar de novo</button></p>
        : !d ? <p className="sutil">Carregando…</p>
        : <p>{d.anos.length ? `Anos base carregados: ${d.anos[0]} a ${d.anos[d.anos.length - 1]} (${d.anos.length} anos).` : 'Nenhum ano carregado ainda.'}
            {d.ultima_execucao?.mensagem && d.ultima_execucao.status !== 'sucesso' ? <span className="sutil"> {d.ultima_execucao.mensagem}</span> : null}</p>}
      {ehAdmin && (
        <div className="acoes">
          <input id="iegm-arquivo" ref={arquivo} type="file" accept=".csv" className="sr-only" onChange={enviar} disabled={enviando} />
          <label className="btn secundario" htmlFor="iegm-arquivo" aria-disabled={enviando}>{enviando ? 'Enviando…' : 'Enviar arquivo do IEG-M (.csv)'}</label>
          <span className="sutil">Colunas: ano_base;ano_apuracao;indice;abrangencia;faixa;municipios. Um arquivo novo substitui os anos que ele traz.</span>
        </div>
      )}
      <div aria-live="polite">{aviso && <p className={aviso.erro ? 'aviso erro' : 'aviso'}>{aviso.texto}</p>}</div>
    </section>
  )
}
