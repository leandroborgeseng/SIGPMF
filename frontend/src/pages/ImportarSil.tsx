import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiErro } from '../api'
import { useAuth } from '../auth'

type Situacao = 'em_andamento' | 'concluido' | 'erro'
type Estado = { id: number; status: string; situacao: Situacao; total: number; mensagem: string | null }

const TEXTO: Record<Situacao, string> = {
  em_andamento: 'Em andamento',
  concluido: 'Concluído',
  erro: 'Erro',
}
const n = (v: number) => new Intl.NumberFormat('pt-BR').format(v)

/** Cargas > Importar imóveis SIL. O CSV sobe em multipart e a carga roda no servidor; a tela consulta o status. */
export default function ImportarSil() {
  const { sessao } = useAuth()
  if (sessao?.perfil !== 'admin') {
    return (
      <section className="cartao" aria-labelledby="t-sil">
        <h1 id="t-sil">Importar imóveis SIL</h1>
        <p role="alert">Você não tem permissão para esta tela.</p>
      </section>
    )
  }
  return <Tela />
}

function Tela() {
  const arquivo = useRef<HTMLInputElement>(null)
  const [enviando, setEnviando] = useState(false)
  const [id, setId] = useState<number | null>(null)
  const [estado, setEstado] = useState<Estado | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const situacao = estado?.situacao
  const ocupado = enviando || situacao === 'em_andamento'

  useEffect(() => {
    if (id == null || situacao !== 'em_andamento') return
    let vivo = true
    const ler = () => {
      api<Estado>(`/cargas/sil/imoveis/${id}`).then((r) => {
        if (!vivo) return
        setEstado(r)
        setErro(null)
      }).catch(() => {
        if (vivo) setErro('Não foi possível consultar o status da importação.')
      })
    }
    ler()
    const t = window.setInterval(ler, 2000)
    return () => { vivo = false; window.clearInterval(t) }
  }, [id, situacao])

  const enviar = async (e: FormEvent) => {
    e.preventDefault()
    const f = arquivo.current?.files?.[0]
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.csv')) {
      setErro('Envie um arquivo .csv.')
      return
    }
    setEnviando(true)
    setErro(null)
    const body = new FormData()
    body.append('arquivo', f)
    try {
      const r = await api<{ id: number }>(`/cargas/sil/imoveis`, { method: 'POST', body })
      setId(r.id)
      setEstado({ id: r.id, status: 'em_andamento', situacao: 'em_andamento', total: 0, mensagem: null })
    } catch (err) {
      setEstado(null)
      setId(null)
      setErro(err instanceof ApiErro ? err.message : 'Não foi possível enviar o arquivo.')
    } finally {
      setEnviando(false)
      if (arquivo.current) arquivo.current.value = ''
    }
  }

  return (
    <section className="cartao" aria-labelledby="t-sil">
      <header className="page-head">
        <p className="kicker">Cargas</p>
        <h1 id="t-sil">Importar imóveis SIL</h1>
        <p className="sutil">Envie o CSV do cadastro físico de imóveis da SIL. A importação continua no servidor e esta tela acompanha o resultado.</p>
      </header>
      <form onSubmit={enviar}>
        <label htmlFor="sil-arquivo">Arquivo CSV</label>
        <input id="sil-arquivo" ref={arquivo} name="arquivo" type="file" accept=".csv,text/csv" required disabled={ocupado} />
        <div className="acoes">
          <button className="btn" type="submit" disabled={ocupado}>{enviando ? 'Enviando…' : 'Importar'}</button>
        </div>
      </form>
      <div aria-live="polite">
        {erro && <p className="aviso erro" role="alert">{erro}</p>}
        {estado && <p>Status: <strong>{TEXTO[estado.situacao] ?? estado.situacao}</strong></p>}
        {estado?.situacao === 'concluido' && <p>Total carregado: <strong>{n(estado.total)}</strong></p>}
        {estado?.situacao === 'erro' && estado.mensagem && <p className="sutil">{estado.mensagem}</p>}
      </div>
    </section>
  )
}
