import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import { installPersonaFetch } from "./studio/persona";

// Luna ou Cleuza: todo pedido ao servidor leva a persona escolhida (10/11)
installPersonaFetch();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
