const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const assert = require("node:assert/strict");
const babel = require("../web_ui/static/js/node_modules/@babel/core");
const source = fs.readFileSync(
  path.join(__dirname, "../web_ui/static/js/gui_upload.js"),
  "utf8",
);
const ast = babel.parseSync(source, { parserOpts: { plugins: ["jsx"] } });
let declaration;
function walk(value) {
  if (!value || typeof value !== "object") return;
  if (
    value.type === "FunctionDeclaration" &&
    value.id?.name === "mergeTrackerResults"
  )
    declaration = value;
  for (const [key, child] of Object.entries(value)) {
    if (["loc", "comments", "tokens"].includes(key)) continue;
    if (Array.isArray(child)) child.forEach(walk);
    else walk(child);
  }
}
walk(ast);
const context = vm.createContext({ Map });
vm.runInContext(source.slice(declaration.start, declaration.end), context);
const merge = (...args) =>
  JSON.parse(JSON.stringify(context.mergeTrackerResults(...args)));

test("live events update individual tracker rows before metadata is saved", () => {
  const previous = [
    { tracker: "FIRST", outcome: "Skipped", detail: "Old reason" },
    { tracker: "SECOND", outcome: "Waiting" },
  ];
  const progress = [
    {
      label: "FIRST",
      group: "tracker",
      status: "Uploaded",
      detail: "",
      url: "https://example.test/42",
    },
    { label: "SECOND", group: "tracker", status: "Uploading…", detail: "" },
    { label: "Images", group: "activity", status: "completed" },
  ];
  const rows = merge(previous, progress, true);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].outcome, "Uploaded");
  assert.equal(rows[0].detail, "");
  assert.equal(rows[0].url, "https://example.test/42");
  assert.equal(rows[1].outcome, "Uploading…");
});

test("ending a run stops unfinished tracker states without inventing success", () => {
  const progress = [
    "Waiting",
    "Checking…",
    "Preparing…",
    "Uploading…",
    "Debug processing…",
    "Uploaded",
    "Debug completed",
    "Skipped",
    "Failed",
  ].map((status, index) => ({
    label: String(index),
    group: "tracker",
    status,
    detail: "Reason",
  }));
  const rows = merge([], progress, false);
  assert(
    rows
      .slice(0, 5)
      .every((row) => row.outcome === "Not completed" && !row.url),
  );
  assert.deepEqual(
    rows.slice(5).map((row) => row.outcome),
    ["Uploaded", "Debug completed", "Skipped", "Failed"],
  );
  assert.equal(rows[8].detail, "Reason");
});
