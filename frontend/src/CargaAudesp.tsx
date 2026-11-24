import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { api, ApiErro, dataHoraBR } from './api'
import { classeStatus, STATUS_EXEC } from './AgendaCargas'
import { Esqueleto } from './componentes'

type Arquivo = { tipo: string; ano: number; origem: string; linhas: number; ultimo_mes: number | null; publicado_tce: string | null; carregado_em: string }
type Exec = { id: number; status: string; mensagem: string | null; arquivo_atual: string | null; lidos: number; iniciado_em: string; finalizado_em: string | null }
type Estado = { anos: number[]; municipio: string; arquivos: Arquivo[]; em_andamento: Exec | null; ultima_execucao: Exec | null }

const TIPOS: [string, string][] = [['receitas', 'Receitas'], ['despesas', 'Despesas'], ['restos_a_pagar', 'Restos a pagar']]
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
const TEXTO_STATUS: Record<string, string> = { ...STATUS_EXEC, interrompida: 'Interrompida' }
const n = (v: number) => new Intl.NumberFormat('pt-BR').format(v)
/** Resposta fora do formato (backend antigo, proxy, erro) vira estado de erro do card, nunca quebra a página Conectores. */
const estadoOk = (r: unknown): r is Estado =>
  !!r && Array.isArray((r as Estado).anos) && Array.isArray((r as Estado).arquivos)

/** "Wed, 23 Sep 2026 12:02:03 GMT" (Last-Modified do TCE) -> "23/09/2026". */
function dataTce(v: string | null): string {
  const d = v ? new Date(v) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' }) : '—'
}

