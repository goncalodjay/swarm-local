import { App } from "./src/app.ts";
import { FileSystemTuiIO, projectRoot } from "./src/io.ts";
import { parseKey } from "./src/keys.ts";
import { frameModel, renderFrame } from "./src/render.ts";

const POLL_INTERVAL_MS = 1000;

async function main(): Promise<void> {
  const root = projectRoot(process.cwd());
  const io = new FileSystemTuiIO(root);
  const app = new App(io);
  app.start();

  const render = (): void => {
    const frame = renderFrame(frameModel(app));
    process.stdout.write("\x1b[2J\x1b[H");
    process.stdout.write(frame.join("\n") + "\n");
  };

  render();

  const pollTimer = setInterval(() => {
    if (app.view === "attached") return;
    app.poll();
    render();
  }, POLL_INTERVAL_MS);

  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
  }
  process.stdin.resume();
  process.stdin.setEncoding("utf8");

  const handleData = async (data: Buffer): Promise<void> => {
    const key = parseKey(data.toString("utf8"));
    if (!key) return;
    if (app.view === "attached") return;
    if (key === "quit") {
      clearInterval(pollTimer);
      app.quit();
      return;
    }
    await app.press(key);
    render();
  };

  process.stdin.on("data", (data: Buffer) => {
    void handleData(data);
  });

  process.stdout.write("\x1b[?25l");
  process.stdout.write("\x1b[2J\x1b[H");

  process.on("SIGWINCH", () => {
    app.checkSize();
    render();
  });
}

void main();
