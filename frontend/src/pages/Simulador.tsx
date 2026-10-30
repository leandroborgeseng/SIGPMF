import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, ApiErro, dataBR } from '../api'
import { useAuth } from '../auth'
import { Erro, ErrorBoundary, Esqueleto } from '../componentes'
import OrigemDado from '../OrigemDado'
import { AcessosCidadao, ImpostosCidadao, LinhaDoTempoCidadao, ResumoPortal, SaudeCidadao, useDadosFicha } from '../FichaPortal'

type Item = { cidadao_id: string; nome: string | null; cpf: string | null; sistemas: string[] | null }
type Dado = { dominio: string; campo: string; valor: string | null; fonte: string | null; atualizado_em: string | null }
type Ficha = { cidadao_id: string; nome: string | null; cpf: string | null; cns?: string | null; sistemas: string[] | null; dados: Dado[] | null; enderecos: EnderecoFicha[] | null }
type EnderecoFicha = { valor: string | null; linhas?: string[]; fonte: string | null; atualizado_em: string | null; mais_recente: boolean; origem_endereco?: string | null }

const DOMINIO: Record<string, string> = { identificacao: 'Identificação', endereco: 'Endereço', saude: 'Saúde', cemiterio: 'Cemitério' }
const CAMPO: Record<string, string> = { nome: 'Nome', data_nascimento: 'Data de nascimento', nome_mae: 'Nome da mãe', endereco: 'Endereço', cadastro: 'Cadastro' }
const MOTIVO_MIN = Number(import.meta.env.VITE_REVELAR_MOTIVO_MIN ?? 10)

export default function Simulador() {
  const [busca, setBusca] = useState('')
  const [buscado, setBuscado] = useState('')
  const [lista, setLista] = useState<Item[] | null>(null)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState(false)
  const [params] = useSearchParams()
  const [sel, setSel] = useState<string | null>(params.get('cid'))  // VN5: "Abrir ficha completa" do explorador

  const buscar = async (e?: FormEvent) => {
    e?.preventDefault()
    setErro(false); setCarregando(true); setSel(null); setBuscado(busca)
    try { setLista((await api<Item[] | null>(`/silver/cidadaos?busca=${encodeURIComponent(busca)}`)) ?? []) }
    catch { setErro(true) } finally { setCarregando(false) }
  }

  return (
    <section aria-labelledby="t-sim">
      <h1 id="t-sim">Simulador do Cidadão</h1>
      <form role="search" onSubmit={buscar} className="busca">
        <label htmlFor="busca">Nome ou CPF completo</label>
        <input id="busca" value={busca} onChange={(e) => setBusca(e.target.value)} />
        <button className="btn" type="submit">Buscar</button>
      </form>
      {erro ? <Erro texto="Não foi possível carregar os dados do cidadão. Tente novamente." onRetry={buscar} />
        : carregando ? <Esqueleto />
        : lista === null ? <p className="vazio">Busque um cidadão por nome ou CPF para ver os dados integrados.</p>
        : lista.length === 0 ? <p className="vazio">Nenhum cidadão encontrado para "{buscado}". Confira a grafia ou busque pelo CPF.</p>
        : !sel && (
          <ul className="lista-cartoes" aria-label="Resultados">
            {lista.map((c) => (
              <li key={c.cidadao_id} className="cartao">
                <button className="link-btn" onClick={() => setSel(c.cidadao_id)}>{c.nome || 'Nome não informado'}</button>
                <div>CPF: {c.cpf ?? 'não informado'} · Sistemas: {(c.sistemas ?? []).join(', ') || '—'}</div>
              </li>
            ))}
          </ul>
        )}
      {sel && (
        <ErrorBoundary chave={sel} titulo="Não foi possível carregar os dados deste cidadão." onVoltar={() => setSel(null)} rotuloVoltar="Voltar à busca">
          <FichaCidadao id={sel} voltar={() => setSel(null)} />
        </ErrorBoundary>
      )}
    </section>
  )
}

