import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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

type SqlRow = Record<string, unknown>;
type SqlResult = {
  sql: string;
  model: string;
  returns_rows: boolean;
  row_count: number;
  execution_ms: number;
  rows: SqlRow[];
  latencyMs: number;
};

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function runSql(question: string): Promise<SqlResult> {
  const t0 = performance.now();
  const res = await fetch(`${API_BASE_URL}/api/sql`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const bodyText = await res.text();
  const t1 = performance.now();
  if (!res.ok) {
    try {
      const parsed = JSON.parse(bodyText) as {
        detail?: string | { error?: string; sql?: string };
      };
      if (typeof parsed.detail === "string") {
        throw new Error(parsed.detail);
      }
      if (parsed.detail?.error) {
        const sqlBlock = parsed.detail.sql ? `\n\nSQL:\n${parsed.detail.sql}` : "";
        throw new Error(`${parsed.detail.error}${sqlBlock}`);
      }
    } catch (error) {
      if (error instanceof Error) throw error;
    }
    throw new Error(`HTTP ${res.status}: ${bodyText || res.statusText}`);
  }
  let parsed: Omit<SqlResult, "latencyMs">;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    throw new Error(`Invalid JSON response: ${bodyText.slice(0, 300)}`);
  }
  return { ...parsed, latencyMs: t1 - t0 };
}

const Index = () => {
  const [question, setQuestion] = useState(
    "Show the latest 10 invoices with invoice_id, provider_company, gross_amount, and invoice_date.",
  );
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SqlResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "LLM SQL Runner";
  }, []);

  async function handleRun() {
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const r = await runSql(question);
      setResult(r);
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  const columns = result?.rows.length ? Object.keys(result.rows[0]) : [];

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,hsl(17_80%_94%),transparent_35%),linear-gradient(180deg,hsl(34_55%_98%),hsl(0_0%_100%))]">
      <div className="mx-auto max-w-6xl px-4 py-10">
        <header className="mb-8 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-700">
            Natural Language to SQL
          </p>
          <h1 className="text-4xl font-semibold tracking-tight text-slate-950">
            LLM SQL Runner
          </h1>
          <p className="max-w-2xl text-sm text-slate-600">
            The app asks the model for one exact SQL statement, executes it directly, and shows
            the raw result.
          </p>
          <p className="text-sm text-slate-500">
            API base: <code className="font-mono">{API_BASE_URL}</code>
          </p>
        </header>

        <Card className="border-slate-200 shadow-sm">
          <CardHeader>
            <CardTitle>Ask for SQL</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Textarea
              rows={5}
              placeholder="Ask a database question in plain English..."
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <Button onClick={handleRun} disabled={loading || !question.trim()}>
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Generate and execute SQL
            </Button>

            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}
          </CardContent>
        </Card>

        {result && (
          <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr),minmax(0,1.4fr)]">
            <Card className="border-slate-200 shadow-sm">
              <CardHeader>
                <CardTitle>Generated SQL</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <LatencyPill ms={result.latencyMs} />
                  <span className="text-muted-foreground">
                    db execution: {formatLatency(result.execution_ms)}
                  </span>
                  <span className="text-muted-foreground">model: {result.model}</span>
                </div>
                <pre className="overflow-x-auto rounded-md border bg-slate-950 p-4 text-sm text-slate-50">
                  <code>{result.sql}</code>
                </pre>
              </CardContent>
            </Card>

            <Card className="border-slate-200 shadow-sm">
              <CardHeader>
                <CardTitle>
                  {result.returns_rows
                    ? `Query Result (${result.row_count} rows)`
                    : `Statement Result (${result.row_count} affected)`}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {result.returns_rows ? (
                  result.rows.length > 0 ? (
                    <div className="overflow-x-auto rounded-md border">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/40 text-left">
                          <tr>
                            {columns.map((column) => (
                              <th key={column} className="px-3 py-2 font-medium">
                                {column}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {result.rows.map((row, index) => (
                            <tr key={index} className="border-t align-top">
                              {columns.map((column) => (
                                <td
                                  key={`${index}-${column}`}
                                  className="max-w-xs px-3 py-2 font-mono text-xs"
                                >
                                  {formatCellValue(row[column])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">Query ran successfully with no rows returned.</p>
                  )
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Statement executed successfully. Rows affected: <strong>{result.row_count}</strong>
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </main>
  );
};

export default Index;
