const fs = require("node:fs");

for (const filename of [
  "n8n/CCTV-Investigation-Orchestrator.json",
  "n8n/CCTV-Investigation-Orchestrator-Import.json",
]) {
  const workflow = JSON.parse(fs.readFileSync(filename, "utf8"));
  const nodes = Object.fromEntries(workflow.nodes.map((node) => [node.name, node]));
  const neighbors = Object.fromEntries(
    Array.from({ length: 6 }, (_, index) => ["Area-" + (index + 1), []]),
  );
  const input = { case_id: "SYNTHETIC", description: "blue car", initial_zone: "Area-1", neighbors };
  const run = (code, data) => new Function("$input", code)({ first: () => ({ json: data }) });
  const validated = run(nodes["Validate Case Intake"].parameters.jsCode, {
    ...input, neighbors_json: JSON.stringify(neighbors),
  })[0].json;
  const planned = run(nodes["Investigation Planner (demo)"].parameters.jsCode, validated)[0].json;
  const queued = run(nodes["Zone Review Queue (demo)"].parameters.jsCode, planned);
  if (queued.length !== 6 || new Set(queued.map((item) => item.json.jurisdiction)).size !== 6) {
    throw new Error("Six-jurisdiction assignment failed for " + filename);
  }
  console.log(filename + ": six arbitrary jurisdictions produced six work assignments");
}
