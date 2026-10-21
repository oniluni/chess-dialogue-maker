import { el } from "../ui/dom.ts";
import { errorMessage, isRecord } from "../net/http.ts";
import { formatTimestamp, messageOf } from "../ui/text.ts";

interface SearchHit {
  id: string;
  title: string;
  channel: string;
  duration: number | null;
  url: string;
}

function hitOf(raw: unknown): SearchHit | null {
  if (!isRecord(raw)) return null;
  const title = raw["title"];
  const url = raw["url"];
  const channel = raw["channel"];
  const duration = raw["duration"];
  if (typeof title !== "string" || typeof url !== "string") return null;
  return {
    id: typeof raw["id"] === "string" ? raw["id"] : url,
    title,
    channel: typeof channel === "string" ? channel : "",
    duration: typeof duration === "number" ? duration : null,
    url,
  };
}

/** YouTube search through the backend; the picked link lands in the URL field. */
export function createSearchPanel(onPick: (url: string) => void): HTMLElement {
  const query = el("input", {
    class: "text-input",
    attrs: { type: "text", placeholder: "chess blunder or @handle words", "aria-label": "Search query" },
  });
  const searchButton = el("button", { class: "button", text: "Search", attrs: { type: "button" } });
  const cookies = el("textarea", {
    class: "text-input",
    attrs: { rows: "3", placeholder: "Netscape cookies.txt (optional)", "aria-label": "Cookies" },
  });
  const saveCookiesButton = el("button", { class: "button", text: "Save cookies", attrs: { type: "button" } });
  const updateButton = el("button", { class: "button", text: "Update yt-dlp", attrs: { type: "button" } });
  const status = el("p", { class: "status-line" });
  const results = el("ol", { class: "move-list" });

  const element = el("section", {
    class: "analysis",
    children: [
      el("h2", { class: "section-title", text: "Find the source" }),
      el("p", {
        class: "page-hint",
        text: "Searches YouTube through yt-dlp; @handle plus words searches inside that channel. Pick a result to put its link in the URL field.",
      }),
      el("div", { class: "toolbar", children: [query, searchButton] }),
      status,
      results,
      el("details", {
        children: [
          el("summary", { text: "Cookies and yt-dlp" }),
          cookies,
          el("div", { class: "toolbar", children: [saveCookiesButton, updateButton] }),
        ],
      }),
    ],
  });

  searchButton.addEventListener("click", () => {
    void (async (): Promise<void> => {
      searchButton.disabled = true;
      status.textContent = "Searching…";
      try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query.value.trim())}`);
        if (!response.ok) throw new Error(await errorMessage(response));
        const data: unknown = await response.json();
        const hits = isRecord(data) && Array.isArray(data["hits"]) ? data["hits"].flatMap((raw) => hitOf(raw) ?? []) : [];
        results.replaceChildren(
          ...hits.map((hit) => {
            const use = el("button", { class: "button button-small", text: "Use", attrs: { type: "button" } });
            use.addEventListener("click", () => {
              onPick(hit.url);
              status.textContent = "Link is in the URL field.";
            });
            return el("li", {
              class: "move-row",
              children: [
                el("span", { class: "move-title", text: hit.title }),
                el("span", { class: "meta", text: `${hit.channel}${hit.duration === null ? "" : ` · ${formatTimestamp(hit.duration)}`}` }),
                use,
              ],
            });
          }),
        );
        status.textContent = hits.length === 0 ? "Nothing found." : `${hits.length} results.`;
      } catch (error) {
        status.textContent = messageOf(error);
      } finally {
        searchButton.disabled = false;
      }
    })();
  });

  saveCookiesButton.addEventListener("click", () => {
    void (async (): Promise<void> => {
      try {
        const response = await fetch("/api/download/cookies", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text: cookies.value }),
        });
        if (!response.ok) throw new Error(await errorMessage(response));
        status.textContent = "Cookies saved for yt-dlp.";
      } catch (error) {
        status.textContent = messageOf(error);
      }
    })();
  });

  updateButton.addEventListener("click", () => {
    void (async (): Promise<void> => {
      updateButton.disabled = true;
      status.textContent = "Updating yt-dlp…";
      try {
        const response = await fetch("/api/download/update", { method: "POST" });
        if (!response.ok) throw new Error(await errorMessage(response));
        const data: unknown = await response.json();
        const output = isRecord(data) && typeof data["output"] === "string" ? data["output"] : "";
        status.textContent = output.trim().split(/\r?\n/).slice(-1)[0] ?? "The update finished.";
      } catch (error) {
        status.textContent = messageOf(error);
      } finally {
        updateButton.disabled = false;
      }
    })();
  });

  return element;
}