function FichaCidadao({ id, voltar }: { id: string; voltar: () => void }) {
  const { sessao } = useAuth()
  const [f, setF] = useState<Ficha | null>(null)
  const [erro, setErro] = useState(false)
  const [cpf, setCpf] = useState<string | null>(null)
  const [dialogo, setDialogo] = useState(false)
  const [aviso, setAviso] = useState('')
  const btnRevelar = useRef<HTMLButtonElement>(null)
  const { trib, saude } = useDadosFicha(id)  // FC1: uma busca de cada, entregue aos blocos

  const [motivo, setMotivo] = useState('')
  const carregar = () => {
    setErro(false); setF(null)
    api<Ficha>(`/gold/cidadaos/${id}`).then(setF).catch((e) => {
      setMotivo(e instanceof ApiErro ? `HTTP ${e.status}` : 'falha de rede'); setErro(true)
    })
  }
  useEffect(carregar, [id])
  useEffect(() => () => setCpf(null), [id])

  const revelado = (valor: string, segundos: number) => {
    setCpf(valor); setAviso('Revelação registrada na auditoria.')
    window.setTimeout(() => setCpf(null), segundos * 1000)
  }
  if (erro) return (
    <>
      <Erro texto={`Não foi possível carregar os dados deste cidadão (${motivo}). Tente de novo.`} onRetry={carregar} />
      <button className="btn secundario" onClick={voltar}>Voltar à busca</button>
    </>
  )
  if (!f) return <Esqueleto texto="Carregando dados do cidadão…" />
  const enderecos = f.enderecos ?? []
  const grupos = (f.dados ?? []).reduce<Record<string, Dado[]>>((acc, d) => ((acc[d.dominio] ??= []).push(d), acc), {})
  const fmt = (c: string) => c.length !== 11 ? c : `${c.slice(0, 3)}.${c.slice(3, 6)}.${c.slice(6, 9)}-${c.slice(9)}`
  return (
    <article className="ficha" aria-labelledby="t-ficha">
      <button className="btn secundario" onClick={voltar}>Voltar à busca</button>
      <h2 id="t-ficha">{f.nome || 'Nome não informado'}</h2>
      <p>
        CPF: <span data-testid="cpf">{cpf ? fmt(cpf) : (f.cpf ?? 'não informado')}</span>{' '}
        {sessao?.perfil === 'admin_lgpd' && f.cpf && !cpf && (
          <button ref={btnRevelar} className="icone-btn" aria-label="Revelar CPF" onClick={() => setDialogo(true)}>👁</button>
        )}
      </p>
      <div aria-live="polite">{aviso && <p className="aviso">{aviso}</p>}</div>
      <ResumoPortal trib={trib} saude={saude} />
      <div className="portal-grade">
        <ImpostosCidadao id={id} dados={trib} />
        <SaudeCidadao saude={saude} />
      </div>
      <section className="cartao portal-bloco" aria-labelledby="t-cadastro">
      <h3 id="t-cadastro">Dados cadastrais</h3>
      {Object.keys(grupos).length === 0 && <p className="vazio">—</p>}
      {Object.entries(grupos).map(([dom, itens]) => (
        <section key={dom} aria-labelledby={`d-${dom}`}>
          {/* EN9/UXO9: o rótulo aparece uma vez só (domínio com o mesmo nome do único campo não repete o título) */}
          {!rotuloUnico(dom, itens) && <h3 id={`d-${dom}`}>{DOMINIO[dom] ?? dom}</h3>}
          <dl>
            {itens.map((d) => (
              <div key={`${d.campo}-${d.fonte}`} className="campo">
                <dt id={rotuloUnico(dom, itens) ? `d-${dom}` : undefined}>{CAMPO[d.campo] ?? d.campo}</dt>
                <dd>
                  <ValorCampo d={d} />
                  <OrigemDado fonte={d.fonte} atualizadoEm={d.atualizado_em} />
                  {d.campo === 'endereco' && <OutrosValores itens={outrosEnderecos(enderecos, d)} />}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      </section>
      <div className="portal-grade">
        <LinhaDoTempoCidadao id={id} />
        <AcessosCidadao id={id} />
      </div>
      {dialogo && <DialogoRevelar id={id} fechar={() => { setDialogo(false); btnRevelar.current?.focus() }} aoRevelar={revelado} />}
    </article>
  )
}

function DialogoRevelar({ id, fechar, aoRevelar }: { id: string; fechar: () => void; aoRevelar: (cpf: string, s: number) => void }) {
  const [motivo, setMotivo] = useState('')
  const [erro, setErro] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    el?.querySelector<HTMLElement>('textarea')?.focus()
    const f = (e: KeyboardEvent) => {
      if (e.key === 'Escape') fechar()
      if (e.key === 'Tab' && el) {
        const foc = Array.from(el.querySelectorAll<HTMLElement>('textarea, button:not([disabled])'))
        const i = foc.indexOf(document.activeElement as HTMLElement)
        if (e.shiftKey && i <= 0) { e.preventDefault(); foc[foc.length - 1]?.focus() }
        else if (!e.shiftKey && i === foc.length - 1) { e.preventDefault(); foc[0]?.focus() }
      }
    }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [fechar])
  const revelar = async () => {
    setErro('')
    try {
      const r = await api<{ cpf: string; remascarar_em_segundos: number }>(`/cidadaos/${id}/revelar-cpf`, { method: 'POST', body: JSON.stringify({ motivo }) })
      aoRevelar(r.cpf, r.remascarar_em_segundos ?? 60)
      fechar()
    } catch (e) {
      setErro(e instanceof ApiErro && e.status === 400 ? 'Informe o motivo para revelar o CPF.' : 'Não foi possível revelar o CPF.')
    }
  }
  return (
    <div className="veu-dialogo">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="t-revelar" className="dialogo">
        <h2 id="t-revelar">Revelar CPF</h2>
        <p>Esta ação fica registrada na auditoria com seu usuário, data e motivo.</p>
        <label htmlFor="motivo">Motivo</label>
        <textarea id="motivo" value={motivo} onChange={(e) => setMotivo(e.target.value)} required aria-describedby="motivo-ajuda" />
        <small id="motivo-ajuda">Mínimo de {MOTIVO_MIN} caracteres.</small>
        <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
        <div className="acoes">
          <button className="btn secundario" onClick={fechar}>Cancelar</button>
          <button className="btn" onClick={revelar} disabled={motivo.trim().length < MOTIVO_MIN}>Revelar</button>
        </div>
      </div>
    </div>
  )
}

const rotuloUnico = (dom: string, itens: Dado[]) => itens.length === 1 && (CAMPO[itens[0].campo] ?? itens[0].campo) === DOMINIO[dom]

/** UXO1/UXO6/EN8: valor em destaque; endereço em 2 linhas; sem dado, "Não informado na origem" em itálico. */
function ValorCampo({ d }: { d: Dado }) {
  if (!d.valor) return <span className="campo-valor nao-informado">Não informado na origem</span>
  if (d.campo === 'data_nascimento' || d.campo === 'data_obito') return <span className="campo-valor">{dataBR(d.valor)}</span>
  return <span className="campo-valor">{d.valor.split('\n').map((l, i) => <span key={i} className="linha">{l}</span>)}</span>
}

function outrosEnderecos(ends: EnderecoFicha[], d: Dado): EnderecoFicha[] {
  let pulou = false  // o valor exibido (mesma fonte e texto) sai da lista uma vez
  return ends.filter((e) => {
    if (!pulou && e.fonte === d.fonte && e.valor === d.valor) { pulou = true; return false }
    return true
  })
}

/** UXO5: "Ver outros N valores" abre a lista de valor + chip por fonte. */
function OutrosValores({ itens }: { itens: EnderecoFicha[] }) {
  const [aberto, setAberto] = useState(false)
  if (itens.length === 0) return null
  const n = itens.length
  return (
    <div>
      <button className="btn-link" aria-expanded={aberto} onClick={() => setAberto(!aberto)}>
        {aberto ? 'Ocultar outros valores' : `Ver ${n === 1 ? 'outro valor' : `outros ${n} valores`}`}
      </button>
      {aberto && (
        <ul className="outros-valores">
          {itens.map((e, i) => (
            <li key={i}>
              {e.valor ? <span className="campo-valor">{(e.linhas?.length ? e.linhas : e.valor.split('\n')).map((l, j) => <span key={j} className="linha">{l}</span>)}</span>
                : <span className="campo-valor nao-informado">Não informado na origem</span>}
              <OrigemDado fonte={e.fonte} atualizadoEm={e.atualizado_em} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
