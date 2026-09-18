import type { Unit } from "./types";

// Real DMG carts are nearly square; the label sticker (where the game image
// mounts) dominates the front face.
export function cartMetrics(u: Unit, width: number) {
  const labelWidth = width - u(24);
  const labelHeight = labelWidth * 0.85;
  // paddings + grip ridges above the label + contact strip below it
  return { labelWidth, labelHeight, height: labelHeight + u(58) };
}

// Fixed-size pieces so the panel stack height is known up front - portrait
// uses it to park the ejected cartridge low enough to leave room above.
export function modMetrics(u: Unit) {
  const pad = u(10);
  const nameH = u(18); // name row, flanked by the scroll arrows
  const descH = u(24); // two lines of description
  const btnH = u(22); // YES / NO row
  const configH = u(22); // per-mod CONFIG button below the YES / NO row
  const gap = u(6);
  const stackGap = u(8); // between the browser panel and the count panel
  const countH = u(24);
  const settingsH = u(26);
  const panelH = pad * 2 + nameH + gap + descH + gap + btnH + gap + configH;
  return {
    pad,
    nameH,
    descH,
    btnH,
    configH,
    gap,
    stackGap,
    countH,
    settingsH,
    height: panelH + stackGap + countH + stackGap + settingsH,
  };
}
