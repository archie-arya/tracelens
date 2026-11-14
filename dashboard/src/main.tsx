import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, HashRouter, Route, Routes } from "react-router-dom";
import { STATIC_BUILD } from "./api";
import { Layout } from "./components/Layout";
import "./index.css";
import Alerts from "./pages/Alerts";
import Datasets, { DatasetDetail } from "./pages/Datasets";
import Evals from "./pages/Evals";
import Optimizer, { OptimizerRun } from "./pages/Optimizer";
import Overview from "./pages/Overview";
import Sessions from "./pages/Sessions";
import SettingsPage from "./pages/Settings";
import TraceDetail from "./pages/TraceDetail";
import Traces from "./pages/Traces";
import { AppProvider } from "./state";

const Router = STATIC_BUILD ? HashRouter : BrowserRouter;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Router>
      <AppProvider>
        <Layout>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/traces" element={<Traces />} />
            <Route path="/traces/:id" element={<TraceDetail />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/evals" element={<Evals />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/datasets/:id" element={<DatasetDetail />} />
            <Route path="/optimizer" element={<Optimizer />} />
            <Route path="/optimizer/:id" element={<OptimizerRun />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </Layout>
      </AppProvider>
    </Router>
  </React.StrictMode>
);