/** AU1: AUDESP (TCE-SP) na tela Conectores: o que está carregado, "Carregar agora" e envio manual do arquivo. */
export default function CargaAudesp({ ehAdmin }: { ehAdmin: boolean }) {
  const [d, setD] = useState<Estado | null>(null)
  const [erro, setErro] = useState(false)
  const [aviso, setAviso] = useState<{ texto: string; erro?: boolean } | null>(null)
  const [enviando, setEnviando] = useState(false)
  const [forcar, setForcar] = useState(false)
  const arquivo = useRef<HTMLInputElement>(null)

  const carregar = useCallback(() => {
    api<unknown>('/audesp/arquivos').then((r) => {
      if (estadoOk(r)) { setD(r); setErro(false) } else setErro(true)
    }).catch(() => setErro(true))
  }, [])
  useEffect(carregar, [carregar])
  const rodando = d?.em_andamento ?? null
  useEffect(() => {  // acompanha a carga em background
    if (!rodando) return
    const t = window.setTimeout(carregar, 3000)
    return () => window.clearTimeout(t)
  }, [rodando, d, carregar])

  const carregarAgora = async () => {
    setAviso(null)
    try {
      const r = await api<unknown>('/audesp/carregar', { method: 'POST', body: JSON.stringify({ forcar }) })
      if (estadoOk(r)) setD(r); else carregar()
      setAviso({ texto: 'Carga iniciada: conferindo os arquivos no portal do TCE.' })
    } catch (e) {
      setAviso({ texto: e instanceof ApiErro ? e.message : 'Não foi possível iniciar a carga.', erro: true })
    }
  }
  const enviar = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setEnviando(true); setAviso(null)
    try {
      const r = await api<{ mensagem: string }>('/audesp/arquivo', { method: 'POST', body: f, headers: { 'Content-Type': f.type || 'application/octet-stream' } })
      setAviso({ texto: r.mensagem })
      carregar()
    } catch (er) {
      setAviso({ texto: er instanceof ApiErro ? er.message : 'Não foi possível enviar o arquivo.', erro: true })
    } finally {
      setEnviando(false)
      if (arquivo.current) arquivo.current.value = ''
    }
  }

  if (erro) {
    return (
      <section className="cartao audesp" aria-labelledby="t-audesp">
        <h2 id="t-audesp">AUDESP (TCE-SP)</h2>
        <p className="sutil">Não foi possível carregar o estado do AUDESP.{' '}
          <button className="link-btn" type="button" onClick={carregar}>Tentar de novo</button></p>
      </section>
    )
  }
  if (!d) return <section className="cartao audesp" aria-labelledby="t-audesp"><h2 id="t-audesp">AUDESP (TCE-SP)</h2><Esqueleto linhas={3} /></section>

  const porChave = new Map(d.arquivos.map((a) => [`${a.ano}-${a.tipo}`, a]))
  const anos = [...new Set([...d.anos, ...d.arquivos.map((a) => a.ano)])].sort((a, b) => b - a)
  const ult = d.ultima_execucao
  return (
    <section className="cartao audesp" id="fonte-audesp" aria-labelledby="t-audesp">
      <div className="ph-audesp">
        <div>
          <h2 id="t-audesp">AUDESP (TCE-SP)</h2>
          <p className="sutil">Receita, despesa e restos a pagar que a prefeitura envia ao Tribunal de Contas. O TCE publica uma vez
            por mês, com cerca de dois meses de atraso. Anos configurados: {d.anos.join(', ')}.</p>
        </div>
        {rodando
          ? <span className="selo em_andamento"><i aria-hidden="true" />Carregando{rodando.arquivo_atual ? ` · ${rodando.arquivo_atual}` : ''}</span>
          : ult && <span className={`selo ${ult.status === 'parcial' ? 'atencao' : classeStatus(ult.status === 'sem_novidades' ? 'sucesso' : ult.status)}`}>
              <i aria-hidden="true" />{TEXTO_STATUS[ult.status] ?? ult.status} · {dataHoraBR(ult.finalizado_em ?? ult.iniciado_em)}</span>}
      </div>
      {!rodando && ult?.mensagem && <p className="sutil">{ult.mensagem}</p>}

      {ehAdmin && (
        <div className="acoes">
          <button className="btn" type="button" onClick={carregarAgora} disabled={!!rodando || enviando}>Carregar agora</button>
          <label className="agenda-chave">
            <input type="checkbox" checked={forcar} onChange={(e) => setForcar(e.target.checked)} disabled={!!rodando} />
            <span>Baixar de novo mesmo sem mudança no TCE</span>
          </label>
          <input id="audesp-arquivo" ref={arquivo} type="file" accept=".zip,.csv" className="sr-only" onChange={enviar}
            disabled={!!rodando || enviando} />
          <label className="btn secundario" htmlFor="audesp-arquivo" aria-disabled={!!rodando || enviando}>
            {enviando ? 'Enviando…' : 'Enviar arquivo do TCE (.zip ou .csv)'}
          </label>
        </div>
      )}
      <div aria-live="polite">{aviso && <p className={aviso.erro ? 'aviso erro' : 'aviso'}>{aviso.texto}</p>}</div>

      <div className="agenda-rolagem">
        <table className="tabela">
          <caption className="sr-only">Arquivos do AUDESP carregados</caption>
          <thead><tr><th scope="col">Ano</th><th scope="col">Arquivo</th><th scope="col">Linhas</th><th scope="col">Dados até</th>
            <th scope="col">Publicado no TCE</th><th scope="col">Carregado em</th></tr></thead>
          <tbody>
            {anos.flatMap((ano) => TIPOS.map(([tipo, nome]) => {
              const a = porChave.get(`${ano}-${tipo}`)
              return (
                <tr key={`${ano}-${tipo}`}>
                  <td data-rotulo="Ano" className="num">{ano}</td>
                  <th scope="row" data-rotulo="Arquivo">{nome}</th>
                  {a
                    ? <>
                        <td data-rotulo="Linhas" className="num">{n(a.linhas)}</td>
                        <td data-rotulo="Dados até">{a.ultimo_mes ? (a.ultimo_mes === 13 ? `encerramento/${ano}` : `${MESES[a.ultimo_mes - 1]}/${ano}`) : '—'}</td>
                        <td data-rotulo="Publicado no TCE">{a.origem === 'upload' ? 'enviado manualmente' : dataTce(a.publicado_tce)}</td>
                        <td data-rotulo="Carregado em">{dataHoraBR(a.carregado_em)}</td>
                      </>
                    : <td colSpan={4} className="sutil" data-rotulo="Situação">Ainda não carregado</td>}
                </tr>
              )
            }))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
