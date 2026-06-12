/*
File: skills/serve-learning-graph/scripts/server.js

Purpose:
    Serve a Learning Directed Graph SQLite database through the local GUI
    workflow host.

Responsibilities:
    - Call export_graph.py to load graph data
    - Start an HTTP server on the first available port
    - Serve the Vite-built React app at / and static assets under /assets/
    - Serve graph JSON at /graph.json
    - Serve local /api/* workflow endpoints through Python adapters
    - Optionally open the local URL in the user's browser

What this file does NOT do:
    - Access SQLite directly
    - Install npm packages or build the React app
    - Call interactive CLI REPL loops
    - Run as a background daemon

Inputs: command-line DB path, goal ID/prefix, user ID, preferred port
Outputs: foreground HTTP server and printed local URL
*/

const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

function parseArgs(argv) {
  const args = {
    db: null,
    goal: null,
    user: "default",
    port: 8765,
    host: "127.0.0.1",
    noOpen: false,
    python: process.env.PYTHON || process.env.PYTHON_BIN || "python3",
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--db") args.db = argv[++i];
    else if (arg === "--goal") args.goal = argv[++i];
    else if (arg === "--user") args.user = argv[++i];
    else if (arg === "--port") args.port = Number.parseInt(argv[++i], 10);
    else if (arg === "--host") args.host = argv[++i];
    else if (arg === "--python") args.python = argv[++i];
    else if (arg === "--no-open") args.noOpen = true;
    else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!Number.isInteger(args.port) || args.port < 1 || args.port > 65535) {
    throw new Error(`Invalid port: ${args.port}`);
  }
  return args;
}

function printHelp() {
  console.log(`Usage: node server.js [--db path] [--goal id-or-prefix] [--user id] [--port n] [--no-open]

Serve a Learning Directed Graph SQLite DB as a React Flow local page.
The server runs in the foreground. Stop it with Ctrl+C.`);
}

function loadGraph(args) {
  const exporter = path.join(__dirname, "export_graph.py");
  const pyArgs = [exporter, "--user", args.user];
  if (args.db) pyArgs.push("--db", args.db);
  if (args.goal) pyArgs.push("--goal", args.goal);

  const result = childProcess.spawnSync(args.python, pyArgs, {
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || "export_graph.py failed").trim());
  }
  return JSON.parse(result.stdout);
}

function runWorkflowApi(args, action, payload = {}, user = args.user) {
  const adapter = path.join(__dirname, "workflow_api.py");
  const pyArgs = [adapter, "--action", action, "--user", user];
  if (args.db) pyArgs.push("--db", args.db);

  const result = childProcess.spawnSync(args.python, pyArgs, {
    encoding: "utf8",
    input: JSON.stringify(payload),
    maxBuffer: 20 * 1024 * 1024,
    // Run the Python adapter as a pure data plane: it may touch SQLite but must
    // refuse LLM work, so generation steps are handed back to Codex/Claude Code.
    env: { ...process.env, LDG_WEB_MODE: "1" },
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || "workflow_api.py failed").trim());
  }
  return JSON.parse(result.stdout);
}

function staticRoot() {
  return path.resolve(__dirname, "..", "web", "dist");
}

function ensureWebBuild(root) {
  const indexPath = path.join(root, "index.html");
  if (!fs.existsSync(indexPath)) {
    throw new Error(
      `React web build not found at ${indexPath}. Run: cd skills/serve-learning-graph/web && pnpm install && pnpm build`
    );
  }
}

function contentType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".map": "application/json; charset=utf-8",
  }[ext] || "application/octet-stream";
}

function sendFile(res, filePath, cacheControl = "no-store") {
  fs.stat(filePath, (statError, stat) => {
    if (statError || !stat.isFile()) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Not found");
      return;
    }

    res.writeHead(200, {
      "Content-Type": contentType(filePath),
      "Content-Length": stat.size,
      "Cache-Control": cacheControl,
    });
    fs.createReadStream(filePath).pipe(res);
  });
}

function sendJson(res, statusCode, payload) {
  const text = JSON.stringify(payload, null, 2);
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(text),
    "Cache-Control": "no-store",
  });
  res.end(text);
}

function resolveStaticPath(root, urlPathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPathname);
  } catch {
    return null;
  }

  const candidate = path.resolve(root, `.${decoded}`);
  const relative = path.relative(root, candidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    return null;
  }
  return candidate;
}

