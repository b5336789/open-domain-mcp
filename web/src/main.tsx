import React from "react";
import ReactDOM from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";
import { ToastProvider } from "./components/ui";
import App from "./App";
import Dashboard from "./pages/Dashboard";
import Ingest from "./pages/Ingest";
import Explore from "./pages/Explore";
import Ask from "./pages/Ask";
import Browse from "./pages/Browse";
import Review from "./pages/Review";
import McpBuilder from "./pages/McpBuilder";
import Simulator from "./pages/Simulator";
import Settings from "./pages/Settings";
import Graph from "./pages/Graph";
import Advisor from "./pages/Advisor";
import Metrics from "./pages/Metrics";
import "./index.css";

const router = createHashRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "ingest", element: <Ingest /> },
      { path: "explore", element: <Explore /> },
      { path: "ask", element: <Ask /> },
      { path: "browse", element: <Browse /> },
      { path: "review", element: <Review /> },
      { path: "graph", element: <Graph /> },
      { path: "advisor", element: <Advisor /> },
      { path: "mcp", element: <McpBuilder /> },
      { path: "simulator", element: <Simulator /> },
      { path: "metrics", element: <Metrics /> },
      { path: "settings", element: <Settings /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider>
  </React.StrictMode>
);
