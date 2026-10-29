import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiErro, dataBR, dataHoraBR } from '../api'
import { useAuth } from '../auth'
import { Esqueleto } from '../componentes'

/** PR-06 (R5/R6, UXV1–UXV8): tela de TI para vincular usuários às secretarias. Só ADMIN.
 *  O termo de busca e o id do usuário ficam só no estado da tela: todas as chamadas são POST com corpo (UXV1). */
type UsuarioAchado = { id: number; nome: string; email: string; perfil: string; secretarias: number }
type Vinculo = {
  id: number; secretaria: string; secretaria_nome: string; prefeito: boolean; inicio: string; fim: string | null
  vencido: boolean; futuro: boolean; cadastrado_por: string; cadastrado_em: string; versao: number
}
type Historico = { quando: string; quem: string; acao: string; secretaria: string; motivo: string | null }
type Secretaria = { codigo: string; nome: string }

const B = '/ti/acesso-secretaria'
const MOTIVO_MIN = 10
const ERRO_SALVAR = 'Não foi possível salvar. Tente de novo.'
const CONFLITO = 'Este vínculo foi alterado por outra pessoa. Recarregue para ver a versão atual.'
const post = <T,>(c: string, corpo: object) => api<T>(B + c, { method: 'POST', body: JSON.stringify(corpo) })
const hojeISO = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(new Date())

function useFocoPreso(ref: React.RefObject<HTMLDivElement | null>, fechar: () => void) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { fechar(); return }
      const el = ref.current
      if (e.key === 'Tab' && el) {
        const foc = Array.from(el.querySelectorAll<HTMLElement>('input, select, textarea, button:not([disabled])'))
        const i = foc.indexOf(document.activeElement as HTMLElement)
        if (e.shiftKey && i <= 0) { e.preventDefault(); foc[foc.length - 1]?.focus() }
        else if (!e.shiftKey && i === foc.length - 1) { e.preventDefault(); foc[0]?.focus() }
      }
    }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [ref, fechar])
}

function Vigencia({ v }: { v: Vinculo }) {
  return (
    <>
      {v.fim ? `de ${dataBR(v.inicio)} até ${dataBR(v.fim)}` : `de ${dataBR(v.inicio)}, sem data de fim`}
      {v.vencido && <> <span className="selo falha">Vencido</span></>}
      {v.futuro && <> <span className="selo neutro">Ainda não começou</span></>}
    </>
  )
}

export default function AcessoSecretaria() {
  const { sessao } = useAuth()
  if (sessao?.perfil !== 'admin') {
    return (
      <section className="cartao" aria-labelledby="t-acesso"><h1 id="t-acesso" className="sr-only">Acesso por secretaria</h1>
        <p role="alert">Você não tem permissão para esta tela.</p></section>
    )
  }
  return <TelaAcesso />
}

