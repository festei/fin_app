import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Alerts } from "./pages/Alerts";
import { Opportunities } from "./pages/Opportunities";
import { PortfolioPage } from "./pages/Portfolio";
import { Sandbox } from "./pages/Sandbox";
import { SecurityPage } from "./pages/Security";
import { Settings } from "./pages/Settings";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Opportunities />} />
        <Route path="security/:ticker" element={<SecurityPage />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="sandbox" element={<Sandbox />} />
        <Route path="sandbox/:id" element={<PortfolioPage />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<p className="empty">Page not found.</p>} />
      </Route>
    </Routes>
  );
}
