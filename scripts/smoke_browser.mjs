#!/usr/bin/env node
// Real Edge smoke coverage for the served dashboard; requires Node 20+ and no npm packages.
import { spawn, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { delimiter, dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

const frontendUrl = process.env.IGL_FRONTEND_URL || "http://127.0.0.1:8001";
const apiBase = (process.env.IGL_API_BASE_URL || "http://127.0.0.1:8000/api/v1").replace(/\/$/, "");
const views = [
  ["overview", "Overview"], ["cameras", "Cameras"], ["workers", "Workers"],
  ["events", "Events"], ["incidents", "Incidents"], ["alarm", "Alarm center"],
  ["rules", "Rules"], ["notifications", "Notifications"], ["evidence", "Evidence"],
  ["analytics", "Analytics"], ["system", "System health"],
];
const selectedViews = process.env.IGL_BROWSER_VIEWS
  ? views.filter(([view]) => process.env.IGL_BROWSER_VIEWS.split(",").map((value) => value.trim()).includes(view))
  : views;
const widths = [390, 768, 1024, 1440, 1920];
const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function locateEdge() {
  const candidates = [
    process.env.IGL_EDGE_PATH,
    process.env["PROGRAMFILES(X86)"] && join(process.env["PROGRAMFILES(X86)"], "Microsoft", "Edge", "Application", "msedge.exe"),
    process.env.PROGRAMFILES && join(process.env.PROGRAMFILES, "Microsoft", "Edge", "Application", "msedge.exe"),
    ...String(process.env.PATH || "").split(delimiter).map((directory) => join(directory, "msedge.exe")),
  ].filter(Boolean);
  return candidates.find(existsSync);
}

async function freePort() {
  const server = createServer();
  await new Promise((ok, fail) => server.once("error", fail).listen(0, "127.0.0.1", ok));
  const { port } = server.address();
  await new Promise((ok, fail) => server.close((error) => error ? fail(error) : ok()));
  return port;
}

async function waitForTarget(port, child) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`Edge exited during startup (${child.exitCode}).`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json`, { signal: AbortSignal.timeout(500) });
      const targets = await response.json();
      const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* DevTools endpoint is not ready yet. */ }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
  }
  throw new Error("Edge DevTools did not become ready.");
}

function connectCdp(url) {
  return new Promise((resolvePromise, reject) => {
    const socket = new WebSocket(url);
    let nextId = 0;
    const pending = new Map();
    const exceptions = [];
    const consoleErrors = [];
    const failedResponses = [];
    socket.addEventListener("error", () => reject(new Error("Could not connect to Edge DevTools.")), { once: true });
    socket.addEventListener("open", () => {
      socket.addEventListener("message", (message) => {
        const payload = JSON.parse(message.data);
        if (payload.id && pending.has(payload.id)) {
          const handlers = pending.get(payload.id);
          pending.delete(payload.id);
          if (payload.error) handlers.reject(new Error(payload.error.message));
          else handlers.resolve(payload.result);
        } else if (payload.method === "Runtime.exceptionThrown") {
          exceptions.push(payload.params.exceptionDetails?.text || "JavaScript exception");
        } else if (payload.method === "Runtime.consoleAPICalled" && payload.params.type === "error") {
          consoleErrors.push((payload.params.args || []).map((item) => item.value || item.description || "").join(" "));
        } else if (payload.method === "Network.responseReceived" && payload.params.response.status >= 400) {
          failedResponses.push({ status: payload.params.response.status, url: payload.params.response.url });
        }
      });
      const command = (method, params = {}) => new Promise((ok, fail) => {
        const id = ++nextId;
        pending.set(id, { resolve: ok, reject: fail });
        socket.send(JSON.stringify({ id, method, params }));
      });
      resolvePromise({ socket, command, exceptions, consoleErrors, failedResponses });
    });
  });
}

const edge = locateEdge();
if (!edge) throw new Error("Microsoft Edge was not found. Set IGL_EDGE_PATH to msedge.exe.");
const debugPort = await freePort();
const profileRoot = join(projectRoot, ".codex-tmp");
mkdirSync(profileRoot, { recursive: true });
const profile = mkdtempSync(join(profileRoot, "edge-smoke-"));
const child = spawn(edge, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  `--user-data-dir=${profile}`, `--remote-debugging-port=${debugPort}`, frontendUrl,
], { stdio: ["ignore", "ignore", "ignore"], windowsHide: true });

let cdp;
try {
  const websocketUrl = await waitForTarget(debugPort, child);
  cdp = await connectCdp(websocketUrl);
  await cdp.command("Runtime.enable");
  await cdp.command("Network.enable");
  const evaluate = async (expression) => {
    const result = await cdp.command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed.");
    return result.result?.value;
  };
  let documentReady = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { documentReady = await evaluate("!!document.querySelector('#login-api-base')"); } catch { /* First about:blank target may not be the loaded tab yet. */ }
    if (documentReady) break;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
  }
  if (!documentReady) throw new Error("The served dashboard form did not load in Edge.");
  let authGateReady = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    authGateReady = await evaluate("!document.querySelector('#login-shell').hidden || !document.querySelector('#app-shell').hidden");
    if (authGateReady) break;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
  }
  if (!authGateReady) throw new Error("The dashboard did not initialize its application script.");
  await evaluate(`(() => { const input = document.querySelector('#login-api-base'); input.value = ${JSON.stringify(apiBase)}; input.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  const targetHealth = await evaluate("fetch(`${sessionStorage.getItem('iglSafetyApiBase')}/system/health`).then(response => { if (!response.ok) throw new Error(`Health check failed (${response.status})`); return response.json(); })");
  const needsLogin = !String(targetHealth.access_control || "").startsWith("ANONYMOUS_ACCESS_ENABLED");
  const expectedGate = needsLogin ? "!document.querySelector('#login-shell').hidden" : "!document.querySelector('#app-shell').hidden";
  authGateReady = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    authGateReady = await evaluate(expectedGate);
    if (authGateReady) break;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
  }
  if (!authGateReady) throw new Error("The dashboard did not reach the target API's configured access state.");
  let authentication = needsLogin ? "LOGIN_REQUIRED" : "ANONYMOUS_DEVELOPMENT_ACCESS";
  if (await evaluate("!document.querySelector('#login-shell').hidden")) {
    const username = process.env.IGL_TEST_USERNAME;
    const password = process.env.IGL_TEST_PASSWORD;
    if (username && password) {
      await evaluate(`(() => { const form = document.querySelector('#login-form'); form.elements.username_or_email.value = ${JSON.stringify(username)}; form.elements.password.value = ${JSON.stringify(password)}; form.requestSubmit(); })()`);
      for (let attempt = 0; attempt < 80; attempt += 1) {
        if (await evaluate("!document.querySelector('#app-shell').hidden")) break;
        await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
      }
      authentication = await evaluate("!document.querySelector('#app-shell').hidden ? 'LOGIN_SUCCEEDED' : `LOGIN_FAILED: ${document.querySelector('#login-message').textContent}`");
      if (authentication !== "LOGIN_SUCCEEDED") {
        throw new Error(`${authentication}; browser errors: ${JSON.stringify([...cdp.exceptions, ...cdp.consoleErrors])}; failed requests: ${JSON.stringify(cdp.failedResponses)}`);
      }
    } else {
      authentication = "NOT_VALIDATED_NO_TEST_LOGIN_CREDENTIALS";
    }
  }
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const ready = await evaluate("!document.querySelector('#app-shell').hidden && !document.querySelector('#view-content').innerText.includes('Loading current API data')");
    if (ready) break;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
  }

  const results = [];
  for (const [view, title] of selectedViews) {
    const visible = await evaluate(`!document.querySelector('[data-view="${view}"]').hidden`);
    if (!visible) { results.push({ view, result: "HIDDEN_BY_ROLE" }); continue; }
    await evaluate(`document.querySelector('[data-view="${view}"]').click()`);
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const settled = await evaluate(`document.querySelector('#view-title').textContent === ${JSON.stringify(title)} && !document.querySelector('#view-content').innerText.includes('Loading current API data')`);
      if (settled) break;
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
    }
    const widthResults = [];
    for (const width of widths) {
      await cdp.command("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: false, screenWidth: width, screenHeight: 900 });
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 150));
      await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
      widthResults.push(await evaluate(`({ requested: ${width}, viewport: innerWidth, document: document.documentElement.scrollWidth, title: document.querySelector('#view-title').textContent, content: document.querySelector('#view-content').innerText, roots: Array.from(document.body.children).map(node => ({tag: node.tagName, id: node.id, className: String(node.className || '').slice(0, 60), left: Math.round(node.getBoundingClientRect().left), right: Math.round(node.getBoundingClientRect().right), width: Math.round(node.getBoundingClientRect().width), scrollWidth: node.scrollWidth})), overflow: Array.from(document.querySelectorAll('body *')).map(node => ({node, rect: node.getBoundingClientRect()})).filter(item => item.rect.right > innerWidth + 1 && getComputedStyle(item.node).position !== 'fixed').slice(0, 8).map(item => ({tag: item.node.tagName, id: item.node.id, className: String(item.node.className || '').slice(0, 80), right: Math.round(item.rect.right), width: Math.round(item.rect.width), text: (item.node.innerText || '').slice(0, 60), parents: [item.node.parentElement, item.node.parentElement?.parentElement, item.node.parentElement?.parentElement?.parentElement].filter(Boolean).map(parent => ({tag: parent.tagName, id: parent.id, className: String(parent.className || '').slice(0, 60), left: Math.round(parent.getBoundingClientRect().left), right: Math.round(parent.getBoundingClientRect().right), width: Math.round(parent.getBoundingClientRect().width)}))})) })`));
    }
    const layouts = widthResults.map((item) => ({ width: item.requested, viewport: item.viewport, document: item.document, noOverflow: item.document <= item.viewport, ...(item.document > item.viewport ? { roots: item.roots, overflow: item.overflow } : {}) }));
    const content = widthResults[0]?.content || "";
    results.push({ view, result: "RENDERED", pageContent: content.slice(0, 120), apiErrorText: /Request failed|Internal Server Error|Insufficient permission/.test(content), widths: layouts });
  }

  const cameraVisible = await evaluate("!document.querySelector('[data-view=\"cameras\"]').hidden");
  let webcam = { status: "NOT_VALIDATED", reason: cameraVisible ? "camera view was not reached" : "camera view hidden by role" };
  if (cameraVisible && process.env.IGL_SKIP_WEBCAM !== "1") {
    await evaluate("document.querySelector('[data-view=\"cameras\"]').click()");
    // Camera rendering also performs a physical device discovery probe. Windows
    // backends may need several seconds to release/reopen the capture handle.
    for (let attempt = 0; attempt < 240; attempt += 1) {
      if (await evaluate("document.querySelector('#view-title').textContent === 'Cameras' && !!document.querySelector('#webcam-start')")) break;
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
    }
    const cameraControls = await evaluate("!!document.querySelector('#webcam-start')");
    if (!cameraControls) {
      const cameraPage = await evaluate("({ title: document.querySelector('#view-title')?.textContent, content: document.querySelector('#view-content')?.innerText?.slice(0, 400) })");
      throw new Error(`Camera controls did not render: ${JSON.stringify(cameraPage)}`);
    }
    const initiallyDisabled = await evaluate("document.querySelector('#webcam-start').disabled");
    if (!initiallyDisabled) {
      await evaluate("document.querySelector('#webcam-start').click()");
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const state = await evaluate("document.querySelector('#webcam-status-badge')?.textContent || 'NOT_AVAILABLE'");
        if (["CAMERA: ONLINE", "CAMERA: ERROR"].includes(state)) break;
        await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
      }
      for (let attempt = 0; attempt < 40; attempt += 1) {
        if (await evaluate("!!document.querySelector('#webcam-preview-image')?.getAttribute('src') && document.querySelector('#webcam-preview-image').naturalWidth > 0")) break;
        await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
      }
      const firstOpen = await evaluate("({ state: document.querySelector('#webcam-status-badge')?.textContent, error: document.querySelector('#webcam-error')?.textContent, resolution: document.querySelector('#webcam-resolution')?.textContent, fps: document.querySelector('#webcam-fps')?.textContent, preview: !!document.querySelector('#webcam-preview-image')?.getAttribute('src') && document.querySelector('#webcam-preview-image').naturalWidth > 0 })");
      if (firstOpen.state === "CAMERA: ONLINE") {
        for (let attempt = 0; attempt < 40; attempt += 1) {
          if (await evaluate("!document.querySelector('#webcam-stop').disabled")) break;
          await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
        }
        await evaluate("document.querySelector('#webcam-stop').click()");
        for (let attempt = 0; attempt < 120; attempt += 1) {
          if (await evaluate("document.querySelector('#webcam-status-badge')?.textContent === 'CAMERA: STOPPED'")) break;
          await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
        }
        const stopState = await evaluate("document.querySelector('#webcam-status-badge')?.textContent");
        await evaluate("document.querySelector('#webcam-start').click()");
        for (let attempt = 0; attempt < 100; attempt += 1) {
          if (await evaluate("document.querySelector('#webcam-status-badge')?.textContent === 'CAMERA: ONLINE' && !document.querySelector('#webcam-stop').disabled")) break;
          await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
        }
        const reopen = await evaluate("document.querySelector('#webcam-status-badge')?.textContent");
        if (reopen === "CAMERA: ONLINE") await evaluate("document.querySelector('#webcam-stop').click()");
        for (let attempt = 0; attempt < 120; attempt += 1) {
          if (await evaluate("document.querySelector('#webcam-status-badge')?.textContent === 'CAMERA: STOPPED'")) break;
          await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
        }
        const finalStopState = await evaluate("document.querySelector('#webcam-status-badge')?.textContent");
        const cameraCycleComplete = firstOpen.state === "CAMERA: ONLINE" && firstOpen.preview && stopState === "CAMERA: STOPPED" && reopen === "CAMERA: ONLINE" && finalStopState === "CAMERA: STOPPED";
        webcam = { status: cameraCycleComplete ? "VALIDATED" : "PARTIALLY_VALIDATED", firstOpen, stopState, reopen, finalStopState };
      } else {
        webcam = { status: "NOT_VALIDATED", firstOpen };
      }
    } else {
      webcam = { status: "NOT_VALIDATED", reason: await evaluate("document.querySelector('#webcam-error')?.textContent || 'Camera start control disabled; source not discovered.'") };
    }
  }

  const failures = results.flatMap((result) => (result.widths || []).filter((item) => !item.noOverflow));
  const browserErrors = [...cdp.exceptions, ...cdp.consoleErrors];
  if (authentication === "LOGIN_SUCCEEDED") {
    await evaluate("document.querySelector('#logout-button').click()");
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (await evaluate("!document.querySelector('#login-shell').hidden")) break;
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
    }
    authentication = await evaluate("!document.querySelector('#login-shell').hidden ? 'LOGIN_AND_LOGOUT_VALIDATED' : 'LOGOUT_NOT_VALIDATED'");
  }
  console.log(JSON.stringify({ authentication, views: results, horizontalOverflow: failures, webcam, browserErrors, failedRequests: cdp.failedResponses }, null, 2));
  if (failures.length || browserErrors.length || cdp.failedResponses.length || webcam.status === "PARTIALLY_VALIDATED") process.exitCode = 1;
} finally {
  if (cdp?.socket.readyState === WebSocket.OPEN) cdp.socket.close();
  if (child.exitCode === null) {
    try {
      if (process.platform === "win32") execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      else child.kill("SIGTERM");
    } catch { child.kill("SIGTERM"); }
  }
  const safeProfile = resolve(profile);
  const safeRoot = resolve(profileRoot) + sep;
  if (safeProfile.startsWith(safeRoot) && safeProfile.includes("edge-smoke-")) {
    try { rmSync(safeProfile, { recursive: true, force: true, maxRetries: 3, retryDelay: 500 }); }
    catch { console.warn(`Temporary Edge profile could not be removed: ${safeProfile}`); }
  }
}
