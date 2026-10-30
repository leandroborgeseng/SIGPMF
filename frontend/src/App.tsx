import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from './auth'
import Layout from './Layout'
import AcessoSecretaria from './pages/AcessoSecretaria'
import Cargas from './pages/Cargas'
import Conectores from './pages/Conectores'
import Dashboard from './pages/Dashboard'
import ExplorarDados from './pages/ExplorarDados'
import Login from './pages/Login'
import Simulador from './pages/Simulador'
import Politicas from './pages/Politicas'
import Vinculos from './pages/Vinculos'
import { MonitorCargas, PainelFazenda, PainelGeral, PainelSaude } from './paineis/Paineis'

function Protegido({ children }: { children: ReactNode }) {
  const { sessao } = useAuth()
  const loc = useLocation()
  if (!sessao) return <Navigate to="/login" replace state={{ de: loc.pathname }} />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protegido><Layout /></Protegido>}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/conectores" element={<Conectores />} />
        <Route path="/cargas" element={<Cargas />} />
        <Route path="/simulador" element={<Simulador />} />
        <Route path="/explorar" element={<ExplorarDados />} />
        <Route path="/painel" element={<PainelGeral />} />
        <Route path="/painel/saude" element={<PainelSaude />} />
        <Route path="/painel/fazenda" element={<PainelFazenda />} />
        <Route path="/monitor" element={<MonitorCargas />} />
        <Route path="/vinculos" element={<Vinculos />} />
        <Route path="/politicas" element={<Politicas />} />
        <Route path="/ti/acesso-secretaria" element={<AcessoSecretaria />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}
