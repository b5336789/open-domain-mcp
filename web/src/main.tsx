import React from "react";
import ReactDOM from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";
import { ToastProvider } from "./components/ui";
import App from "./App";
import CommandCenter from "./pages/CommandCenter";
import SourceIntake from "./pages/SourceIntake";
import Explore from "./pages/Explore";
import Ask from "./pages/Ask";
import Browse from "./pages/Browse";
import Articles from "./pages/Articles";
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
      { index: true, element: <CommandCenter /> },
      { path: "intake", element: <SourceIntake /> },
      { path: "ingest", element: <SourceIntake /> },
      { path: "explore", element: <Explore /> },
      { path: "ask", element: <Ask /> },
      { path: "browse", element: <Browse /> },
      { path: "articles", element: <Articles /> },
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
