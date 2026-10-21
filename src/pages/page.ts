export interface Page {
  element: HTMLElement;
  /** Called before the page leaves the screen: release media, timers and object URLs here. */
  dispose?(): void;
}
