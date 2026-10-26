import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './auth'
import { ErrorBoundary } from './componentes'

const GRUPOS = [
  { titulo: 'Visão', itens: [{ to: '/dashboard', rotulo: 'Dashboard', curto: 'Dashboard' }] },
  { titulo: 'Dados', itens: [
    { to: '/conectores', rotulo: 'Conectores', curto: 'Conectores' },
    { to: '/simulador', rotulo: 'Simulador do Cidadão', curto: 'Simulador' },
  ] },
]
const ITENS = GRUPOS.flatMap((g) => g.itens)

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
          {GRUPOS.map((g) => (
            <div key={g.titulo} className="grupo-nav">
              <div className="grupo-titulo" aria-hidden="true">{g.titulo}</div>
              <ul>
                {g.itens.map((i) => (
                  <li key={i.to}>
                    <NavLink to={i.to} onClick={() => fechar()}>{i.rotulo}</NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="rodape-lateral">
          <div className="usuario-nome">{sessao?.nome}</div>
          <div className="perfil">Perfil: {sessao?.perfil}</div>
          <button className="btn secundario" onClick={sair}>Sair</button>
        </div>
      </aside>
      <main className="conteudo" id="conteudo">
        <ErrorBoundary chave={local.pathname} titulo="Não foi possível exibir esta tela.">
          <Outlet />
        </ErrorBoundary>
      </main>
      <nav className="nav-inferior" aria-label="Atalhos">
        {ITENS.map((i) => <NavLink key={i.to} to={i.to}>{i.curto}</NavLink>)}
      </nav>
    </div>
  )
}
