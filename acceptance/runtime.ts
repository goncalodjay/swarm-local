import { readFileSync } from "node:fs";
import { after, describe, test } from "node:test";

export interface StepIR {
  keyword: string;
  text: string;
  parameters?: string[];
}

export interface ScenarioIR {
  name: string;
  steps: StepIR[];
  examples?: Record<string, string>[];
}

export interface FeatureIR {
  name: string;
  background?: StepIR[];
  scenarios: ScenarioIR[];
}

export interface ScenarioExecution {
  name: string;
  steps: StepIR[];
  example: Record<string, string>;
}

export function loadFeature(irPath: string): FeatureIR {
  return JSON.parse(readFileSync(irPath, "utf8")) as FeatureIR;
}

export function expandScenario(scenario: ScenarioIR): ScenarioExecution[] {
  const rows = scenario.examples && scenario.examples.length > 0 ? scenario.examples : [{}];
  return rows.map((example, index) => ({
    name: `${scenario.name}/example_${index + 1}`,
    steps: scenario.steps,
    example,
  }));
}

export interface StepHandlerDef<World> {
  pattern: RegExp;
  run: (world: World, step: StepIR, example: Record<string, string>, args: string[]) => void | Promise<void>;
}

export function resolveStepText(step: StepIR, example: Record<string, string>): string {
  return step.text.replace(/<([A-Za-z0-9_]+)>/g, (_, name: string) => {
    if (!(name in example)) {
      throw new Error(`Missing example value for parameter <${name}> in step: ${step.keyword} ${step.text}`);
    }
    return example[name];
  });
}

export async function dispatchStep<World>(
  world: World,
  step: StepIR,
  example: Record<string, string>,
  handlers: Array<StepHandlerDef<World>>,
): Promise<void> {
  const text = resolveStepText(step, example);
  for (const handler of handlers) {
    const match = handler.pattern.exec(text);
    if (match) {
      await handler.run(world, step, example, match.slice(1));
      return;
    }
  }
  throw new Error(`Unsupported step: ${step.keyword} ${text}`);
}

export function runFeature<World>(
  feature: FeatureIR,
  makeWorld: () => World,
  handlers: Array<StepHandlerDef<World>>,
  disposeWorld?: (world: World) => void,
): void {
  describe(feature.name, () => {
    const worlds: World[] = [];
    if (disposeWorld) {
      after(() => {
        for (const world of worlds) disposeWorld(world);
      });
    }
    for (const scenario of feature.scenarios) {
      const executions = expandScenario(scenario);
      for (const execution of executions) {
        test(execution.name, async () => {
          const world = makeWorld();
          worlds.push(world);
          const background = feature.background ?? [];
          for (const step of [...background, ...execution.steps]) {
            await dispatchStep(world, step, execution.example, handlers);
          }
        });
      }
    }
  });
}
