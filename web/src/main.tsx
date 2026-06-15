import React from "react";
import ReactDOM from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import Dashboard from "./pages/Dashboard";
import Ingest from "./pages/Ingest";
import Explore from "./pages/Explore";
import Ask from "./pages/Ask";
import Browse from "./pages/Browse";
import Settings from "./pages/Settings";
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
      { path: "settings", element: <Settings /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
