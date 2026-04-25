import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Loader2 } from "lucide-react";

const API_BASE_URL = "http://localhost:8000";

// ---------- Latency helpers ----------
function formatLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function LatencyPill({ ms }: { ms: number }) {
  let cls = "bg-emerald-500/15 text-emerald-600 border-emerald-500/30";
  if (ms >= 5000) cls = "bg-red-500/15 text-red-600 border-red-500/30";
  else if (ms >= 1000) cls = "bg-amber-500/15 text-amber-600 border-amber-500/30";
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${cls}`}>
      {formatLatency(ms)}
    </span>
  );
}

// ---------- LLM call ----------
type LlmResult = { response: string; model: string; latencyMs: number };

async function callLlm(text: string): Promise<LlmResult> {
  const t0 = performance.now();
  const res = await fetch(`${API_BASE_URL}/api/llm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const bodyText = await res.text();
  const t1 = performance.now();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${bodyText || res.statusText}`);
  }
  let parsed: { response: string; model: string };
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    throw new Error(`Invalid JSON response: ${bodyText.slice(0, 300)}`);
  }
  return { response: parsed.response, model: parsed.model, latencyMs: t1 - t0 };
}

// ---------- IndexedDB for directory handle ----------
const DB_NAME = "context-engine";
const STORE = "handles";
const HANDLE_KEY = "data-dir";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbGet<T>(key: string): Promise<T | undefined> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).get(key);
    req.onsuccess = () => resolve(req.result as T | undefined);
    req.onerror = () => reject(req.error);
  });
}

async function idbSet(key: string, value: unknown): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// ---------- File walking ----------
type WalkedFile = { path: string; file: File };

async function walkDirHandle(dir: any, prefix = ""): Promise<WalkedFile[]> {
  const out: WalkedFile[] = [];
  for await (const [name, handle] of dir.entries()) {
    const path = prefix ? `${prefix}/${name}` : name;
    if (handle.kind === "file") {
      const file: File = await handle.getFile();
      out.push({ path, file });
    } else if (handle.kind === "directory") {
      out.push(...(await walkDirHandle(handle, path)));
    }
  }
  return out;
}

function filesFromInput(list: FileList): WalkedFile[] {
  const out: WalkedFile[] = [];
  for (let i = 0; i < list.length; i++) {
    const f = list[i];
    const rel: string = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
    out.push({ path: rel, file: f });
  }
  return out;
}

// ---------- PDF.js loader ----------
let pdfjsPromise: Promise<any> | null = null;
function loadPdfJs(): Promise<any> {
  if (!pdfjsPromise) {
    const url = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.min.mjs";
    pdfjsPromise = (new Function("u", "return import(u)") as (u: string) => Promise<any>)(url).then(
      (mod: any) => {
        try {
          mod.GlobalWorkerOptions.workerSrc =
            "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.mjs";
        } catch {
          // ignore
        }
        return mod;
      }
    );
  }
  return pdfjsPromise;
}

async function extractText(file: File, path: string): Promise<string> {
  const ext = (path.split(".").pop() || "").toLowerCase();
  const textLike = ["txt", "csv", "json", "md", "xml", "eml"];
  if (textLike.includes(ext)) {
    return await file.text();
  }
  if (ext === "pdf") {
    const pdfjs = await loadPdfJs();
    const buf = await file.arrayBuffer();
    const doc = await pdfjs.getDocument({ data: buf }).promise;
    let full = "";
    for (let i = 1; i <= doc.numPages; i++) {
      const page = await doc.getPage(i);
      const content = await page.getTextContent();
      full += content.items.map((it: any) => it.str).join(" ") + "\n";
    }
    return full;
  }
  throw new Error(`Unsupported extension: .${ext}`);
}

// ---------- Panel 1: Scan ----------
type ScanRow = {
  path: string;
  size: number;
  extractMs?: number;
  llmMs?: number;
  preview?: string;
  fullResponse?: string;
  error?: string;
};

const SEEN_KEY = "seen_files";

function getSeen(): string[] {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function ScanPanel() {
  const [rows, setRows] = useState<ScanRow[]>([]);
  const [scanning, setScanning] = useState(false);
  const [totalMs, setTotalMs] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const fallbackRef = useRef<HTMLInputElement>(null);
  const supportsFsa = typeof (window as any).showDirectoryPicker === "function";

  async function processFiles(walked: WalkedFile[]) {
    const startedAt = performance.now();
    const seen = new Set(getSeen());
    const currentPaths = walked.map((w) => w.path);
    const newOnes = walked.filter((w) => !seen.has(w.path));

    if (newOnes.length === 0) {
      const total = performance.now() - startedAt;
      setRows([]);
      setTotalMs(total);
      setMessage("No new files since last scan.");
      localStorage.setItem(SEEN_KEY, JSON.stringify(currentPaths));
      return;
    }

    setMessage(null);
    const initial: ScanRow[] = newOnes.map((w) => ({
      path: w.path,
      size: w.file.size,
    }));
    setRows(initial);

    for (let i = 0; i < newOnes.length; i++) {
      const { path, file } = newOnes[i];
      const ext = (path.split(".").pop() || "").toLowerCase();
      const supported = ["txt", "csv", "json", "md", "xml", "eml", "pdf"].includes(ext);
      if (!supported) {
        setRows((prev) => {
          const next = [...prev];
          next[i] = { ...next[i], error: `Skipped: unsupported .${ext}` };
          return next;
        });
        continue;
      }
      const eStart = performance.now();
      let text: string;
      try {
        text = await extractText(file, path);
      } catch (err: any) {
        setRows((prev) => {
          const next = [...prev];
          next[i] = {
            ...next[i],
            extractMs: performance.now() - eStart,
            error: `Extraction failed: ${err?.message || String(err)}`,
          };
          return next;
        });
        continue;
      }
      const extractMs = performance.now() - eStart;
      const truncated = text.slice(0, 50000);
      try {
        const result = await callLlm(truncated);
        setRows((prev) => {
          const next = [...prev];
          next[i] = {
            ...next[i],
            extractMs,
            llmMs: result.latencyMs,
            preview: result.response.slice(0, 200),
            fullResponse: result.response,
          };
          return next;
        });
      } catch (err: any) {
        setRows((prev) => {
          const next = [...prev];
          next[i] = {
            ...next[i],
            extractMs,
            error: err?.message || String(err),
          };
          return next;
        });
      }
    }

    const total = performance.now() - startedAt;
    setTotalMs(total);
    localStorage.setItem(SEEN_KEY, JSON.stringify(currentPaths));
  }

  async function handleScan() {
    setError(null);
    setMessage(null);
    setTotalMs(null);
    setRows([]);
    setExpanded({});
    setScanning(true);
    try {
      if (supportsFsa) {
        let handle: any = await idbGet(HANDLE_KEY);
        if (handle) {
          // verify permission
          try {
            const perm = await handle.queryPermission?.({ mode: "read" });
            if (perm !== "granted") {
              const req = await handle.requestPermission?.({ mode: "read" });
              if (req !== "granted") handle = null;
            }
          } catch {
            handle = null;
          }
        }
        if (!handle) {
          handle = await (window as any).showDirectoryPicker();
          await idbSet(HANDLE_KEY, handle);
        }
        const walked = await walkDirHandle(handle);
        await processFiles(walked);
      } else {
        // trigger fallback picker; processing happens in onChange
        fallbackRef.current?.click();
      }
    } catch (err: any) {
      if (err?.name !== "AbortError") {
        setError(err?.message || String(err));
      }
    } finally {
      if (supportsFsa) setScanning(false);
    }
  }

  async function handleFallbackChange(e: React.ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list || list.length === 0) {
      setScanning(false);
      return;
    }
    try {
      const walked = filesFromInput(list);
      await processFiles(walked);
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setScanning(false);
      e.target.value = "";
    }
  }

  function resetSeen() {
    localStorage.removeItem(SEEN_KEY);
    setMessage("Seen files cleared.");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scan data folder for new files</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col items-start gap-2">
          <Button onClick={handleScan} disabled={scanning}>
            {scanning && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Scan data folder
          </Button>
          {!supportsFsa && (
            <p className="text-xs text-muted-foreground">
              Your browser doesn't support the File System Access API. You'll need to re-pick the
              folder each session.
            </p>
          )}
          <input
            ref={fallbackRef}
            type="file"
            // @ts-expect-error non-standard attribute
            webkitdirectory=""
            directory=""
            multiple
            className="hidden"
            onChange={handleFallbackChange}
          />
          <button
            onClick={resetSeen}
            className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          >
            Reset seen files
          </button>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {totalMs !== null && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Total scan time:</span>
            <LatencyPill ms={totalMs} />
          </div>
        )}

        {message && <p className="text-sm text-muted-foreground">{message}</p>}

        {rows.length > 0 && (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left">
                <tr>
                  <th className="px-3 py-2 font-medium">Filename</th>
                  <th className="px-3 py-2 font-medium">Size</th>
                  <th className="px-3 py-2 font-medium">Extraction</th>
                  <th className="px-3 py-2 font-medium">LLM latency</th>
                  <th className="px-3 py-2 font-medium">Response</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.path} className="border-t align-top">
                    <td className="px-3 py-2 font-mono text-xs">{r.path}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{(r.size / 1024).toFixed(1)} KB</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {r.extractMs !== undefined ? formatLatency(r.extractMs) : "—"}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {r.llmMs !== undefined ? <LatencyPill ms={r.llmMs} /> : "—"}
                    </td>
                    <td className="px-3 py-2">
                      {r.error ? (
                        <span className="text-destructive">{r.error}</span>
                      ) : r.fullResponse ? (
                        <div>
                          <pre className="whitespace-pre-wrap break-words font-sans">
                            {expanded[i] ? r.fullResponse : r.preview}
                            {!expanded[i] && r.fullResponse.length > 200 && "…"}
                          </pre>
                          {r.fullResponse.length > 200 && (
                            <button
                              onClick={() =>
                                setExpanded((prev) => ({ ...prev, [i]: !prev[i] }))
                              }
                              className="mt-1 text-xs text-primary underline-offset-2 hover:underline"
                            >
                              {expanded[i] ? "Collapse" : "Expand"}
                            </button>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">…</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- Panel 2: Free-text ----------
function FreeTextPanel() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<LlmResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSend() {
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const r = await callLlm(text);
      setResult(r);
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Free-text input</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Textarea
          rows={6}
          placeholder="Paste any text and send it straight to the LLM…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <Button onClick={handleSend} disabled={loading}>
          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Send
        </Button>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {result && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <LatencyPill ms={result.latencyMs} />
              <span className="text-muted-foreground">model: {result.model}</span>
            </div>
            <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/30 p-3 text-sm font-sans">
              {result.response}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- Panel 3: Ask ----------
function AskPanel() {
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<LlmResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleAsk() {
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const r = await callLlm(q);
      setResult(r);
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ask a question</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          type="text"
          placeholder="Ask a question about the property…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !loading) handleAsk();
          }}
        />
        <Button onClick={handleAsk} disabled={loading}>
          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Ask
        </Button>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {result && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <LatencyPill ms={result.latencyMs} />
              <span className="text-muted-foreground">model: {result.model}</span>
            </div>
            <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/30 p-3 text-sm font-sans">
              {result.response}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- Page ----------
const Index = () => {
  useEffect(() => {
    document.title = "Context Engine — LLM Console";
  }, []);

  return (
    <main className="min-h-screen bg-background">
      <div className="mx-auto max-w-4xl px-4 py-10">
        <header className="mb-8">
          <h1 className="text-3xl font-semibold tracking-tight">Context Engine — LLM Console</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            API base: <code className="font-mono">{API_BASE_URL}</code>
          </p>
        </header>
        <div className="space-y-6">
          <ScanPanel />
          <FreeTextPanel />
          <AskPanel />
        </div>
      </div>
    </main>
  );
};

export default Index;
