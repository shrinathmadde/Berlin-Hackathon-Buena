import { useEffect, useState } from "react";
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

// ---------- Panel 1: Scan (server-side) ----------
type ScanResultItem = {
  path: string;
  size_bytes: number;
  extraction_ms: number;
  llm_ms: number;
  llm_response?: string | null;
  error?: string | null;
};

type ScanResponse = {
  total_ms: number;
  data_dir: string;
  files_seen: number;
  new_or_changed: number;
  processed: number;
  baselined: boolean;
  results: ScanResultItem[];
};

function ScanPanel() {
  const [response, setResponse] = useState<ScanResponse | null>(null);
  const [scanning, setScanning] = useState(false);
  const [clientMs, setClientMs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetMessage, setResetMessage] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  async function handleScan() {
    setError(null);
    setResponse(null);
    setClientMs(null);
    setResetMessage(null);
    setExpanded({});
    setScanning(true);
    const t0 = performance.now();
    try {
      const res = await fetch(`${API_BASE_URL}/api/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const bodyText = await res.text();
      const t1 = performance.now();
      setClientMs(t1 - t0);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${bodyText || res.statusText}`);
      }
      setResponse(JSON.parse(bodyText) as ScanResponse);
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setScanning(false);
    }
  }

  async function handleReset() {
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/scan/state`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setResetMessage("Server scan state cleared. The next scan will baseline all files.");
      setResponse(null);
    } catch (err: any) {
      setError(err?.message || String(err));
    }
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
          <p className="text-xs text-muted-foreground">
            Server walks the <code className="font-mono">data/</code> folder, diffs against the
            last scan, extracts text from any new or modified file, and routes each through the
            LLM endpoint. First scan only baselines mtimes (no LLM calls).
          </p>
          <button
            onClick={handleReset}
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

        {resetMessage && (
          <p className="text-sm text-muted-foreground">{resetMessage}</p>
        )}

        {response && (
          <div className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span>
                <span className="text-muted-foreground">Server total:</span>{" "}
                <LatencyPill ms={response.total_ms} />
              </span>
              {clientMs !== null && (
                <span>
                  <span className="text-muted-foreground">Round-trip:</span>{" "}
                  <LatencyPill ms={clientMs} />
                </span>
              )}
              <span className="text-muted-foreground">
                files seen <strong>{response.files_seen}</strong> · new/changed{" "}
                <strong>{response.new_or_changed}</strong> · processed{" "}
                <strong>{response.processed}</strong>
                {response.baselined && " · (baselined first scan)"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              data dir: <code className="font-mono">{response.data_dir}</code>
            </p>
          </div>
        )}

        {response && response.baselined && response.results.length === 0 && (
          <p className="text-sm text-muted-foreground">
            First scan completed. {response.files_seen} files recorded as the baseline. Drop a
            new file into <code className="font-mono">data/</code> and scan again — only the
            change will be processed.
          </p>
        )}

        {response && !response.baselined && response.results.length === 0 && (
          <p className="text-sm text-muted-foreground">No new files since last scan.</p>
        )}

        {response && response.results.length > 0 && (
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
                {response.results.map((r, i) => (
                  <tr key={r.path} className="border-t align-top">
                    <td className="px-3 py-2 font-mono text-xs">{r.path}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {(r.size_bytes / 1024).toFixed(1)} KB
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatLatency(r.extraction_ms)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {r.error ? "—" : <LatencyPill ms={r.llm_ms} />}
                    </td>
                    <td className="px-3 py-2">
                      {r.error ? (
                        <span className="text-destructive">{r.error}</span>
                      ) : r.llm_response ? (
                        <div>
                          <pre className="whitespace-pre-wrap break-words font-sans">
                            {expanded[i]
                              ? r.llm_response
                              : r.llm_response.slice(0, 200)}
                            {!expanded[i] && r.llm_response.length > 200 && "…"}
                          </pre>
                          {r.llm_response.length > 200 && (
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
