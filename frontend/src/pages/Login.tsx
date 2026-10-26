import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'

export default function Login() {
  const { sessao, entrar } = useAuth()
  const [usuario, setUsuario] = useState('')
  const [senha, setSenha] = useState('')
  const [erro, setErro] = useState('')
  const loc = useLocation()
  if (sessao) return <Navigate to={(loc.state as { de?: string })?.de ?? '/dashboard'} replace />
  const enviar = async (e: FormEvent) => {
    e.preventDefault()
    setErro('')
    try {
      await entrar(usuario, senha)
    } catch {
      setErro('Usuário ou senha inválidos.')
    }
  }
  return (
    <main className="login">
      <div className="login-lado">
      <form onSubmit={enviar} aria-labelledby="titulo-login">
        <div className="marca">
          <img src="/pmf-catavento.png" alt="Prefeitura de Franca" width="40" height="40" />
          <span className="marca-texto"><strong>SIGPMF</strong><small>Plataforma de Dados da Prefeitura</small></span>
        </div>
        <h1 id="titulo-login">Entrar no SIGPMF</h1>
        <p className="sutil">Use suas credenciais institucionais.</p>
        <label htmlFor="usuario">Usuário (e-mail)</label>
        <input id="usuario" autoComplete="username" value={usuario} onChange={(e) => setUsuario(e.target.value)} required />
        <label htmlFor="senha">Senha</label>
        <input id="senha" type="password" autoComplete="current-password" value={senha} onChange={(e) => setSenha(e.target.value)} required />
        <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
        <button className="btn" type="submit">Entrar</button>
      </form>
      </div>
      <aside className="login-painel" aria-hidden="true">
        <p className="kicker-claro">Prefeitura Municipal de Franca/SP</p>
        <p className="login-chamada">Dados dos sistemas municipais integrados em um só lugar.</p>
        <p>Conectores, camadas bronze, prata e ouro e o cadastro único do cidadão.</p>
      </aside>
    </main>
  )
}
