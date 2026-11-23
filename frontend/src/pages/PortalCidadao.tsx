import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth'
import '../portal-cidadao.css'

type Parcela = { id: string; tributo: string; exercicio: number; parcela: number | null; imovel: string | null; vencimento: string | null; valor: number | null; situacao: 'pago' | 'vencido' | 'a_vencer' }
type Acordo = { id: string; descricao: string; situacao: string; valor: number | null; vencimento: string | null }
type Cadastro = { campo: string; valor: string | null; fonte: string; atualizado_em: string | null }
type Conta = { modo_teste?: boolean; nome: string; cpf: string; nascimento: string; atualizado_em: string | null; cadastro: Cadastro[]; imoveis: { id: string; inscricao: string | null; descricao: string }[]; lancamentos: Parcela[]; totais: { em_aberto: number; vencido: number }; aviso_valores: string; lista_limitada?: boolean; csrf: string; expira_em: string }
type Config = { identificacao_disponivel: boolean; emissao_disponivel: boolean }
const moeda = (v: number | null) => v == null ? 'Valor a consultar' : new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v)
const data = (v: string | null) => v ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo' }).format(new Date(v.length === 10 ? v + 'T12:00:00Z' : v)) : 'Não informado'
const nomes: Record<string, string> = { inicio: 'Início', tributos: 'IPTU e parcelas', acordos: 'Meus acordos', cadastro: 'Meu cadastro', imoveis: 'Meus imóveis' }
const icones: Record<string, string> = { inicio: '⌂', tributos: '▤', acordos: '⇄', cadastro: '◎', imoveis: '▦' }
const rotulos = { pago: 'Pago', vencido: 'Vencido', a_vencer: 'A vencer' }
async function requisicao<T>(path: string, options: RequestInit = {}): Promise<T> {
  const r = await fetch('/api/portal' + path, { ...options, credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json', ...options.headers } })
  if (!r.ok) { let msg = 'Não foi possível concluir. Tente novamente.'; try { const d = await r.json(); if (typeof d.detail === 'string') msg = d.detail; else if (r.status === 422) msg = 'Confira o CPF, a data de nascimento e os campos obrigatórios.' } catch { /* Sem mensagem técnica no portal. */ } throw Object.assign(new Error(msg), { status: r.status }) }
  return r.json() as Promise<T>
}
const exemplo: Conta = {
  nome: 'Pessoa de demonstração', cpf: '***.***.***-**', nascimento: '1985-05-12', atualizado_em: '2026-11-20', csrf: '', expira_em: '',
  cadastro: [{ campo: 'endereco', valor: 'Rua de Exemplo, 100 · Franca/SP', fonte: 'Cadastro municipal · exemplo', atualizado_em: '2026-11-20' }, { campo: 'email', valor: 'cidadao@example.invalid', fonte: 'Cadastro municipal · exemplo', atualizado_em: '2026-11-20' }],
  imoveis: [{ id: 'demo-imovel', inscricao: 'EXEMPLO-001', descricao: 'Imóvel de demonstração' }],
  lancamentos: [{ id: 'demo-1', tributo: 'IPTU', exercicio: 2026, parcela: 10, imovel: 'demo-imovel', vencimento: '2026-10-15', valor: 182.40, situacao: 'vencido' }, { id: 'demo-2', tributo: 'IPTU', exercicio: 2026, parcela: 11, imovel: 'demo-imovel', vencimento: '2026-12-15', valor: 182.40, situacao: 'a_vencer' }, { id: 'demo-3', tributo: 'IPTU', exercicio: 2026, parcela: 9, imovel: 'demo-imovel', vencimento: '2026-09-15', valor: 182.40, situacao: 'pago' }],
  totais: { em_aberto: 364.80, vencido: 182.40 }, aviso_valores: 'Demonstração com valores fictícios. Nenhuma cobrança ou guia real é gerada.',
}
export default function PortalCidadao() {
  const { sessao } = useAuth()
  const acessoTeste = sessao?.perfil === 'admin'
  const [config, setConfig] = useState<Config | null>(null)
  const [conta, setConta] = useState<Conta | null>(null)
  const [demo, setDemo] = useState(false)
  const [tela, setTela] = useState('inicio')
  const [etapa, setEtapa] = useState<'identificar' | 'codigo'>('identificar')
  const [desafio, setDesafio] = useState('')
  const [busy, setBusy] = useState(false)
  const [erro, setErro] = useState('')
  const [mensagem, setMensagem] = useState('')
  const [filtro, setFiltro] = useState('aberto')
  const [imovel, setImovel] = useState('')
  const [acordos, setAcordos] = useState<Acordo[] | null>(null)
  const [erroAcordos, setErroAcordos] = useState('')
  const [guia, setGuia] = useState<{ url: string; mensagem: string } | null>(null)
  const titulo = useRef<HTMLHeadingElement>(null)
  useEffect(() => { const anterior = document.title; document.title = 'Portal do Cidadão · Franca'; return () => { document.title = anterior } }, [])
  useEffect(() => { requisicao<Config>('/config').then(setConfig).catch(() => setConfig({ identificacao_disponivel: false, emissao_disponivel: false })); requisicao<Conta>('/me').then(setConta).catch(() => { /* Não há sessão do cidadão: mostra entrada. */ }) }, [])
  useEffect(() => { titulo.current?.focus(); window.scrollTo(0, 0) }, [tela, conta, etapa])
  useEffect(() => {
    if (!conta || demo) return
    const duracao = Date.parse(conta.expira_em) - Date.now()
    const timer = window.setTimeout(() => { setConta(null); setErro('Sua sessão terminou. Confirme sua identidade para entrar novamente.'); setEtapa('identificar'); setDesafio('') }, Math.max(0, duracao))
    return () => clearTimeout(timer)
  }, [conta, demo])
  useEffect(() => {
    if (tela !== 'acordos' || !conta) return
    setErroAcordos('')
    if (demo) { setAcordos([{ id: 'demo-acordo', descricao: 'Acordo de demonstração · parcela 3 de 12', situacao: 'Em andamento', valor: 95, vencimento: '2026-12-10' }]); return }
    setAcordos(null); requisicao<{ acordos: Acordo[] }>('/acordos').then(r => setAcordos(r.acordos)).catch(e => setErroAcordos(e.message))
  }, [tela, conta, demo])
  const mudar = (v: string) => { setTela(v); setErro(''); setMensagem(''); setGuia(null) }
  async function entrar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const form = e.currentTarget; const fd = new FormData(form); setBusy(true); setErro(''); setMensagem('')
    try {
      if (etapa === 'identificar' && acessoTeste) {
        await requisicao('/acesso-teste', { method: 'POST', headers: { Authorization: 'Bearer ' + sessao!.token }, body: JSON.stringify({ cpf: fd.get('cpf'), nascimento: fd.get('nascimento'), nome_mae: fd.get('nome_mae') }) })
        form.reset(); setConta(await requisicao<Conta>('/me')); setDesafio(''); setMensagem(''); setTela('inicio')
      } else if (etapa === 'identificar') {
        const r = await requisicao<{ desafio: string; mensagem: string }>('/identificar', { method: 'POST', body: JSON.stringify({ cpf: fd.get('cpf'), nascimento: fd.get('nascimento'), nome_mae: fd.get('nome_mae') }) })
        form.reset(); setDesafio(r.desafio); setMensagem(r.mensagem); setEtapa('codigo')
      } else {
        await requisicao('/confirmar', { method: 'POST', body: JSON.stringify({ desafio, codigo: fd.get('codigo') }) })
        form.reset(); setConta(await requisicao<Conta>('/me')); setDesafio(''); setMensagem(''); setTela('inicio')
      }
    } catch (err) { setErro((err as Error).message) } finally { setBusy(false) }
  }
  async function sair() {
    if (!demo && conta) { try { await requisicao('/sair', { method: 'POST', headers: { 'X-Portal-CSRF': conta.csrf } }) } catch { setErro('Não foi possível encerrar a sessão no servidor. Tente novamente.'); return } }
    setConta(null); setDemo(false); setTela('inicio'); setEtapa('identificar'); setMensagem(''); setErro(''); setGuia(null); setAcordos(null)
  }
  async function emitir(tipo: 'lancamento' | 'acordo', id: string) {
    if (demo) { setMensagem('Esta é uma demonstração. No portal ativo, a guia será emitida pelo sistema tributário com os valores atualizados.'); return }
    setBusy(true); setErro(''); setGuia(null)
    try { setGuia(await requisicao('/guias', { method: 'POST', headers: { 'X-Portal-CSRF': conta!.csrf }, body: JSON.stringify({ tipo, id }) })) }
    catch (err) { setErro((err as Error).message) } finally { setBusy(false) }
  }
  function conhecer() { setDemo(true); setConta(structuredClone(exemplo)); setMensagem(''); setErro(''); setTela('inicio') }
  const parcelas = conta?.lancamentos.filter(p => (!imovel || p.imovel === imovel) && (filtro === 'todos' || (filtro === 'aberto' ? p.situacao !== 'pago' : p.situacao === filtro))) || []
  const cartaoParcela = (p: Parcela) => <article className="pc-parcela" key={p.id}><div className="pc-parcela-top"><span className={'pc-status ' + p.situacao}>{rotulos[p.situacao]}</span><span>{p.exercicio} · Parcela {p.parcela ?? 'única'}</span></div><h3>{p.tributo}</h3><p>{p.imovel ? `Inscrição / referência ${conta?.imoveis.find(i => i.id === p.imovel)?.inscricao || p.imovel}` : 'Lançamento municipal'}</p><div className="pc-valor"><strong>{moeda(p.valor)}</strong><span>Vencimento {data(p.vencimento)}</span></div>{p.situacao !== 'pago' && <button className="pc-btn pc-outline" disabled={busy} onClick={() => emitir('lancamento', p.id)}>Consultar guia atualizada <span aria-hidden="true">↗</span></button>}</article>
  return <div className="pc-portal"><a className="pc-skip" href="#pc-conteudo">Ir para o conteúdo</a><header className="pc-header"><Link to="/cidadao" className="pc-marca"><img src="/pmf-catavento.png" width="36" height="36" alt="Prefeitura de Franca" /><span><b>Franca</b><small>Portal do Cidadão</small></span></Link><span className="pc-seguro">◈ Ambiente de serviços municipais</span>{conta && <button className="pc-sair" onClick={sair}>{demo ? 'Sair da demonstração' : 'Sair com segurança'}</button>}</header>
    {conta?.modo_teste && <div className="pc-demo"><b>Modo de teste</b> · Acesso autorizado pelo administrador do SIGPMF, sem código de e-mail ou celular.</div>}
    {demo && <div className="pc-demo"><b>Demonstração</b> · Cadastro e valores fictícios. Nenhum pagamento é realizado.</div>}
    {conta && <nav className="pc-nav" aria-label="Serviços do cidadão">{Object.entries(nomes).map(([id, nome]) => <button key={id} aria-current={tela === id ? 'page' : undefined} onClick={() => mudar(id)}><span aria-hidden="true">{icones[id]}</span>{nome}</button>)}</nav>}
    <main id="pc-conteudo" className={conta ? 'pc-main' : 'pc-entrada'}>
      {!conta ? <><section className="pc-boasvindas"><span className="pc-eyebrow">PREFEITURA DE FRANCA</span><h1 ref={titulo} tabIndex={-1}>A cidade mais perto.<br /><em>Seu dia mais simples.</em></h1><p>Seu cadastro, seus imóveis, suas informações de saúde e educação e seus serviços municipais em um só lugar. Fácil de acessar, inclusive pelo celular.</p><div className="pc-servicos"><div><span>▦</span><b>IPTU e imóveis</b><p>Consulte parcelas e solicite a guia atualizada.</p></div><div><span>⇄</span><b>Parcelas e acordos</b><p>Acompanhe vencimentos e regularize pendências.</p></div><div><span>♡</span><b>Saúde</b><p>Informações de atendimentos, agendamentos e vacinas da rede municipal.</p></div><div><span>▣</span><b>Educação</b><p>Informações de matrículas e do vínculo com a rede municipal de ensino.</p></div><div><span>◎</span><b>Seus dados</b><p>Veja as informações do seu cadastro municipal.</p></div></div><button className="pc-link" onClick={conhecer}>Conhecer o portal com dados fictícios <span aria-hidden="true">→</span></button></section><section className="pc-login" aria-labelledby="pc-login-titulo"><div className="pc-passos">{acessoTeste ? <span className="ativo">Acesso de teste · administrador SIGPMF</span> : <><span className="ativo">1 · Identificação</span><span className={etapa === 'codigo' ? 'ativo' : ''}>2 · Confirmação</span></>}</div><h2 id="pc-login-titulo">{etapa === 'identificar' ? 'Acesse seu espaço' : 'Confirme que é você'}</h2><p>{etapa === 'identificar' ? 'Informe os dados como estão no seu cadastro municipal.' : 'Digite o código enviado ao e-mail já registrado na Prefeitura. Ele vale por 10 minutos.'}</p><form onSubmit={entrar} key={etapa}>
        {etapa === 'identificar' ? <><label>CPF<input name="cpf" inputMode="numeric" autoComplete="off" placeholder="000.000.000-00" required minLength={11} maxLength={14} /></label><label>Data de nascimento<input name="nascimento" type="date" autoComplete="bday" required max={new Date().toISOString().slice(0, 10)} /></label><label>Nome completo da mãe<input name="nome_mae" autoComplete="off" placeholder="Conforme o cadastro" required minLength={5} maxLength={200} /></label></> : <label>Código de confirmação<input name="codigo" className="pc-codigo" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} placeholder="000000" required /></label>}
        {erro && <p className="pc-alerta erro" role="alert">{erro}</p>}{mensagem && <p className="pc-alerta" role="status">{mensagem}</p>}
        <button className="pc-btn" disabled={busy}>{busy ? 'Aguarde…' : etapa === 'identificar' ? acessoTeste ? 'Entrar para testar, sem código →' : 'Continuar com meus dados →' : 'Entrar no meu espaço →'}</button>
        {etapa === 'codigo' && <button type="button" className="pc-link" onClick={() => { setEtapa('identificar'); setDesafio(''); setErro(''); setMensagem('') }}>Conferir dados ou solicitar novo código</button>}
      </form><div className="pc-privacidade"><b>{acessoTeste ? 'Teste sem envio de código' : 'Seus dados protegidos'}</b><p>{acessoTeste ? 'Sua sessão de administrador do SIGPMF autoriza o teste. CPF, nascimento e nome da mãe devem conferir com as bases internas; não é necessário e-mail ou celular cadastrado.' : 'Os dados cadastrais localizam seu registro. O código confirma o acesso pelo contato já cadastrado. Em computadores compartilhados, encerre a sessão ao terminar.'}</p></div>{config && !config.identificacao_disponivel && !acessoTeste && <p className="pc-disponibilidade">Acesso digital em preparação. Você já pode conhecer a demonstração do portal.</p>}</section></> : <>
        <div className="pc-titulo"><div><span className="pc-eyebrow">SEU ESPAÇO NA PREFEITURA</span><h1 ref={titulo} tabIndex={-1}>{tela === 'inicio' ? `Olá, ${conta.nome.split(' ')[0]}.` : nomes[tela]}</h1><p>{tela === 'inicio' ? 'O que você precisa resolver hoje?' : 'Informações do seu cadastro, com clareza e praticidade.'}</p></div><span className="pc-cpf">{conta.cpf}</span></div>
        {erro && <div className="pc-alerta erro" role="alert">{erro}</div>}{mensagem && <div className="pc-alerta" role="status">{mensagem}</div>}{guia && <div className="pc-guia" role="status"><h2>Sua guia está disponível</h2><p>{guia.mensagem}</p><a className="pc-btn" href={guia.url} target="_blank" rel="noopener noreferrer">Abrir guia oficial ↗</a></div>}
        {tela === 'inicio' && <><section className="pc-resumo" aria-label="Resumo financeiro"><article><span>Em aberto na base consultada</span><strong>{moeda(conta.totais.em_aberto)}</strong><button onClick={() => { setFiltro('aberto'); mudar('tributos') }}>Ver parcelas →</button></article><article className="atencao"><span>Parcelas vencidas</span><strong>{moeda(conta.totais.vencido)}</strong><button onClick={() => { setFiltro('vencido'); mudar('tributos') }}>Consultar atualização →</button></article><article><span>Imóveis cadastrados</span><strong>{conta.imoveis.length}</strong><button onClick={() => mudar('imoveis')}>Ver meus imóveis →</button></article></section><p className="pc-nota">{conta.aviso_valores}</p><h2 className="pc-secao-titulo">Resolva por aqui</h2><div className="pc-atalhos">{(['tributos', 'acordos', 'cadastro'] as const).map(id => <button key={id} onClick={() => mudar(id)}><span className="pc-atalho-icon">{icones[id]}</span><b>{nomes[id]}</b><small>{id === 'tributos' ? 'Guias, parcelas vencidas e pagamentos' : id === 'acordos' ? 'Parcelamentos e próximos vencimentos' : 'Informações que temos sobre você'}</small><span className="pc-seta">→</span></button>)}</div><div className="pc-secao-head"><h2>Próximas pendências</h2><button className="pc-link" onClick={() => mudar('tributos')}>Ver todas →</button></div><div className="pc-parcelas">{conta.lancamentos.filter(p => p.situacao !== 'pago').slice(0, 3).map(cartaoParcela)}</div>{!conta.lancamentos.some(p => p.situacao !== 'pago') && <div className="pc-vazio">Nenhuma parcela em aberto na base consultada.</div>}</>}
        {tela === 'tributos' && <><p className="pc-nota">{conta.aviso_valores}</p><div className="pc-filtros"><label>Mostrar<select value={filtro} onChange={e => setFiltro(e.target.value)}><option value="aberto">Em aberto</option><option value="vencido">Vencidas</option><option value="a_vencer">A vencer</option><option value="pago">Pagas</option><option value="todos">Todas</option></select></label><label>Imóvel<select value={imovel} onChange={e => setImovel(e.target.value)}><option value="">Todos os imóveis / lançamentos</option>{conta.imoveis.map(i => <option key={i.id} value={i.id}>{i.inscricao || i.id}</option>)}</select></label></div><div className="pc-parcelas">{parcelas.map(cartaoParcela)}</div>{!parcelas.length && <div className="pc-vazio">Nenhuma parcela encontrada para este filtro.</div>}{conta.lista_limitada && <p className="pc-nota">Exibindo até 300 lançamentos recentes. O resumo considera apenas esta lista; consulte o atendimento para o histórico completo.</p>}</>}
        {tela === 'acordos' && <><p className="pc-nota">A situação e o valor atualizado de cada acordo são consultados no sistema tributário. Emitir uma guia não cria um novo acordo.</p>{erroAcordos ? <div className="pc-vazio" role="status"><h2>Consulta de acordos indisponível</h2><p>{erroAcordos}</p></div> : acordos === null ? <p role="status">Consultando seus acordos…</p> : acordos.length ? <div className="pc-parcelas">{acordos.map(a => <article className="pc-parcela" key={a.id}><span className="pc-status a_vencer">{a.situacao}</span><h3>{a.descricao}</h3><div className="pc-valor"><strong>{moeda(a.valor)}</strong><span>Vencimento {data(a.vencimento)}</span></div><button className="pc-btn pc-outline" disabled={busy} onClick={() => emitir('acordo', a.id)}>Consultar guia do acordo ↗</button></article>)}</div> : <div className="pc-vazio">Nenhum acordo foi retornado pelo sistema tributário.</div>}</>}
        {tela === 'cadastro' && <><section className="pc-cadastro"><h2>Seus dados cadastrais</h2><dl><div><dt>Nome</dt><dd>{conta.nome}</dd></div><div><dt>CPF</dt><dd>{conta.cpf}</dd></div><div><dt>Nascimento</dt><dd>{data(conta.nascimento)}</dd></div>{conta.cadastro.map((d, i) => <div key={i}><dt>{{ endereco: 'Endereço', email: 'E-mail', telefone: 'Telefone' }[d.campo] || d.campo}</dt><dd>{d.valor || 'Não informado'}<small>{d.fonte} · atualização {data(d.atualizado_em)}</small></dd></div>)}</dl></section><div className="pc-alerta"><b>Encontrou alguma informação incorreta?</b><p>Solicite a atualização no atendimento municipal. A alteração precisa ser conferida antes de substituir o registro oficial.</p></div></>}
        {tela === 'imoveis' && <div className="pc-parcelas">{conta.imoveis.length ? conta.imoveis.map(i => <article className="pc-parcela" key={i.id}><span className="pc-atalho-icon">▦</span><h2>{i.descricao}</h2><p>Inscrição municipal <b>{i.inscricao || i.id}</b></p><button className="pc-btn pc-outline" onClick={() => { setImovel(i.id); setFiltro('aberto'); mudar('tributos') }}>Ver IPTU e parcelas →</button></article>) : <div className="pc-vazio">Nenhum imóvel com vínculo atual foi encontrado na base consultada.</div>}</div>}
      </>}
    </main><footer className="pc-footer"><span><b>Prefeitura de Franca</b> · Serviços ao cidadão</span><span>Ao terminar, saia da sua conta para proteger seus dados.</span></footer>
  </div>
}
