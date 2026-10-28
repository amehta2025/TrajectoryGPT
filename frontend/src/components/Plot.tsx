// react-plotly.js bound to the minified Plotly bundle, plus a shared dark layout.
import createPlotlyComponent from 'react-plotly.js/factory'
import Plotly from 'plotly.js-dist-min'
import type { Layout } from 'plotly.js'

export const Plot = createPlotlyComponent(Plotly)

export const baseLayout: Partial<Layout> = {
  autosize: true,
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { family: 'Inter, system-ui, sans-serif', size: 11, color: '#c9cfdb' },
  margin: { l: 60, r: 20, t: 10, b: 50 },
  hoverlabel: { bgcolor: '#151a24', bordercolor: '#2b3242', font: { color: '#e8ecf3' } },
}

export const axisStyle = { gridcolor: '#1f2533', zerolinecolor: '#2b3242', linecolor: '#2b3242' }
