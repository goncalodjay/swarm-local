import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import readline from "node:readline";

interface Job {
  id: string;
  feature_json: string;
  generated_dir: string;
  work_dir: string;
  timeout?: string;
}

interface Response {
  id: string;
  outcome: "test_success" | "test_failure" | "infrastructure_error";
  output: string;
  error: string;
  duration: number;
}

interface GeneratedMetadata {
  schema_version: number;
  feature_path: string;
  ir_path: string;
  implementation_hash: string;
  hash_scope: string;
  generated_files: string[];
}

function metadataDir(generatedDir: string): string {
  return path.join(generatedDir, "metadata");
}

function featureName(pathStr: string): string {
  return path.basename(pathStr).replace(/\.[^.]+$/, "");
}

function irFeatureName(featureJson: string): string | null {
  try {
    const parsed = JSON.parse(readFileSync(featureJson, "utf8")) as { name?: unknown };
    return typeof parsed.name === "string" ? parsed.name : null;
  } catch {
    return null;
  }
}

function generatedTestsForFeature(generatedDir: string, featureJson: string): string[] {
  const metaDir = metadataDir(generatedDir);
  const wanted = irFeatureName(featureJson) ?? featureName(featureJson);
  if (!existsSync(metaDir)) {
    return [];
  }
  const tests: string[] = [];
  for (const entry of readdirSync(metaDir).filter((name) => name.endsWith(".json"))) {
    let meta: GeneratedMetadata;
    try {
      meta = JSON.parse(readFileSync(path.join(metaDir, entry), "utf8"));
    } catch {
      continue;
    }
    if (featureName(meta.feature_path) !== wanted) continue;
    for (const rel of meta.generated_files) {
      const testPath = path.resolve(generatedDir, rel);
      if (existsSync(testPath) && testPath.endsWith(".test.ts")) {
        tests.push(testPath);
      }
    }
  }
  return [...new Set(tests)].sort();
}

function timeoutMs(value: string | undefined): number | undefined {
  if (!value) return undefined;
  const match = /^(\d+)s$/.exec(value);
  if (match) return Number(match[1]) * 1000;
  const ms = Number(value);
  return Number.isFinite(ms) && ms > 0 ? ms : undefined;
}

function runTests(job: Job): Promise<Response> {
  const files = generatedTestsForFeature(job.generated_dir, job.feature_json);
  if (files.length === 0) {
    return Promise.resolve({
      id: job.id,
      outcome: "infrastructure_error",
      output: "",
      error: `No generated tests found for ${job.feature_json} in ${job.generated_dir}`,
      duration: 0,
    });
  }
  const irPath = path.resolve(job.feature_json);
  const started = Date.now();
  return new Promise((resolve) => {
    const child: ChildProcess = spawn(
      "node",
      ["--test", ...files],
      {
        cwd: job.work_dir,
        env: { ...process.env, SWARMFORGE_ACCEPTANCE_IR: irPath },
      },
    );
    let output = "";
    let error = "";
    const timeout = timeoutMs(job.timeout);
    const timer = timeout ? setTimeout(() => child.kill("SIGKILL"), timeout) : undefined;
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      error += chunk.toString();
    });
    child.on("error", (err) => {
      if (timer) clearTimeout(timer);
      resolve({
        id: job.id,
        outcome: "infrastructure_error",
        output,
        error: err.message,
        duration: Date.now() - started,
      });
    });
    child.on("exit", (code) => {
      if (timer) clearTimeout(timer);
      const outcome = code === 0 ? "test_success" : "test_failure";
      resolve({
        id: job.id,
        outcome,
        output: (output + error).trim(),
        error: "",
        duration: Date.now() - started,
      });
    });
  });
}

async function main(): Promise<void> {
  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    if (line.trim() === "") continue;
    let job: Job;
    try {
      job = JSON.parse(line);
    } catch {
      const response: Response = {
        id: "unknown",
        outcome: "infrastructure_error",
        output: "",
        error: "Invalid JSON job",
        duration: 0,
      };
      process.stdout.write(JSON.stringify(response) + "\n");
      continue;
    }
    const response = await runTests(job);
    process.stdout.write(JSON.stringify(response) + "\n");
  }
}

void main();
