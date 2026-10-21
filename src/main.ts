import "./style.css";

import { startHealthPolling } from "./api.ts";
import { startRouter } from "./app/router.ts";
import { mountShell } from "./app/shell.ts";

const root = document.querySelector<HTMLElement>("#app");
if (root === null) throw new Error("missing #app root element");

const shell = mountShell(root);
startRouter(shell.main);
startHealthPolling();
