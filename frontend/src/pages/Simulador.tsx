import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, ApiErro, dataBR } from '../api'
import { useAuth } from '../auth'
import { Erro, ErrorBoundary, Esqueleto } from '../componentes'
import OrigemDado, { nomeFonte } from '../OrigemDado'
import { AcessosCidadao, CemiterioCidadao, EducacaoCidadao, estadoArea, ImpostosCidadao, LinhaDoTempoCidadao, MapaAreas, ResumoPortal, SaudeCidadao, useDadosFicha, type ItemMapa } from '../FichaPortal'
import { reais } from '../ImpostosCidadao'

type Item = { cidadao_id: string; nome: string | null; cpf: string | null; sistemas: string[] | null }
type Empresa = { documento: string; tipo_pessoa: string | null; nome: string | null; ccms: number[]; possui_debito: boolean | null; imoveis_ativos: number; fontes: string[] }

/** CN4: CNPJ completo (numérico ou alfanumérico), raiz do CNPJ (8) ou CCM vão para a busca de empresa; o resto é cidadão. */
function ehBuscaDeEmpresa(texto: string): boolean {
  const t = texto.replace(/[\s./-]/g, '').toUpperCase()
  if (/^\d{11}$/.test(t)) return false  // CPF
  return /^[0-9A-Z]{12}\d{2}$/.test(t) || (/^[0-9A-Z]{8}$/.test(t) && /\d/.test(t)) || /^\d{1,9}$/.test(t)
}
type Dado = { dominio: string; campo: string; valor: string | null; fonte: string | null; atualizado_em: string | null }
type Ficha = { cidadao_id: string; nome: string | null; cpf: string | null; cns?: string | null; ve_cadastro?: boolean; sistemas: string[] | null; dados: Dado[] | null; enderecos: EnderecoFicha[] | null }
type EnderecoFicha = { valor: string | null; linhas?: string[]; fonte: string | null; atualizado_em: string | null; mais_recente: boolean; origem_endereco?: string | null }

// Sempre na ficha, com ou sem valor; campo novo do ouro entra depois destes, com o próprio nome
const CAMPOS_FIXOS: [string, string][] = [['nome', 'Nome'], ['data_nascimento', 'Data de nascimento'], ['sexo', 'Sexo'], ['nome_mae', 'Nome da mãe'], ['nome_pai', 'Nome do pai'], ['endereco', 'Endereço']]
const FORA_DA_LISTA = new Set([...CAMPOS_FIXOS.map(([c]) => c), 'cadastro', 'falecido', 'data_obito'])
const SEXO: Record<string, string> = { F: 'Feminino', M: 'Masculino' }
const MOTIVO_MIN = Number(import.meta.env.VITE_REVELAR_MOTIVO_MIN ?? 10)

