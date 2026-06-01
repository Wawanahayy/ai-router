import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import Dashboard from './pages/Dashboard'
import Providers from './pages/Providers'
import Keys from './pages/Keys'
import LocalKeys from './pages/LocalKeys'
import Combos from './pages/Combos'
import Logs from './pages/Logs'
import Settings from './pages/Settings'
import Login from './pages/Login'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<App />}>
          <Route index element={<Dashboard />} />
          <Route path="providers" element={<Providers />} />
          <Route path="keys" element={<Keys />} />
          <Route path="keys/:providerId" element={<Keys />} />
          <Route path="api-keys" element={<LocalKeys />} />
          <Route path="combos" element={<Combos />} />
          <Route path="logs" element={<Logs />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </HashRouter>
  </React.StrictMode>
)
