import {
  applyProject,
  deleteProjectOnServer,
  listProjectsOnServer,
  openProjectFromServer,
  saveProjectOnServer,
} from "../analysis/project.ts";
import type { ProjectSummary } from "../analysis/project.ts";
import { reloadPage } from "../app/router.ts";
import { el } from "../ui/dom.ts";
import { messageOf } from "../ui/text.ts";

function savedAtText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Saves and restores the document (moves, transcript, cast, sounds, options); the clip is re-opened by hand. */
export function createProjectsPanel(): HTMLElement {  const nameInput = el("input", {
    class: "text-input",
    attrs: { type: "text", placeholder: "project name", "aria-label": "Project name" },
  });
  const saveButton = el("button", { class: "button button-primary", text: "Save project", attrs: { type: "button" } });
  const refreshButton = el("button", { class: "button", text: "Refresh", attrs: { type: "button" } });
  const status = el("p", { class: "status-line" });
  const list = el("ol", { class: "move-list" });

  const element = el("section", {
    class: "analysis",
    children: [
      el("h2", { class: "section-title", text: "Projects" }),
      el("p", {
        class: "page-hint",
        text: "Saved on the backend in .cache/projects. The video file is not stored: open the same clip again after loading.",
      }),
      el("div", { class: "toolbar", children: [nameInput, saveButton, refreshButton] }),
      status,
      list,
    ],
  });

  function render(projects: ProjectSummary[]): void {
    list.replaceChildren(
      ...projects.map((project) => {
        const open = el("button", { class: "button button-small", text: "Open", attrs: { type: "button" } });
        const remove = el("button", {
          class: "button button-small",
          text: "✕",
          attrs: { type: "button", "aria-label": `Delete ${project.name}` },
        });

        open.addEventListener("click", () => {
          open.disabled = true;
          void openProjectFromServer(project.id)
            .then((data) => {
              applyProject(data);
              reloadPage();
            })
            .catch((error: unknown) => {
              status.textContent = messageOf(error);
            })
            .finally(() => {
              open.disabled = false;
            });
        });

        remove.addEventListener("click", () => {
          void deleteProjectOnServer(project.id)
            .then(() => {
              status.textContent = "Project deleted.";
              return listProjectsOnServer();
            })
            .then(render)
            .catch((error: unknown) => {
              status.textContent = messageOf(error);
            });
        });

        return el("li", {
          class: "move-row",
          children: [
            el("span", { class: "move-title", text: project.name }),
            el("span", { class: "meta", text: savedAtText(project.savedAt) }),
            open,
            remove,
          ],
        });
      }),
    );
    list.hidden = projects.length === 0;
  }

  function refresh(): void {
    void listProjectsOnServer()
      .then(render)
      .catch((error: unknown) => {
        status.textContent = messageOf(error);
      });
  }

  saveButton.addEventListener("click", () => {
    saveButton.disabled = true;
    void saveProjectOnServer(nameInput.value.trim() === "" ? "Untitled" : nameInput.value.trim())
      .then((summary) => {
        status.textContent = `Saved as "${summary.name}".`;
        return listProjectsOnServer();
      })
      .then(render)
      .catch((error: unknown) => {
        status.textContent = messageOf(error);
      })
      .finally(() => {
        saveButton.disabled = false;
      });
  });

  refreshButton.addEventListener("click", () => {
    refresh();
  });

  refresh();
  return element;
}
