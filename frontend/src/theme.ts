// Shared colours. Colour is used only for meaning:
// - domains get fixed hues so the heatmap axes and the 3D cartoon agree;
// - lost vs gained contact is orange vs blue (safe for red-green colour blindness);
// - noise is grey. Nothing else is coloured.
export const DOMAIN_COLORS: Record<string, string> = {
  NMP: '#d9a441',
  LID: '#a08ff0',
  CORE: '#8a93a6',
}
export const CHANGE_COLORS = { lost: '#f0874f', gained: '#5aa5f0' }
export const PAIR_COLORS = { a: '#ff6fa3', b: '#ffe14d' }
export const EXCLUDED_GREY = '#8b909c' // light enough to stand apart from Viridis' dark-purple zero
