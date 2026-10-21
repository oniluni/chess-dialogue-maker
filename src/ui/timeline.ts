import { clampRange, MIN_RANGE_SECONDS } from "../media/range.ts";
import type { TimeRange } from "../media/range.ts";
import { el } from "./dom.ts";
import { formatClock } from "./text.ts";

export interface TimelineOptions {
  duration: number;
  range: TimeRange;
  onChange(range: TimeRange): void;
  onSeek(time: number): void;
}

export interface Timeline {
  element: HTMLElement;
  setRange(range: TimeRange): void;
  setPlayhead(time: number): void;
}

/** One strip: the kept range with two draggable edges, a playhead and a click-to-seek track. */
export function createTimeline(options: TimelineOptions): Timeline {
  const { duration } = options;
  const span = Math.max(duration, MIN_RANGE_SECONDS);
  let range = clampRange(options.range, duration);

  const selection = el("div", { class: "timeline-selection" });
  const startHandle = el("button", {
    class: "timeline-handle is-start",
    attrs: { type: "button", "aria-label": "Selection start" },
  });
  const endHandle = el("button", {
    class: "timeline-handle is-end",
    attrs: { type: "button", "aria-label": "Selection end" },
  });
  const playhead = el("div", { class: "timeline-playhead" });
  const track = el("div", {
    class: "timeline-track",
    children: [selection, startHandle, endHandle, playhead],
  });
  const element = el("div", {
    class: "timeline",
    children: [
      track,
      el("div", {
        class: "timeline-scale",
        children: [el("span", { text: "0:00" }), el("span", { text: formatClock(duration) })],
      }),
    ],
  });

  function percent(time: number): string {
    return `${(Math.min(Math.max(0, time), span) / span) * 100}%`;
  }

  function render(): void {
    selection.style.left = percent(range.start);
    selection.style.width = `${((range.end - range.start) / span) * 100}%`;
    startHandle.style.left = percent(range.start);
    endHandle.style.left = percent(range.end);
  }

  function timeAt(clientX: number): number {
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    const fraction = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return fraction * duration;
  }

  function apply(next: TimeRange): void {
    range = clampRange(next, duration);
    render();
    options.onChange({ ...range });
  }

  function bindHandle(handle: HTMLElement, edge: "start" | "end"): void {
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
      const move = (moveEvent: PointerEvent): void => {
        const time = timeAt(moveEvent.clientX);
        apply(edge === "start" ? { start: time, end: range.end } : { start: range.start, end: time });
      };
      const stop = (): void => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });
  }

  bindHandle(startHandle, "start");
  bindHandle(endHandle, "end");

  track.addEventListener("pointerdown", (event) => {
    if (event.target !== track && event.target !== selection) return;
    track.setPointerCapture(event.pointerId);
    options.onSeek(timeAt(event.clientX));
    const move = (moveEvent: PointerEvent): void => options.onSeek(timeAt(moveEvent.clientX));
    const stop = (): void => {
      track.removeEventListener("pointermove", move);
      track.removeEventListener("pointerup", stop);
      track.removeEventListener("pointercancel", stop);
    };
    track.addEventListener("pointermove", move);
    track.addEventListener("pointerup", stop);
    track.addEventListener("pointercancel", stop);
  });

  render();

  return {
    element,
    setRange(next) {
      range = clampRange(next, duration);
      render();
    },
    setPlayhead(time) {
      playhead.style.left = percent(time);
    },
  };
}