export default function Simulador() {
  const [busca, setBusca] = useState('')
  const [buscado, setBuscado] = useState('')
  const [lista, setLista] = useState<Item[] | null>(null)
  const [empresas, setEmpresas] = useState<Empresa[] | null>(null)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState(false)
  const [recusa, setRecusa] = useState<string | null>(null)
  const [params] = useSearchParams()
  const [sel, setSel] = useState<string | null>(params.get('cid'))  // VN5: "Abrir ficha completa" do explorador

  const buscar = async (e?: FormEvent) => {
    e?.preventDefault()
    setErro(false); setRecusa(null); setCarregando(true); setSel(null); setBuscado(busca)
    try {
      if (ehBuscaDeEmpresa(busca)) {
        setLista(null); setEmpresas((await api<Empresa[] | null>(`/silver/empresas?busca=${encodeURIComponent(busca)}`)) ?? [])
      } else {
        setEmpresas(null); setLista((await api<Item[] | null>(`/silver/cidadaos?busca=${encodeURIComponent(busca)}`)) ?? [])
      }
    } catch (x) {
      // 400 = busca mal formada (CNPJ com DV errado...): mostra o motivo; 403 = sem acesso ao tributário
      if (x instanceof ApiErro && (x.status === 400 || x.status === 403)) { setLista(null); setEmpresas(null); setRecusa(x.message) }
      else setErro(true)
    } finally { setCarregando(false) }
  }

  return (
    <section aria-labelledby="t-sim">
      <h1 id="t-sim">Portal do Cidadão</h1>
      <p className="sutil">Selecione um cidadão para ver, área por área, o que a prefeitura tem registrado sobre essa pessoa.</p>
      <form role="search" onSubmit={buscar} className="busca">
        <label htmlFor="busca">Nome, CPF, CNPJ ou CCM</label>
        <input id="busca" value={busca} onChange={(e) => setBusca(e.target.value)} />
        <button className="btn" type="submit">Buscar</button>
      </form>
      {erro ? <Erro texto="Não foi possível carregar os dados do cidadão. Tente novamente." onRetry={buscar} />
        : carregando ? <Esqueleto />
        : recusa ? <p className="vazio" role="alert">{recusa}</p>
        : empresas !== null ? <ListaEmpresas empresas={empresas} buscado={buscado} />
        : lista === null ? !sel && <p className="vazio">Busque um cidadão por nome ou CPF, ou uma empresa pelo CNPJ ou CCM.</p>
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

function ListaEmpresas({ empresas, buscado }: { empresas: Empresa[]; buscado: string }) {
  if (empresas.length === 0) return <p className="vazio">Nenhuma empresa encontrada para "{buscado}" na SIL, no ISS nem no IPTU.</p>
  return (
    <>
      <p className="sutil">CNPJ e razão social aparecem mascarados (LGPD). A ficha completa da empresa vem numa próxima etapa.</p>
      <ul className="lista-cartoes" aria-label="Empresas encontradas">
        {empresas.map((e) => (
          <li key={`${e.documento}-${e.ccms.join(',')}`} className="cartao">
            <strong>{e.nome || 'Nome não informado'}</strong>
            <div>{e.tipo_pessoa === 'PF' ? 'CPF' : 'CNPJ'}: {e.documento} · CCM: {e.ccms.join(', ') || '—'}</div>
            <div>
              {e.possui_debito === true ? 'Com débito na SIL' : e.possui_debito === false ? 'Sem débito na SIL' : 'Débito: sem informação'}
              {' · '}{e.imoveis_ativos} {e.imoveis_ativos === 1 ? 'imóvel ativo' : 'imóveis ativos'} · Fontes: {e.fontes.join(', ')}
            </div>
          </li>
        ))}
      </ul>
    </>
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
  const { trib, saude, educacao, cemiterio, fontes, leituras } = useDadosFicha(id)  // FC1: uma busca de cada, entregue aos blocos

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
  const dados = f.dados ?? []
  const fmt = (c: string) => c.length !== 11 ? c : `${c.slice(0, 3)}.${c.slice(3, 6)}.${c.slice(6, 9)}-${c.slice(9)}`
  const dado = (campo: string) => dados.find((d) => d.campo === campo)
  const veCadastro = f.ve_cadastro !== false
  const nasc = dado('data_nascimento')?.valor
  const obito = dado('data_obito')?.valor
  const falecido = !!dado('falecido') || !!obito
  const sistemas = [...new Set(dados.filter((d) => d.campo === 'cadastro').map((d) => nomeFonte(d.fonte)))]
  const extras = dados.filter((d) => !FORA_DA_LISTA.has(d.campo))
  const t = trib || null, c = cemiterio || null, sa = saude || null
  const conta = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`
  const nTumulos = c ? c.titular.length + c.autorizado.length + c.leilao.length : 0
  const mapa: ItemMapa[] = [
    { id: 'vacinas', nome: 'Vacinas', estado: estadoArea(leituras.saude, sa?.totais?.vacinas ?? sa?.vacinas.length ?? 0, fontes, 'vacinas'), valor: conta(sa?.totais?.vacinas ?? sa?.vacinas.length ?? 0, 'dose', 'doses') },
    { id: 'consultas', nome: 'Consultas', estado: estadoArea(leituras.saude, sa?.totais?.atendimentos ?? sa?.atendimentos.length ?? 0, fontes, 'consultas'), valor: conta(sa?.totais?.atendimentos ?? sa?.atendimentos.length ?? 0, 'consulta', 'consultas') },
    { id: 'agendamentos', nome: 'Agendamentos', estado: estadoArea(leituras.saude, sa?.proximos.length ?? 0, fontes, 'agendamentos'), valor: sa?.proximos[0] ? `Próximo ${dataBR(sa.proximos[0].data)}` : '' },
    { id: 'imoveis', nome: 'Imóveis', estado: estadoArea(leituras.tributario, t?.imoveis.length ?? 0, fontes, 'imoveis'), valor: t ? `${t.totais.imoveis} atua${t.totais.imoveis === 1 ? 'l' : 'is'}` : '' },
    { id: 'lancamentos', nome: 'Impostos e taxas', estado: estadoArea(leituras.tributario, t?.totais.lancamentos ?? t?.lancamentos.length ?? 0, fontes, 'lancamentos'), valor: t ? (t.totais.em_aberto > 0 ? `${reais(t.totais.em_aberto)} em aberto` : 'Nada em aberto') : '' },
    { id: 'dividas', nome: 'Dívida ativa', estado: estadoArea(leituras.tributario, t?.dividas.length ?? 0, fontes, 'dividas'), valor: t ? (t.totais.divida_ativa > 0 ? `${reais(t.totais.divida_ativa)} em aberto` : 'Sem saldo em aberto') : '' },
    { id: 'empresas', nome: 'Empresas', estado: estadoArea(leituras.tributario, t?.empresas?.length ?? 0, fontes, 'empresas'), valor: conta(t?.empresas?.length ?? 0, 'empresa', 'empresas') },
    { id: 'educacao', nome: 'Educação', estado: estadoArea(leituras.educacao, educacao ? educacao.alunos.length : 0, fontes, 'educacao'), valor: educacao && educacao.alunos.some((a) => a.matricula_atual) ? 'Matrícula atual' : 'Só histórico' },
    { id: 'tumulos', nome: 'Túmulos', estado: estadoArea(leituras.cemiterio, nTumulos, fontes, 'cemiterio'), valor: conta(nTumulos, 'túmulo', 'túmulos') },
    { id: 'sepultamento', nome: 'Sepultamento', estado: estadoArea(leituras.cemiterio, c?.sepultamento.length ?? 0, fontes, 'cemiterio'), valor: 'Confirmado' },
  ]
  return (
    <article className="ficha" aria-labelledby="t-ficha">
      <button className="btn secundario ficha-voltar" onClick={voltar}>Voltar à busca</button>
      <header className="ficha-topo">
        <p className="ficha-kicker">Visão do cidadão · esta consulta fica registrada na auditoria</p>
        <h2 id="t-ficha">{f.nome || 'Nome não informado'}{falecido && <span className="selo neutro ficha-selo"><i aria-hidden="true" />Falecido{obito ? ` em ${dataBR(obito)}` : ''}</span>}</h2>
        <p className="ficha-doc">
          <span>CPF: <span data-testid="cpf">{cpf ? fmt(cpf) : (f.cpf ?? 'não informado')}</span>{' '}
            {sessao?.perfil === 'admin_lgpd' && f.cpf && !cpf && (
              <button ref={btnRevelar} className="icone-btn" aria-label="Revelar CPF" onClick={() => setDialogo(true)}>👁</button>
            )}</span>
          {nasc && !falecido && <span>{idadeEmAnos(nasc)} anos</span>}
          <span>Cartão SUS: {f.cns ?? 'não informado'}</span>
        </p>
        <div aria-live="polite">{aviso && <p className="aviso">{aviso}</p>}</div>
      </header>
      <MapaAreas itens={mapa} naoIntegradas={fontes ? fontes.nao_integradas : []} />
      <ResumoPortal trib={trib} saude={saude} />
      <section className="cartao portal-bloco" aria-labelledby="t-cadastro">
        <h3 id="t-cadastro">Dados cadastrais</h3>
        {!veCadastro ? <p className="area-indefinida">Seu perfil não tem acesso aos dados cadastrais. Isso não quer dizer que não haja registro.</p> : (
          <dl className="cadastro-grade">
            {CAMPOS_FIXOS.map(([campo, rotulo]) => {
              const d = dado(campo) ?? { dominio: '', campo, valor: null, fonte: null, atualizado_em: null }
              return (
                <div key={campo} className={`campo${campo === 'endereco' ? ' campo-largo' : ''}`}>
                  <dt id={campo === 'endereco' ? 'd-endereco' : undefined}>{rotulo}</dt>
                  <dd>
                    <ValorCampo d={d} />
                    {d.fonte && <OrigemDado fonte={d.fonte} atualizadoEm={d.atualizado_em} />}
                    {campo === 'endereco' && <OutrosValores itens={outrosEnderecos(enderecos, d)} />}
                  </dd>
                </div>
              )
            })}
            {extras.map((d) => (
              <div key={`${d.campo}-${d.fonte}`} className="campo">
                <dt>{d.campo.replace(/_/g, ' ')}</dt>
                <dd><ValorCampo d={d} /><OrigemDado fonte={d.fonte} atualizadoEm={d.atualizado_em} /></dd>
              </div>
            ))}
            <div className="campo campo-largo">
              <dt>Sistemas em que aparece</dt>
              <dd>{sistemas.length ? sistemas.join(', ') : <span className="campo-valor nao-informado">Nenhum sistema vinculado</span>}</dd>
            </div>
          </dl>
        )}
      </section>
      <div className="portal-grade">
        <SaudeCidadao saude={saude} leitura={leituras.saude} fontes={fontes} />
        <div className="portal-coluna">
          <EducacaoCidadao educacao={educacao} leitura={leituras.educacao} fontes={fontes} />
          <CemiterioCidadao cemiterio={cemiterio} leitura={leituras.cemiterio} fontes={fontes} />
        </div>
      </div>
      <ImpostosCidadao id={id} dados={trib} leitura={leituras.tributario} fontes={fontes} />
      <div className="portal-grade">
        <LinhaDoTempoCidadao id={id} />
        <AcessosCidadao id={id} />
      </div>
      {dialogo && <DialogoRevelar id={id} fechar={() => { setDialogo(false); btnRevelar.current?.focus() }} aoRevelar={revelado} />}
    </article>
  )
}

function idadeEmAnos(iso: string): number {
  const n = new Date(`${iso.slice(0, 10)}T00:00:00`), h = new Date()
  return h.getFullYear() - n.getFullYear() - (h.getMonth() < n.getMonth() || (h.getMonth() === n.getMonth() && h.getDate() < n.getDate()) ? 1 : 0)
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

/** UXO1/UXO6/EN8: valor em destaque; endereço em 2 linhas; sem dado, "Não informado na origem" em itálico. */
function ValorCampo({ d }: { d: Dado }) {
  if (!d.valor) return <span className="campo-valor nao-informado">Não informado na origem</span>
  if (d.campo === 'data_nascimento' || d.campo === 'data_obito') return <span className="campo-valor">{dataBR(d.valor)}</span>
  if (d.campo === 'sexo') return <span className="campo-valor">{SEXO[d.valor.toUpperCase()] ?? d.valor}</span>
  return <span className="campo-valor">{d.valor.split('\n').map((l, i) => <Linha key={i} texto={l} />)}</span>
}

/** R1/UXO6: textos fixos de ausência (rua/bairro sem referência ou não informados) em itálico e cor secundária. */
const AUSENCIA = /^(Rua não informada na origem|Rua cadastrada na origem \(referência não carregada\)|Bairro cadastrado na origem \(referência não carregada\))/
function Linha({ texto }: { texto: string }) {
  return <span className="linha">{texto.split(' · ').map((p, i) => (
    <span key={i}>{i > 0 && ' · '}{AUSENCIA.test(p) ? <span className="nao-informado">{p}</span> : p}</span>))}</span>
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
              {e.valor ? <span className="campo-valor">{(e.linhas?.length ? e.linhas : e.valor.split('\n')).map((l, j) => <Linha key={j} texto={l} />)}</span>
                : <span className="campo-valor nao-informado">Não informado na origem</span>}
              <OrigemDado fonte={e.fonte} atualizadoEm={e.atualizado_em} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
