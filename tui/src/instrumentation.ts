import { NodeSDK } from "@opentelemetry/sdk-node";
import { LangfuseSpanProcessor } from "@langfuse/otel";

export function isTracingConfigured(): boolean {
  return Boolean(
    process.env.LANGFUSE_PUBLIC_KEY &&
    process.env.LANGFUSE_SECRET_KEY &&
    process.env.LANGFUSE_BASE_URL,
  );
}

let sdk: NodeSDK | null = null;

if (isTracingConfigured()) {
  sdk = new NodeSDK({
    spanProcessors: [new LangfuseSpanProcessor()],
  });
  sdk.start();
}

export function shutdownTracing(): Promise<void> {
  if (!sdk) return Promise.resolve();
  return sdk.shutdown();
}

export { isTracingConfigured as _isTracingConfigured };
