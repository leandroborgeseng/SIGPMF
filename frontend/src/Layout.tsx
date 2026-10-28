import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './auth'
import { ErrorBoundary } from './componentes'
import { PERFIS_EXPLORADOR } from './explorar'

const GRUPOS: { titulo: string; itens: Item[] }[] = [
  { titulo: 'Gestão', itens: [
    { to: '/painel', rotulo: 'Visão geral', curto: 'Painel' },
    { to: '/painel/saude', rotulo: 'Saúde', curto: 'Saúde' },
    { to: '/painel/fazenda', rotulo: 'Fazenda', curto: 'Fazenda' },
  ] },
  { titulo: 'Dados', itens: [
    { to: '/dashboard', rotulo: 'Dashboard das cargas', curto: 'Dashboard' },
    { to: '/monitor', rotulo: 'Monitor de cargas', curto: 'Monitor' },
    { to: '/conectores', rotulo: 'Conectores', curto: 'Conectores' },
    { to: '/simulador', rotulo: 'Simulador do Cidadão', curto: 'Simulador' },
    { to: '/explorar', rotulo: 'Explorar dados', curto: 'Explorar', perfis: PERFIS_EXPLORADOR },
    { to: '/vinculos', rotulo: 'Explorador de vínculos', curto: 'Vínculos', perfis: ['admin', 'admin_lgpd'] },
    { to: '/politicas', rotulo: 'Políticas de dados', curto: 'Políticas' },
  ] },
]
// Barra inferior do celular: só os atalhos principais (o menu lateral tem todos)
const ATALHOS = ['/painel', '/painel/saude', '/painel/fazenda', '/simulador']
type Item = { to: string; rotulo: string; curto: string; perfis?: string[] }
const visivel = (i: Item, perfil?: string) => !i.perfis || (!!perfil && i.perfis.includes(perfil))

function Marca() {
  return (
    <div className="marca">
      <img src="/pmf-catavento.png" alt="Prefeitura de Franca" width="34" height="34" />
      <span className="marca-texto"><strong>SIGPMF</strong><small>Plataforma de Dados</small></span>
    </div>
  )
}

export default function Layout() {
  const { sessao, sair } = useAuth()
  const [aberto, setAberto] = useState(false)
  const botao = useRef<HTMLButtonElement>(null)
  const local = useLocation()
  const grupos = GRUPOS.map((g) => ({ ...g, itens: g.itens.filter((i) => visivel(i, sessao?.perfil)) }))
  const ITENS = grupos.flatMap((g) => g.itens)

  const fechar = (devolveFoco = true) => {
    setAberto(false)
    if (devolveFoco) botao.current?.focus()
  }
  useEffect(() => { setAberto(false) }, [local.pathname])
  useEffect(() => {
    if (!aberto) return
    const f = (e: KeyboardEvent) => { if (e.key === 'Escape') fechar() }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [aberto])

  return (
    <div className="app">
      <header className="topo">
        <button ref={botao} className="menu-btn" aria-label="Abrir menu" aria-expanded={aberto}
          aria-controls="menu-lateral" onClick={() => setAberto(!aberto)}>☰</button>
        <Marca />
        <span className="topo-usuario">{sessao?.nome}</span>
      </header>
      {aberto && <div className="veu" data-testid="veu" onClick={() => fechar()} />}
      <aside id="menu-lateral" className={`lateral${aberto ? ' aberto' : ''}`}>
        <div className="lateral-marca"><Marca /></div>
        <nav aria-label="Navegação principal">
          {grupos.map((g) => (
            <div key={g.titulo} className="grupo-nav">
              <div className="grupo-titulo" aria-hidden="true">{g.titulo}</div>
              <ul>
                {g.itens.map((i) => (
                  <li key={i.to}>
                    <NavLink to={i.to} end onClick={() => fechar()}>{i.rotulo}</NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="rodape-lateral">
          <div className="usuario-nome">{sessao?.nome}</div>
          <div className="perfil">Perfil: {sessao?.perfil}</div>
          <div className="perfil" title="Versão publicada">Build {__VERSAO__}</div>
          <button className="btn secundario" onClick={sair}>Sair</button>
        </div>
      </aside>
      <main className="conteudo" id="conteudo">
        <ErrorBoundary chave={local.pathname} titulo="Não foi possível exibir esta tela.">
          <Outlet />
        </ErrorBoundary>
      </main>
      <nav className="nav-inferior" aria-label="Atalhos">
        {ITENS.filter((i) => ATALHOS.includes(i.to)).map((i) => <NavLink key={i.to} to={i.to} end>{i.curto}</NavLink>)}
      </nav>
    </div>
  )
}