function queryPayload(url) {
  const payload = {};
  for (const [key, value] of url.searchParams.entries()) {
    payload[key] = value;
  }
  return payload;
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 8 * 1024 * 1024) {
        reject(new Error("Request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

// Run a workflow action and send its JSON envelope, mapping thrown errors to 500.
function dispatchWorkflow(res, args, action, payload, user) {
  try {
    const result = runWorkflowApi(args, action, payload, user);
    sendJson(res, result.ok ? 200 : 400, result);
  } catch (error) {
    sendJson(res, 500, { ok: false, error: { code: "server_error", message: error.message } });
  }
}

// Pull the goal id/prefix out of /api/goals/<goal>/... paths.
function goalFromPath(pathname, suffix) {
  const prefix = "/api/goals/";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) return null;
  const raw = pathname.slice(prefix.length, pathname.length - suffix.length);
  if (!raw || raw.includes("/")) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

// Pull the node id/prefix out of /api/learn/<node>/... paths.
function nodeFromPath(pathname, suffix) {
  const prefix = "/api/learn/";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) return null;
  const raw = pathname.slice(prefix.length, pathname.length - suffix.length);
  if (!raw || raw.includes("/")) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

// Pull the session id out of /api/learn/sessions/<session-id>/messages paths.
function sessionFromPath(pathname) {
  const prefix = "/api/learn/sessions/";
  const suffix = "/messages";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) return null;
  const raw = pathname.slice(prefix.length, pathname.length - suffix.length);
  if (!raw || raw.includes("/")) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

// Pull the exam id out of /api/exams/<exam-id><suffix> paths (single-id routes).
function examFromPath(pathname, suffix) {
  const prefix = "/api/exams/";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) return null;
  const raw = pathname.slice(prefix.length, pathname.length - suffix.length);
  if (!raw || raw.includes("/")) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

// Pull (exam id, question id) out of
// /api/exams/<exam-id>/questions/<question-id>/<verb> paths (answer | record).
function examQuestionVerbFromPath(pathname, verb) {
  const match = pathname.match(
    new RegExp(`^/api/exams/([^/]+)/questions/([^/]+)/${verb}$`)
  );
  if (!match) return null;
  try {
    return { examId: decodeURIComponent(match[1]), questionId: decodeURIComponent(match[2]) };
  } catch {
    return null;
  }
}

// Pull the review id out of /api/review/<review-id>/finish paths.
function reviewFromPath(pathname, suffix) {
  const prefix = "/api/review/";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) return null;
  const raw = pathname.slice(prefix.length, pathname.length - suffix.length);
  if (!raw || raw.includes("/")) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

// GET /api/* endpoints whose action name matches the trailing path segment.
const GET_API_ACTIONS = {
  "/api/health": "health",
  "/api/goals": "goals",
  "/api/graph": "graph",
  "/api/review/queue": "review_queue",
};

function createServer(graph, root, args) {
  return http.createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (url.pathname === "/") {
      sendFile(res, path.join(root, "index.html"));
      return;
    }

    if (url.pathname === "/graph.json") {
      res.writeHead(200, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      });
      res.end(JSON.stringify(graph, null, 2));
      return;
    }

    if (req.method === "GET" && Object.prototype.hasOwnProperty.call(GET_API_ACTIONS, url.pathname)) {
      const user = url.searchParams.get("user") || args.user;
      dispatchWorkflow(res, args, GET_API_ACTIONS[url.pathname], queryPayload(url), user);
      return;
    }

    // POST write endpoints: parse the JSON body, inject path params, proxy to Python.
    if (req.method === "POST" && url.pathname.startsWith("/api/")) {
      handlePost(req, res, url, args);
      return;
    }

    const filePath = resolveStaticPath(root, url.pathname);
    if (filePath && filePath.startsWith(root + path.sep)) {
      sendFile(res, filePath, "public, max-age=31536000, immutable");
      return;
    }

    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Not found");
  });
}

