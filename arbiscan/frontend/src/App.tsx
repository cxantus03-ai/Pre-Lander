import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import LiveArbitrage from "./pages/LiveArbitrage";
import MarketExplorer from "./pages/MarketExplorer";
import OpportunityDetails from "./pages/OpportunityDetails";
import CalculatorPage from "./pages/CalculatorPage";
import CapitalPage from "./pages/CapitalPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import AlertsPage from "./pages/AlertsPage";
import SettingsPage from "./pages/SettingsPage";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/live" element={<LiveArbitrage />} />
        <Route path="/markets" element={<MarketExplorer />} />
        <Route path="/opportunity" element={<OpportunityDetails />} />
        <Route path="/opportunity/:id" element={<OpportunityDetails />} />
        <Route path="/calculator" element={<CalculatorPage />} />
        <Route path="/capital" element={<CapitalPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<div className="text-mute text-sm">Page not found.</div>} />
      </Routes>
    </Layout>
  );
}
