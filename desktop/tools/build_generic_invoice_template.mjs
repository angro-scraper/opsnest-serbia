/*
 * Builds the neutral first-party invoice workbook used by OpsNest Desktop.
 * The exporter fills the fixed cells referenced in delta_fakture_export.py.
 * Run from desktop/: node tools/build_generic_invoice_template.mjs
 */
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputPath = new URL("../assets/opsnest_invoice_template.xlsx", import.meta.url);
const outputFile = fileURLToPath(outputPath);
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Invoice");
sheet.showGridLines = false;

const navy = "#12304F";
const teal = "#008C80";
const light = "#EAF7F5";
const pale = "#F7FAFC";
const line = "#C8D5DF";
const muted = "#52667A";

const widths = [6, 16, 37, 12, 11, 15, 12, 17, 14, 18, 14];
for (let index = 0; index < widths.length; index += 1) {
  sheet.getRangeByIndexes(0, index, 63, 1).format.columnWidth = widths[index];
}
sheet.getRange("A1:K62").format.font = { name: "Aptos", size: 9, color: navy };

function mergeValue(range, value, format = {}) {
  const target = sheet.getRange(range);
  target.merge();
  target.values = [[value]];
  target.format = { verticalAlignment: "center", ...format };
}

function label(range, value) {
  mergeValue(range, value, { fill: light, font: { name: "Aptos", size: 7, bold: true, color: muted }, wrapText: true });
}

function value(range, value = "") {
  mergeValue(range, value, { fill: "#FFFFFF", font: { name: "Aptos", size: 8, color: navy }, wrapText: true });
}

