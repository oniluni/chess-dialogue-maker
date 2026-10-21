type Child = Node | string | null | undefined | false;

export interface ElOptions {
  class?: string;
  text?: string;
  attrs?: Record<string, string>;
  children?: Child[];
}

/** Typed `createElement`: text is set as text, never parsed as HTML. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  options: ElOptions = {},
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.class !== undefined) node.className = options.class;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attrs ?? {})) {
    node.setAttribute(name, value);
  }
  for (const child of options.children ?? []) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}
