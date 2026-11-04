import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { api, ApiErro, dataHoraBR } from './api'

type Estado = {
  total: number; mais_usadas: { codigo: number; descricao: string; inscricoes: number }[]; sem_nome: { codigo: number; inscricoes: number }[]
  ultima_execucao: { status: string; mensagem: string | null; finalizado_em: string | null } | null
}
const estadoOk = (r: unknown): r is Estado => !!r && typeof (r as Estado).total === 'number' && Array.isArray((r as Estado).mais_usadas)
const num = new Intl.NumberFormat('pt-BR')

/** Dicionário da natureza da dívida na tela Conectores: importação do CSV (só admin) e conferência do de-para. */
export default function CargaNatureza({ ehAdmin }: { ehAdmin: boolean }) {
  const [d, setD] = useState<Estado | null>(null)
  const [erro, setErro] = useState(false)
  const [aviso, setAviso] = useState<{ texto: string; erro?: boolean } | null>(null)
  const [enviando, setEnviando] = useState(false)
  const arquivo = useRef<HTMLInputElement>(null)
  const carregar = useCallback(() => {
    api<unknown>('/divida/natureza/estado').then((r) => { if (estadoOk(r)) { setD(r); setErro(false) } else setErro(true) }).catch(() => setErro(true))
  }, [])
  useEffect(carregar, [carregar])

  const enviar = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setEnviando(true); setAviso(null)
    try {
      const r = await api<{ mensagem: string }>('/divida/natureza/arquivo', { method: 'POST', body: f, headers: { 'Content-Type': 'text/csv' } })
      setAviso({ texto: r.mensagem }); carregar()
    } catch (er) {
      setAviso({ texto: er instanceof ApiErro ? er.message : 'Não foi possível enviar o arquivo.', erro: true })
    } finally {
      setEnviando(false)
      if (arquivo.current) arquivo.current.value = ''
    }
  }

  return (
    <section className="cartao audesp" id="fonte-natureza" aria-labelledby="t-natureza">
      <div className="ph-audesp">
        <div>
          <h2 id="t-natureza">Natureza da dívida ativa</h2>
          <p className="sutil">Nomes dos códigos de natureza. O arquivo vem do DBA (Geral.dbo.NATUREZA), porque o SIGPMF não lê a base Geral.</p>
        </div>
        {d?.ultima_execucao && (
          <span className={`selo ${d.ultima_execucao.status === 'sucesso' ? 'sucesso' : 'falha'}`}><i aria-hidden="true" />
            {d.ultima_execucao.status === 'sucesso' ? 'Concluída' : 'Falhou'} · {dataHoraBR(d.ultima_execucao.finalizado_em)}</span>
        )}
      </div>
      {erro ? <p className="sutil">Não foi possível carregar o estado das naturezas.{' '}<button className="link-btn" type="button" onClick={carregar}>Tentar de novo</button></p>
        : !d ? <p className="sutil">Carregando…</p>
        : (
          <>
            <p>{d.total ? `${num.format(d.total)} naturezas carregadas.` : 'Nenhuma natureza carregada: os painéis mostram só o código.'}</p>
            {d.mais_usadas.length > 0 && (
              <details>
                <summary>Conferir as mais usadas na dívida</summary>
                <ul className="natureza-lista">{d.mais_usadas.map((n) => <li key={n.codigo}><span className="num">{n.codigo}</span> {n.descricao} <span className="sutil">({num.format(n.inscricoes)} inscrições)</span></li>)}</ul>
              </details>
            )}
            {d.total > 0 && d.sem_nome.length > 0 && <p className="sutil">Códigos em uso sem nome no arquivo: {d.sem_nome.map((n) => n.codigo).join(', ')}.</p>}
          </>
        )}
      {ehAdmin && (
        <div className="acoes">
          <input id="natureza-arquivo" ref={arquivo} type="file" accept=".csv,.txt" className="sr-only" onChange={enviar} disabled={enviando} />
          <label className="btn secundario" htmlFor="natureza-arquivo" aria-disabled={enviando}>{enviando ? 'Enviando…' : 'Enviar arquivo das naturezas (.csv)'}</label>
          <span className="sutil">Colunas: COD_NATU;DESCRICAO. Um arquivo novo substitui o anterior. Depois, recalcule os cubos.</span>
        </div>
      )}
      <div aria-live="polite">{aviso && <p className={aviso.erro ? 'aviso erro' : 'aviso'}>{aviso.texto}</p>}</div>
    </section>
  )
}
