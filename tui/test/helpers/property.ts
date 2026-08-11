import { test } from "node:test";

export function deterministicIntegers(count: number, seed = 0x9e3779b9): number[] {
  let state = seed >>> 0;
  const values: number[] = [];
  for (let index = 0; index < count; index++) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    values.push(state);
  }
  return values;
}

export function forAll<T>(name: string, cases: readonly T[], property: (value: T, index: number) => void): void {
  test(name, () => {
    for (const [index, value] of cases.entries()) {
      try {
        property(value, index);
      } catch (error) {
        throw new Error(`property case ${index}: ${JSON.stringify(value)}`, { cause: error });
      }
    }
  });
}
