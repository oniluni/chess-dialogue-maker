import { readFileSync, writeFileSync } from "node:fs";

const path = "src/pages/sounds.ts";
let text = readFileSync(path, "utf8");
const broken = text.match(/text: "[^"]*", attrs: \{ type: "button" \} \}\);/g) ?? [];
for (const line of broken) {
  if (!line.includes("button button-small")) continue;
}
text = text.replace(/el\("button", \{ class: "button button-small", text: "[^"]*"/, 'el("button", { class: "button button-small", text: "\u2715"');
writeFileSync(path, text, "utf8");
console.log("glyph restored");
