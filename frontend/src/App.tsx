import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from './auth'
import Layout from './Layout'
import Conectores from './pages/Conectores'
import Dashboard from './pages/Dashboard'
import ExplorarDados from './pages/ExplorarDados'
import Login from './pages/Login'
import Simulador from './pages/Simulador'
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
        <Route path="/simulador" element={<Simulador />} />
        <Route path="/explorar" element={<ExplorarDados />} />
        <Route path="/painel" element={<PainelGeral />} />
        <Route path="/painel/saude" element={<PainelSaude />} />
        <Route path="/painel/fazenda" element={<PainelFazenda />} />
        <Route path="/monitor" element={<MonitorCargas />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}