sheet.getRange("A1:K1").format.rowHeight = 28;
sheet.getRange("A1:K1").format.fill = navy;
mergeValue("A1:B4", "OPSNEST", { fill: navy, font: { name: "Aptos Display", size: 16, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" });
mergeValue("C1:G2", "", { fill: navy, font: { name: "Aptos", size: 15, bold: true, color: "#FFFFFF" } });
mergeValue("H1:K1", "TAX DOCUMENT", { fill: teal, font: { name: "Aptos", size: 8, bold: true, color: "#FFFFFF" }, horizontalAlignment: "right" });
mergeValue("H2:K2", "INVOICE", { fill: navy, font: { name: "Aptos Display", size: 18, bold: true, color: "#FFFFFF" }, horizontalAlignment: "right" });
mergeValue("C3:G3", "Professional finance workflow", { fill: navy, font: { name: "Aptos", size: 8, color: "#DDF7F3" } });
mergeValue("H3:I3", "Invoice no.", { fill: navy, font: { name: "Aptos", size: 7, bold: true, color: "#DDF7F3" }, horizontalAlignment: "right" });
mergeValue("J3:K3", "", { fill: navy, font: { name: "Aptos", size: 10, bold: true, color: "#FFFFFF" }, horizontalAlignment: "right" });
mergeValue("C4:G4", "", { fill: navy, font: { name: "Aptos", size: 8, color: "#DDF7F3" } });
mergeValue("H4:I4", "Currency", { fill: navy, font: { name: "Aptos", size: 7, bold: true, color: "#DDF7F3" }, horizontalAlignment: "right" });
mergeValue("J4:K4", "EUR", { fill: navy, font: { name: "Aptos", size: 9, bold: true, color: "#FFFFFF" }, horizontalAlignment: "right" });

const metadataLabels = [
  ["Issue date", "Tax event date", "Due date", "Payment method"],
  ["Project", "Site / address", "Contract", "Protocol / record"],
  ["Period from", "Period to", "Reference", "Issue place"],
];
const metadataRows = [7, 10, 12];
const metadataColumns = [["A", "C"], ["D", "F"], ["G", "H"], ["I", "K"]];
for (let row = 0; row < metadataRows.length; row += 1) {
  for (let col = 0; col < 4; col += 1) {
    const [startColumn, endColumn] = metadataColumns[col];
    const labelRow = metadataRows[row];
    label(`${startColumn}${labelRow}:${endColumn}${labelRow}`, metadataLabels[row][col]);
    value(`${startColumn}${labelRow + 1}:${endColumn}${labelRow + 1}`, "");
  }
}

mergeValue("A15:E15", "SUPPLIER", { fill: teal, font: { name: "Aptos", size: 8, bold: true, color: "#FFFFFF" } });
mergeValue("G15:K15", "CUSTOMER", { fill: teal, font: { name: "Aptos", size: 8, bold: true, color: "#FFFFFF" } });
const partyLabels = ["Company", "Registration no.", "VAT no.", "Address", "Manager", "Contact"];
for (let index = 0; index < partyLabels.length; index += 1) {
  const row = 16 + index;
  label(`A${row}:B${row}`, partyLabels[index]);
  value(`C${row}:E${row}`);
  label(`G${row}:H${row}`, partyLabels[index]);
  value(`I${row}:K${row}`);
}

mergeValue("A23:K23", "INVOICE ITEMS", { fill: teal, font: { name: "Aptos", size: 8, bold: true, color: "#FFFFFF" } });
const headers = ["#", "Category", "Description", "Unit", "Quantity", "Unit price", "Discount", "Net", "VAT", "Gross", "Code / stage"];
sheet.getRange("A24:K24").values = [headers];
sheet.getRange("A24:K24").format = { fill: navy, font: { name: "Aptos", size: 7, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "all", style: "thin", color: line } };
sheet.getRange("A24:K24").format.rowHeight = 26;
sheet.getRange("A25:K42").format = { fill: "#FFFFFF", borders: { preset: "all", style: "thin", color: line }, verticalAlignment: "center", wrapText: true };
sheet.getRange("A25:K42").format.rowHeight = 18;
sheet.getRange("E25:J42").format.numberFormat = "#,##0.00";

label("A44:F44", "Notes");
value("A45:F48", "");
const totalLabels = ["Subtotal", "Adjustment", "Tax base", "VAT", "Total", "Retention", "Advance", "Paid", "Balance", "Reference amount", "Payment deadline"];
for (let index = 0; index < totalLabels.length; index += 1) {
  const row = 44 + index;
  label(`G${row}:I${row}`, totalLabels[index]);
  value(`J${row}:K${row}`, "");
}
sheet.getRange("J44:K53").format.numberFormat = "#,##0.00";
sheet.getRange("J48:K48").format = { fill: "#DDF7F3", font: { name: "Aptos", size: 10, bold: true, color: navy }, borders: { preset: "all", style: "thin", color: teal } };

mergeValue("A50:F50", "", { fill: pale, font: { name: "Aptos", size: 8, italic: true, color: muted }, wrapText: true });
mergeValue("A56:K56", "BANK DETAILS", { fill: teal, font: { name: "Aptos", size: 8, bold: true, color: "#FFFFFF" } });
label("A57:C57", "Bank"); value("A58:C58");
label("D57:G57", "IBAN"); value("D58:G58");
label("H57:K57", "BIC / SWIFT"); value("H58:K58");
mergeValue("A60:K60", "", { fill: pale, font: { name: "Aptos", size: 8, color: muted }, wrapText: true });
mergeValue("A62:E62", "Prepared by ____________________________________", { font: { name: "Aptos", size: 8, color: navy } });
mergeValue("G62:K62", "Received by ____________________________________", { font: { name: "Aptos", size: 8, color: navy }, horizontalAlignment: "right" });

sheet.getRange("A1:K62").format.borders = { preset: "outside", style: "thin", color: line };
const preview = await workbook.render({ sheetName: "Invoice", autoCrop: "all", scale: 1, format: "png" });
const previewPath = new URL("../tmp/generic_invoice_template_preview.png", import.meta.url);
await fs.mkdir(new URL("../tmp/", import.meta.url), { recursive: true });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputFile);
console.log(JSON.stringify({ output: outputFile, preview: fileURLToPath(previewPath) }));