async function handlePost(req, res, url, args) {
  let body;
  try {
    const raw = await readRequestBody(req);
    body = raw.trim() ? JSON.parse(raw) : {};
    if (typeof body !== "object" || body === null || Array.isArray(body)) {
      throw new Error("JSON body must be an object");
    }
  } catch (error) {
    sendJson(res, 400, { ok: false, error: { code: "invalid_json", message: error.message } });
    return;
  }

  const assessStartGoal = goalFromPath(url.pathname, "/assessment/start");
  if (assessStartGoal !== null) {
    const payload = { ...body, goal: assessStartGoal };
    dispatchWorkflow(res, args, "assessment_start", payload, payload.user || args.user);
    return;
  }

  const assessAnswerGoal = goalFromPath(url.pathname, "/assessment/answer");
  if (assessAnswerGoal !== null) {
    const payload = { ...body, goal: assessAnswerGoal };
    dispatchWorkflow(res, args, "assessment_answer", payload, payload.user || args.user);
    return;
  }

  // Learn: send one Socratic turn (checked before /prepare so the longer,
  // /sessions/<id>/messages path is not misread as a node prefix).
  const learnSession = sessionFromPath(url.pathname);
  if (learnSession !== null) {
    const payload = { ...body, session_id: learnSession };
    dispatchWorkflow(res, args, "learn_message", payload, payload.user || args.user);
    return;
  }

  // Learn: generate/reuse outline + create/resume session for a node.
  const learnNode = nodeFromPath(url.pathname, "/prepare");
  if (learnNode !== null) {
    const payload = { ...body, node: learnNode };
    dispatchWorkflow(res, args, "learn_prepare", payload, payload.user || args.user);
    return;
  }

  // Exam: create an exam attempt for a node.
  if (url.pathname === "/api/exams/start") {
    dispatchWorkflow(res, args, "exam_start", body, body.user || args.user);
    return;
  }

  // Exam (data plane): persist one raw answer without scoring.
  const examRecord = examQuestionVerbFromPath(url.pathname, "record");
  if (examRecord !== null) {
    const payload = { ...body, exam_id: examRecord.examId, question_id: examRecord.questionId };
    dispatchWorkflow(res, args, "exam_record_answer", payload, payload.user || args.user);
    return;
  }

  // Exam: score one answer (checked before /finish; this is the deeper path).
  const examQuestion = examQuestionVerbFromPath(url.pathname, "answer");
  if (examQuestion !== null) {
    const payload = { ...body, exam_id: examQuestion.examId, question_id: examQuestion.questionId };
    dispatchWorkflow(res, args, "exam_answer", payload, payload.user || args.user);
    return;
  }

  // Exam: finalize → write state, review schedule (on pass) and error notebook.
  const examFinish = examFromPath(url.pathname, "/finish");
  if (examFinish !== null) {
    const payload = { ...body, exam_id: examFinish };
    dispatchWorkflow(res, args, "exam_finish", payload, payload.user || args.user);
    return;
  }

  // Exam (data plane): load a tool-generated exam for display + answering.
  // Checked after the deeper /questions, /answer, /record, /finish routes so a
  // bare exam id never shadows them.
  const examGet = examFromPath(url.pathname, "");
  if (examGet !== null && examGet !== "start") {
    const payload = { ...body, exam_id: examGet };
    dispatchWorkflow(res, args, "exam_get", payload, payload.user || args.user);
    return;
  }

  // Review: assemble the review context (errors + mnemonic) before the re-exam.
  if (url.pathname === "/api/review/start") {
    dispatchWorkflow(res, args, "review_start", body, body.user || args.user);
    return;
  }

  // Review: finalize → delegate the exam result, complete the old review,
  // reschedule on fail. Reuses Exam primitives but keeps its own completion.
  const reviewFinish = reviewFromPath(url.pathname, "/finish");
  if (reviewFinish !== null) {
    const payload = { ...body, review_id: reviewFinish };
    dispatchWorkflow(res, args, "review_finish", payload, payload.user || args.user);
    return;
  }

  sendJson(res, 404, { ok: false, error: { code: "unknown_route", message: `No POST route for ${url.pathname}` } });
}

function listenOnAvailablePort(server, host, preferredPort) {
  return new Promise((resolve, reject) => {
    function tryPort(port) {
      if (port > 65535) {
        reject(new Error("No available port found."));
        return;
      }
      const onError = (error) => {
        server.removeListener("listening", onListening);
        if (error.code === "EADDRINUSE" || error.code === "EACCES") {
          tryPort(port + 1);
        } else {
          reject(error);
        }
      };
      const onListening = () => {
        server.removeListener("error", onError);
        resolve(port);
      };
      server.once("error", onError);
      server.once("listening", onListening);
      server.listen(port, host);
    }
    tryPort(preferredPort);
  });
}

// Sidecar file recording the live host URL (with the real, possibly-bumped
// port) so generation skills running in a separate process can build a clickable
// deep link back to this page. It lives next to the resolved database file so
// the reader (src/infrastructure/web_link.py) finds it via DB_PATH. See docs/20.
function sidecarPath(dbPath) {
  return path.join(path.dirname(dbPath), ".web_url");
}

function writeSidecar(url, dbPath) {
  const file = sidecarPath(dbPath);
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, `${url}\n`, "utf8");
  } catch {
    // Non-fatal: deep links fall back to LDG_WEB_URL / localhost in the skill.
  }
  const cleanup = () => {
    try {
      if (fs.existsSync(file)) fs.unlinkSync(file);
    } catch {
      /* ignore */
    }
  };
  process.on("exit", cleanup);
  for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.on(sig, () => {
      cleanup();
      process.exit(0);
    });
  }
}

function openBrowser(url) {
  const platform = process.platform;
  let command;
  let args;
  if (platform === "darwin") {
    command = "open";
    args = [url];
  } else if (platform === "win32") {
    command = "cmd";
    args = ["/c", "start", "", url];
  } else {
    command = "xdg-open";
    args = [url];
  }
  const child = childProcess.spawn(command, args, {
    detached: true,
    stdio: "ignore",
  });
  child.on("error", () => {
    console.log(`Open this URL manually: ${url}`);
  });
  child.unref();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const root = staticRoot();
  ensureWebBuild(root);
  const graph = loadGraph(args);
  const server = createServer(graph, root, args);
  const port = await listenOnAvailablePort(server, args.host, args.port);
  const url = `http://${args.host}:${port}`;
  console.log(`Loaded ${graph.summary.node_count} nodes and ${graph.summary.edge_count} edges from ${graph.db_path}`);
  console.log(`Goal: ${graph.goal.title} (${graph.goal.id.slice(0, 8)})`);
  console.log(`Listening on ${url}`);
  writeSidecar(url, graph.db_path);
  console.log("Stop with Ctrl+C");
  if (!args.noOpen) {
    try {
      openBrowser(url);
    } catch (error) {
      console.log(`Open this URL manually: ${url}`);
    }
  }
}

main().catch((error) => {
  console.error(`error: ${error.message}`);
  process.exit(1);
});
