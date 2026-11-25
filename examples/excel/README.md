# Excel: from a workbook to its IR, and its tools over REST

The building blocks under the Excel domain. Build a real macro-enabled `.xlsm`
and print the workbook IR (intermediate representation) AgenticHarvest extracts from
it. Then serve the tools over REST and call them with `curl`.

For the whole observe → mine → agentify loop on an Excel workflow, see
[`../expense_review/`](../expense_review/).

## Run it

From the repo root, with the `excel` extra (and `rest` for the server):

```bash
uv sync --extra excel --extra rest
uv run python examples/excel/build_orders_workbook.py orders.xlsm
```

The script builds the small **orders** workbook without Excel or LibreOffice,
runs `build_ir` on it, and prints what it found. This is the actual output:

```
Built macro-enabled workbook: orders.xlsm

Sheets (name, code name, role, headers)
------------------------------------------------------------
  Orders    shOrders   input      ['OrderID', 'Qty', 'Price']
  Summary   shSummary  output     ['Metric', 'Value']
  Config    shConfig   input      ['TaxRate']

Macros (name, module, event handler?)
------------------------------------------------------------
  RefreshSummary    modOrders
  ClearSummary      modOrders
  WriteTotals       modOrders
  ComputeTotal      modOrders
  TaxRate           modOrders
  ConfigName        modOrders
  Workbook_Open     ThisWorkbook  [event]

Call graph (caller -> callee)
------------------------------------------------------------
  ComputeTotal     -> TaxRate
  RefreshSummary   -> ClearSummary
  RefreshSummary   -> WriteTotals
  Workbook_Open    -> RefreshSummary
  WriteTotals      -> ComputeTotal

Data flow (macro reads/writes which sheet)
------------------------------------------------------------
  ClearSummary      reads=-  writes=['Summary']
  WriteTotals       reads=-  writes=['Summary']
  ComputeTotal      reads=['Orders']  writes=-
  TaxRate           reads=['Config']  writes=-
  ConfigName        reads=['Config']  writes=-

Named ranges
------------------------------------------------------------
  TaxCell    -> Config!$A$1

Controls
------------------------------------------------------------
  hyperlink  on Summary!D2  'Back to Orders'

Workflow reachable from Workbook_Open
------------------------------------------------------------
  Workbook_Open -> RefreshSummary -> ClearSummary -> WriteTotals -> ComputeTotal -> TaxRate
```

What each part means:

| Part | How AgenticHarvest finds it |
|---|---|
| **Sheets and roles** | openpyxl reads the sheets and their VBA code names. A sheet only macros read is an `input`; one they write is an `output`. |
| **Macros** | oletools extracts the VBA source; AgenticHarvest splits it into procedures and marks `Workbook_*` / `Worksheet_*` as event handlers. |
| **Call graph** | Which procedure names appear inside each procedure's body. |
| **Data flow** | Each sheet reference in a macro, by code name (`shSummary`) or literal name (`Sheets("Config")`), is a read, unless the rest of its statement assigns a value or clears the range, which makes it a write. |
| **Workflow** | Everything reachable through the call graph from the event handler. |

No LLM is involved. Extraction is static analysis of the file. An LLM only
appears later in AgenticHarvest, to act on what was extracted.

## Serve the tools over REST

```bash
uv run python examples/excel/serve_rest.py            # http://127.0.0.1:8000
```

In another terminal:

```bash
curl localhost:8000/tools                              # every tool, its domain and effect
curl -X POST localhost:8000/tools/list_sheets/call \
     -H 'content-type: application/json' -d '{"path": "orders.xlsm"}'
```

`list_sheets` answers with the same sheets as above:

```json
{"ok": true, "output": [
  {"name": "Orders",  "codename": "shOrders",  "role": "input",  "headers": ["OrderID", "Qty", "Price"]},
  {"name": "Summary", "codename": "shSummary", "role": "output", "headers": ["Metric", "Value"]},
  {"name": "Config",  "codename": "shConfig",  "role": "input",  "headers": ["TaxRate"]}]}
```

The server offers every installed domain's tools: 8 Excel, 5 SQLite, and 5 web
tools (calling a web tool needs the `web` extra). A write without `"confirm": true` is refused:

```bash
curl -X POST localhost:8000/tools/write_cell/call -H 'content-type: application/json' \
     -d '{"path": "orders.xlsm", "sheet": "Summary", "cell": "B1", "value": 1}'
# {"ok": false, "error": "Tool 'write_cell' has a write effect and requires 'confirm: true' to run."}
```

The `path` is read on the server, so pass one the server process can open.
The REST channel has no authentication: keep it on `127.0.0.1`.

## Running macros needs a workbook Excel authored

`run_macro` drives a real Microsoft Excel through xlwings (the `excel-live`
extra). The workbooks built here are synthesized without Excel. Their VBA
project is complete enough for AgenticHarvest to read, but **Excel won't load it**:
it offers to repair the file, and the repair drops the macros.

To run a macro, write the workbook as parts and assemble it in Excel once:

```python
from agenticharvest.domains.excel.kit import write_kit
kit = write_kit("kit/", sheets=SHEETS, modules=MODULES, name="book")
# kit/book.xlsx + kit/modules/<module>.bas
```

Then, in Excel:

1. Open `book.xlsx`.
2. **Tools → Macro → Visual Basic Editor** (Windows: Alt+F11), then
   **File → Import File…** for each `.bas` file.
3. Save as **Excel Macro-Enabled Workbook (.xlsm)**.

Excel's macro-security prompts must be answered, or the file placed in a
trusted location. xlwings runs Excel hidden, and a hidden Excel waits forever
on an unanswered dialog.

## One source of truth

The workbook is defined once in
[`agenticharvest.domains.excel.samples`](../../src/agenticharvest/domains/excel/samples.py),
which ships with the package. This example and the test suite
([`tests/domains/excel/`](../../tests/domains/excel/)) both use it, so the demo
and the tests can't drift apart. The synthesizer that writes a `vbaProject.bin`
without Excel is
[`agenticharvest.domains.excel.synth`](../../src/agenticharvest/domains/excel/synth.py).