function TelaAcesso() {
  const [termo, setTermo] = useState('')
  const [achados, setAchados] = useState<UsuarioAchado[] | null>(null)
  const [buscando, setBuscando] = useState(false)
  const [erroBusca, setErroBusca] = useState('')
  const [sel, setSel] = useState<UsuarioAchado | null>(null)
  const [semPermissao, setSemPermissao] = useState(false)

  useEffect(() => {
    const t = termo.trim()
    if (t.length < 3) { setAchados(null); return }
    const h = setTimeout(async () => {
      setBuscando(true); setErroBusca('')
      try { setAchados(await post<UsuarioAchado[]>('/usuarios/buscar', { termo: t })) }
      catch (e) {
        if (e instanceof ApiErro && e.status === 403) setSemPermissao(true)
        else setErroBusca('Não foi possível buscar. Tente de novo.')
      } finally { setBuscando(false) }
    }, 300)
    return () => clearTimeout(h)
  }, [termo])

  if (semPermissao) return <section className="cartao"><p role="alert">Você não tem permissão para esta tela.</p></section>
  return (
    <section aria-labelledby="t-acesso" className="acesso-sec">
      <header className="page-head">
        <p className="kicker">TI · administração</p>
        <h1 id="t-acesso">Acesso por secretaria</h1>
        <p className="sutil">Defina quais secretarias cada usuário pode consultar.</p>
      </header>
      <form className="cartao" role="search" onSubmit={(e: FormEvent) => e.preventDefault()}>
        <label htmlFor="as-busca">Buscar usuário por nome ou e-mail</label>
        <input id="as-busca" type="search" autoComplete="off" value={termo} onChange={(e) => setTermo(e.target.value)}
          aria-describedby="as-busca-ajuda" />
        <small id="as-busca-ajuda" className="sutil">Digite ao menos 3 caracteres.</small>
        <div aria-live="polite">
          {buscando && <span className="sr-only">Buscando…</span>}
          {erroBusca && <p className="erro-texto">{erroBusca}</p>}
          {achados && achados.length === 0 && <p>Nenhum usuário encontrado para esse termo.</p>}
        </div>
        {achados && achados.length > 0 && (
          <ul className="as-resultados">
            {achados.map((u) => (
              <li key={u.id}>
                <button type="button" className={`as-usuario${sel?.id === u.id ? ' ativo' : ''}`} aria-pressed={sel?.id === u.id}
                  onClick={() => setSel(u)}>
                  <strong>{u.nome}</strong> <span className="sutil">{u.email} · {u.perfil} · {u.secretarias} {u.secretarias === 1 ? 'secretaria' : 'secretarias'}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </form>
      {sel && <PainelUsuario key={sel.id} usuario={sel} />}
    </section>
  )
}

function PainelUsuario({ usuario }: { usuario: UsuarioAchado }) {
  const [vinculos, setVinculos] = useState<Vinculo[] | null>(null)
  const [historico, setHistorico] = useState<Historico[]>([])
  const [secretarias, setSecretarias] = useState<Secretaria[]>([])
  const [erro, setErro] = useState('')
  const [aviso, setAviso] = useState('')
  const [adicionando, setAdicionando] = useState(false)
  const [remover, setRemover] = useState<Vinculo | null>(null)
  const [editar, setEditar] = useState<Vinculo | null>(null)

  const carregar = useCallback(async () => {
    setErro('')
    try {
      const [l, h] = await Promise.all([
        post<{ vinculos: Vinculo[] }>('/vinculos/listar', { usuario_id: usuario.id }),
        post<Historico[]>('/vinculos/historico', { usuario_id: usuario.id }),
      ])
      setVinculos(l.vinculos); setHistorico(h)
    } catch { setErro('Não foi possível carregar. Tente de novo.') }
  }, [usuario.id])
  useEffect(() => { void carregar() }, [carregar])
  useEffect(() => { post<Secretaria[]>('/secretarias', {}).then(setSecretarias).catch(() => setSecretarias([])) }, [])

  const depois = async (msg: string) => { setAviso(msg); await carregar() }
  const disponiveis = secretarias.filter((s) => !vinculos?.some((v) => v.secretaria === s.codigo))

  return (
    <section className="cartao as-painel" aria-labelledby="t-painel">
      <h2 id="t-painel">Secretarias de {usuario.nome}</h2>
      <div aria-live="polite" className="sr-live">{aviso && <p className="aviso-ok">{aviso}</p>}</div>
      {erro && <div role="alert" className="aviso erro"><p>{erro}</p><button className="btn" onClick={() => void carregar()}>Tentar novamente</button></div>}
      {!erro && vinculos === null && <Esqueleto linhas={3} texto="Carregando secretarias…" />}
      {vinculos && vinculos.length === 0 && <p>Este usuário ainda não tem acesso a nenhuma secretaria.</p>}
      {vinculos && vinculos.length > 0 && (
        <table className="tabela as-tabela">
          <caption className="sr-only">Secretarias de {usuario.nome}</caption>
          <thead><tr><th scope="col">Secretaria</th><th scope="col">Vigência</th><th scope="col">Cadastrado por</th><th scope="col">Ações</th></tr></thead>
          <tbody>
            {vinculos.map((v) => (
              <tr key={v.id} className={v.vencido ? 'vencido' : undefined}>
                <th scope="row" data-rotulo="Secretaria">{v.secretaria_nome}</th>
                <td data-rotulo="Vigência"><Vigencia v={v} /></td>
                <td data-rotulo="Cadastrado por">{v.cadastrado_por} · {dataHoraBR(v.cadastrado_em)} BRT</td>
                <td data-rotulo="Ações" className="as-acoes">
                  <button className="btn secundario" onClick={() => setEditar(v)}
                    aria-label={`Editar vigência de ${v.secretaria_nome}`}>Editar vigência</button>
                  {!v.prefeito && (
                    <button className="btn ghost perigo" onClick={() => setRemover(v)}
                      aria-label={`Remover ${v.secretaria_nome}`}>Remover</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {vinculos && !adicionando && (
        <button className="btn" onClick={() => setAdicionando(true)} disabled={disponiveis.length === 0}>Adicionar secretaria</button>
      )}
      {adicionando && (
        <FormAdicionar usuarioId={usuario.id} opcoes={disponiveis} fechar={() => setAdicionando(false)}
          ok={(nome) => { setAdicionando(false); void depois(`Acesso à ${nome} adicionado.`) }} />
      )}
      <details className="as-historico">
        <summary>Ver histórico de alterações</summary>
        {historico.length === 0 ? <p className="sutil">Nenhuma alteração registrada.</p> : (
          <ul>
            {historico.map((h, i) => (
              <li key={i}><time>{dataHoraBR(h.quando)} BRT</time> · {h.quem} · {h.acao} · {h.secretaria}{h.motivo ? ` · Motivo: ${h.motivo}` : ''}</li>
            ))}
          </ul>
        )}
      </details>
      {remover && <ModalRemover usuario={usuario} v={remover} fechar={() => setRemover(null)}
        ok={() => { const n = remover.secretaria_nome; setRemover(null); void depois(`Acesso à ${n} removido.`) }} />}
      {editar && <ModalEditar v={editar} fechar={() => setEditar(null)}
        ok={() => { const n = editar.secretaria_nome; setEditar(null); void depois(`Vigência de ${n} atualizada.`) }} />}
    </section>
  )
}

function msgErro(e: unknown): string {
  if (e instanceof ApiErro && e.status === 409) return CONFLITO
  if (e instanceof ApiErro && e.status === 422 && e.message.startsWith('A data de fim')) return e.message
  return ERRO_SALVAR
}

function FormAdicionar({ usuarioId, opcoes, fechar, ok }: { usuarioId: number; opcoes: Secretaria[]; fechar: () => void; ok: (nome: string) => void }) {
  const [sec, setSec] = useState('')
  const [inicio, setInicio] = useState(hojeISO())
  const [fim, setFim] = useState('')
  const [erro, setErro] = useState('')
  const fimInvalido = !!fim && !!inicio && fim < inicio
  const salvar = async (e: FormEvent) => {
    e.preventDefault(); setErro('')
    try {
      await post('/vinculos/adicionar', { usuario_id: usuarioId, secretaria: sec, inicio, fim: fim || null })
      ok(opcoes.find((o) => o.codigo === sec)?.nome ?? sec)
    } catch (x) { setErro(msgErro(x)) }
  }
  return (
    <form className="as-form" onSubmit={salvar} aria-labelledby="t-add">
      <h3 id="t-add">Adicionar secretaria</h3>
      <label htmlFor="as-sec">Secretaria</label>
      <select id="as-sec" value={sec} onChange={(e) => setSec(e.target.value)}>
        <option value="">Escolha a secretaria</option>
        {opcoes.map((o) => <option key={o.codigo} value={o.codigo}>{o.nome}</option>)}
      </select>
      <label htmlFor="as-ini">Data de início</label>
      <input id="as-ini" type="date" value={inicio} onChange={(e) => setInicio(e.target.value)} />
      <label htmlFor="as-fim">Data de fim (opcional)</label>
      <input id="as-fim" type="date" value={fim} onChange={(e) => setFim(e.target.value)} aria-invalid={fimInvalido}
        aria-describedby={fimInvalido ? 'as-fim-erro' : undefined} />
      {fimInvalido && <small id="as-fim-erro" className="erro-texto">A data de fim não pode ser anterior à de início.</small>}
      <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
      <div className="acoes">
        <button type="button" className="btn secundario" onClick={fechar}>Cancelar</button>
        <button type="submit" className="btn" disabled={!sec || !inicio || fimInvalido}>Salvar</button>
      </div>
    </form>
  )
}

function ModalRemover({ usuario, v, fechar, ok }: { usuario: UsuarioAchado; v: Vinculo; fechar: () => void; ok: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const cancelar = useRef<HTMLButtonElement>(null)
  const [motivo, setMotivo] = useState('')
  const [erro, setErro] = useState('')
  useEffect(() => { cancelar.current?.focus() }, [])
  useFocoPreso(ref, fechar)
  const confirmar = async () => {
    setErro('')
    try { await post('/vinculos/remover', { vinculo_id: v.id, versao: v.versao, motivo: motivo.trim() }); ok() }
    catch (x) { setErro(msgErro(x)) }
  }
  return (
    <div className="veu-dialogo">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="t-rem" aria-describedby="d-rem" className="dialogo">
        <h2 id="t-rem">Remover acesso</h2>
        <p id="d-rem">Remover o acesso de {usuario.nome} à {v.secretaria_nome}? Ela deixa de ver os dados dessa secretaria na hora.</p>
        <label htmlFor="as-motivo">Motivo (obrigatório)</label>
        <textarea id="as-motivo" value={motivo} onChange={(e) => setMotivo(e.target.value)} aria-describedby="as-motivo-ajuda" />
        <small id="as-motivo-ajuda" className="sutil">Mínimo de {MOTIVO_MIN} caracteres.</small>
        <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
        <div className="acoes">
          <button ref={cancelar} className="btn secundario" onClick={fechar}>Cancelar</button>
          <button className="btn danger" onClick={confirmar} disabled={motivo.trim().length < MOTIVO_MIN}>Remover acesso</button>
        </div>
      </div>
    </div>
  )
}

function ModalEditar({ v, fechar, ok }: { v: Vinculo; fechar: () => void; ok: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const cancelar = useRef<HTMLButtonElement>(null)
  const [inicio, setInicio] = useState(v.inicio)
  const [fim, setFim] = useState(v.fim ?? '')
  const [erro, setErro] = useState('')
  const fimInvalido = !!fim && fim < inicio
  useEffect(() => { cancelar.current?.focus() }, [])
  useFocoPreso(ref, fechar)
  const salvar = async () => {
    setErro('')
    try { await post('/vinculos/editar', { vinculo_id: v.id, versao: v.versao, inicio, fim: fim || null }); ok() }
    catch (x) { setErro(msgErro(x)) }
  }
  return (
    <div className="veu-dialogo">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="t-edit" className="dialogo">
        <h2 id="t-edit">Editar vigência · {v.secretaria_nome}</h2>
        <label htmlFor="ed-ini">Data de início</label>
        <input id="ed-ini" type="date" value={inicio} onChange={(e) => setInicio(e.target.value)} />
        <label htmlFor="ed-fim">Data de fim (opcional)</label>
        <input id="ed-fim" type="date" value={fim} onChange={(e) => setFim(e.target.value)} aria-invalid={fimInvalido} />
        {fimInvalido && <small className="erro-texto">A data de fim não pode ser anterior à de início.</small>}
        <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
        <div className="acoes">
          <button ref={cancelar} className="btn secundario" onClick={fechar}>Cancelar</button>
          <button className="btn" onClick={salvar} disabled={!inicio || fimInvalido}>Salvar</button>
        </div>
      </div>
    </div>
  )
}
