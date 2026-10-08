# C05 algorithm flowcharts

The two independent parallel flows correspond to the model and notation in [c05_model_algorithm.tex](../c05_model_algorithm.tex) and the [compiled algorithms](../c05_model_algorithm.pdf). The upper flow is an external asynchronous LLM selector; the lower flow continues population search and installs only validated choices. Numerical experimental parameters are documented separately in [ALGORITHM.md](../ALGORITHM.md).

| Language | Editable draw.io | Vector PDF | SVG | PNG preview |
|---|---|---|---|---|
| English | [source](c05-flowchart.drawio) | [PDF](c05-flowchart.drawio.pdf) | [SVG](c05-flowchart.drawio.svg) | [PNG](c05-flowchart.png) |
| 中文 | [源文件](c05-flowchart-zh.drawio) | [PDF](c05-flowchart-zh.drawio.pdf) | [SVG](c05-flowchart-zh.drawio.svg) | [PNG](c05-flowchart-zh.png) |

All algorithm nodes, decision diamonds, connectors, mathematical labels, and the LLM brain/network symbol are editable draw.io vector objects. Open the `.drawio` source in draw.io Desktop or diagrams.net. English labels use Times New Roman; Chinese labels use Microsoft YaHei.

The PDF, SVG, and PNG files were exported with draw.io Desktop 24.7.17. The PDF pages were rendered with Poppler and visually inspected after the final export. The standalone `.drawio` sources are the authoritative editable files.

Example export commands:

```text
draw.io -x -f pdf -e -b 16 -o c05-flowchart.drawio.pdf c05-flowchart.drawio
draw.io -x -f svg -e -b 16 -o c05-flowchart.drawio.svg c05-flowchart.drawio
draw.io -x -f png -b 16 -o c05-flowchart.png c05-flowchart.drawio
```

Colors encode evidence/state updates (blue), structural plan execution and installation (orange), and independent LLM selection (purple). Other steps are black and white. Solid arrows denote algorithm control flow; purple dashed arrows are asynchronous data messages. Orange dashed arrows expand a plan-execution step. The R and C circles are standard matching flowchart connectors.
