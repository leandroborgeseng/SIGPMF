import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './auth'

const ITENS = [
  { to: '/dashboard', rotulo: 'Dashboard' },
  { to: '/conectores', rotulo: 'Conectores' },
  { to: '/simulador', rotulo: 'Simulador do Cidadão' },
]

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
        <span className="marca">SIGPMF</span>
      </header>
      {aberto && <div className="veu" data-testid="veu" onClick={() => fechar()} />}
      <aside id="menu-lateral" className={`lateral${aberto ? ' aberto' : ''}`}>
        <div className="marca lateral-marca">SIGPMF</div>
        <nav aria-label="Navegação principal">
          <ul>
            {ITENS.map((i) => (
              <li key={i.to}>
                <NavLink to={i.to} onClick={() => fechar()}>{i.rotulo}</NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="rodape-lateral">
          <div>{sessao?.nome}</div>
          <div className="perfil">Perfil: {sessao?.perfil}</div>
          <button className="btn secundario" onClick={sair}>Sair</button>
        </div>
      </aside>
      <main className="conteudo" id="conteudo">
        <Outlet />
      </main>
    </div>
  )
}
