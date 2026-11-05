import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./style.css";
import { initTheme } from "./theme";

initTheme();

createRoot(document.getElementById("root")!).render(<App />);
