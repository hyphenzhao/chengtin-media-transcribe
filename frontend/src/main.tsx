import { createRoot } from "react-dom/client";
import App from "./App";
import Dialogs from "./Dialogs";
import "./styles.css";
import "./mobile.css";
createRoot(document.getElementById("root")!).render(
  <>
    <App />
    <Dialogs />
  </>,
);
