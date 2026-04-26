import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Loader2 } from "lucide-react";

const API_BASE_URL = "http://localhost:8000";
const FILE_PREVIEW_LIMIT = 50000;
const MODEL_DISPLAY_NAMES: Record<string, string> = {
  "gpt-5.5": "gemini 3.1 pro",
  "eaf2d9b9-04b9-411f-a7cd-7e202c4270cc": "qwen3 8b",
};

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

function formatModelName(model?: string | null): string {
  if (!model) return "";
  return MODEL_DISPLAY_NAMES[model] ?? model;
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
type ExtractOperation = {
  table: string;
  record: Record<string, unknown>;
};
type DocumentProcessResult = {
  model: string;
  row_count: number;
  execution_ms: number;
  latency_ms?: number;
  raw_model_output?: string | null;
  extraction?: {
    summary: string;
    records: ExtractOperation[];
  } | null;
  writes: {
    table: string;
    primary_key: string;
    status: "created" | "updated";
  }[];
  comparisons?: {
    label: string;
    model?: string | null;
    latency_ms: number;
    raw_model_output?: string | null;
    extraction?: {
      summary: string;
      records: ExtractOperation[];
    } | null;
    error?: string | null;
  }[];
  latencyMs: number;
};

type DocumentProcessApiResult = Omit<DocumentProcessResult, "latencyMs">;

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return fallback;
}

function getModelSpecificLatencyMs(
  result: DocumentProcessApiResult,
  requestLatencyMs: number,
): number {
  const resultModel = formatModelName(result.model).toLowerCase();
  const matchingComparison = result.comparisons?.find((comparison) => {
    const comparisonModel = formatModelName(comparison.model || comparison.label).toLowerCase();
    const comparisonLabel = formatModelName(comparison.label).toLowerCase();

    return comparisonModel === resultModel || comparisonLabel === resultModel;
  });

  return matchingComparison?.latency_ms ?? result.latency_ms ?? requestLatencyMs;
}

const SQL_LINE_KEYWORDS = [
  "LEFT JOIN",
  "RIGHT JOIN",
  "INNER JOIN",
  "FULL JOIN",
  "OUTER JOIN",
  "GROUP BY",
  "ORDER BY",
  "SELECT",
  "INSERT",
  "UPDATE",
  "DELETE",
  "FROM",
  "WHERE",
  "HAVING",
  "VALUES",
  "RETURNING",
  "LIMIT",
  "OFFSET",
  "UNION",
  "JOIN",
  "SET",
  "AND",
  "OR",
];

function isSqlIdentifierChar(char: string | undefined): boolean {
  return !!char && /[A-Z0-9_]/.test(char.toUpperCase());
}

function matchesSqlKeyword(text: string, index: number, keyword: string): boolean {
  const before = text[index - 1];
  const after = text[index + keyword.length];

  return (
    text.slice(index, index + keyword.length).toUpperCase() === keyword &&
    !isSqlIdentifierChar(before) &&
    !isSqlIdentifierChar(after)
  );
}

function normalizeSqlWhitespace(sql: string): string {
  let formatted = "";
  let inString = false;
  let pendingSpace = false;

  for (let index = 0; index < sql.length; index += 1) {
    const char = sql[index];
    const next = sql[index + 1];

    if (char === "'") {
      if (pendingSpace && formatted) formatted += " ";
      pendingSpace = false;
      formatted += char;

      if (inString && next === "'") {
        formatted += next;
        index += 1;
      } else {
        inString = !inString;
      }
      continue;
    }

    if (!inString && /\s/.test(char)) {
      pendingSpace = true;
      continue;
    }

    if (pendingSpace && formatted && !formatted.endsWith("(")) {
      formatted += " ";
    }
    pendingSpace = false;
    formatted += char;
  }

  return formatted.trim();
}

function formatSqlQuery(sql: string): string {
  const normalizedSql = normalizeSqlWhitespace(sql);
  let formatted = "";
  let inString = false;

  for (let index = 0; index < normalizedSql.length; index += 1) {
    const char = normalizedSql[index];
    const next = normalizedSql[index + 1];

    if (char === "'") {
      formatted += char;
      if (inString && next === "'") {
        formatted += next;
        index += 1;
      } else {
        inString = !inString;
      }
      continue;
    }

    if (!inString) {
      const keyword = SQL_LINE_KEYWORDS.find((candidate) =>
        matchesSqlKeyword(normalizedSql, index, candidate),
      );

      if (keyword) {
        formatted = formatted.trimEnd();
        if (formatted) formatted += "\n";
        formatted += keyword;
        index += keyword.length - 1;
        continue;
      }
    }

    formatted += char;
  }

  return formatted
    .split("\n")
    .map((line) => {
      const trimmedLine = line.trim();
      if (/^(AND|OR)\b/i.test(trimmedLine)) return `   ${trimmedLine}`;
      return trimmedLine;
    })
    .filter(Boolean)
    .join("\n");
}

function parseApiError(status: number, statusText: string, bodyText: string): Error {
  try {
    const parsed = JSON.parse(bodyText) as {
      detail?: string | { error?: string; sql?: string };
    };
    if (typeof parsed.detail === "string") {
      return new Error(parsed.detail);
    }
    if (parsed.detail?.error) {
      const sqlBlock = parsed.detail.sql ? `\n\nSQL:\n${parsed.detail.sql}` : "";
      return new Error(`${parsed.detail.error}${sqlBlock}`);
    }
  } catch {
    // Fall through to a generic HTTP error when the backend did not return JSON.
  }
  return new Error(`HTTP ${status}: ${bodyText || statusText}`);
}

function RawModelOutput({
  value,
  fallback,
}: {
  value?: string | null;
  fallback?: unknown;
}) {
  const records = extractRecords(value) ?? extractRecords(fallback);
  const output = records === undefined ? "" : JSON.stringify(records, null, 2);

  if (!output) {
    return <p className="text-sm text-muted-foreground">No model output was returned.</p>;
  }

  return (
    <pre className="max-h-[36rem] overflow-auto whitespace-pre-wrap rounded-md border bg-slate-950 p-4 text-sm text-slate-50">
      <code>{output}</code>
    </pre>
  );
}

function extractRecords(value: unknown): unknown[] | undefined {
  if (!value) return undefined;

  if (typeof value === "string") {
    const text = stripJsonFence(value.trim());
    if (!text) return undefined;
    try {
      return extractRecords(JSON.parse(text));
    } catch {
      return undefined;
    }
  }

  if (typeof value === "object" && "records" in value) {
    const records = (value as { records?: unknown }).records;
    return Array.isArray(records) ? records : undefined;
  }

  return undefined;
}

function stripJsonFence(text: string): string {
  if (!text.startsWith("```")) return text;

  const lines = text.split(/\r?\n/);
  if (lines[0]?.startsWith("```")) lines.shift();
  if (lines[lines.length - 1]?.trim() === "```") lines.pop();
  return lines.join("\n").trim();
}

async function askPropertyQuestion(question: string): Promise<SqlResult> {
  const t0 = performance.now();
  const res = await fetch(`${API_BASE_URL}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const bodyText = await res.text();
  const t1 = performance.now();
  if (!res.ok) {
    throw parseApiError(res.status, res.statusText, bodyText);
  }
  let parsed: Omit<SqlResult, "latencyMs">;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    throw new Error(`Invalid JSON response: ${bodyText.slice(0, 300)}`);
  }
  return { ...parsed, latencyMs: t1 - t0 };
}

async function processFile(file: File): Promise<DocumentProcessResult> {
  const formData = new FormData();
  formData.append("file", file);

  const t0 = performance.now();
  const res = await fetch(`${API_BASE_URL}/api/process-file`, {
    method: "POST",
    body: formData,
  });
  const bodyText = await res.text();
  const t1 = performance.now();
  if (!res.ok) {
    throw parseApiError(res.status, res.statusText, bodyText);
  }
  let parsed: DocumentProcessApiResult;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    throw new Error(`Invalid JSON response: ${bodyText.slice(0, 300)}`);
  }
  return { ...parsed, latencyMs: getModelSpecificLatencyMs(parsed, t1 - t0) };
}

const Index = () => {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [filePreview, setFilePreview] = useState<string | null>(null);
  const [filePreviewLoading, setFilePreviewLoading] = useState(false);
  const [filePreviewError, setFilePreviewError] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileResult, setFileResult] = useState<DocumentProcessResult | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [question, setQuestion] = useState(
    "Show the latest 10 invoices with invoice_id, provider_company, gross_amount, and invoice_date.",
  );
  const [questionLoading, setQuestionLoading] = useState(false);
  const [result, setResult] = useState<SqlResult | null>(null);
  const [questionError, setQuestionError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Property AI Console";
  }, []);

  async function handleProcessFile() {
    if (!selectedFile) return;
    setFileError(null);
    setFileResult(null);
    setFileLoading(true);
    try {
      const r = await processFile(selectedFile);
      setFileResult(r);
    } catch (err: unknown) {
      setFileError(getErrorMessage(err, String(err)));
    } finally {
      setFileLoading(false);
    }
  }

  async function handleSelectedFileChange(file: File | null) {
    setSelectedFile(file);
    setFileResult(null);
    setFileError(null);
    setFilePreview(null);
    setFilePreviewError(null);

    if (!file) return;

    setFilePreviewLoading(true);
    try {
      const text = await file.text();
      const suffix = text.length > FILE_PREVIEW_LIMIT
        ? `\n\n[Preview truncated after ${FILE_PREVIEW_LIMIT} characters]`
        : "";
      setFilePreview(`${text.slice(0, FILE_PREVIEW_LIMIT)}${suffix}`);
    } catch (err: unknown) {
      setFilePreviewError(getErrorMessage(err, "Could not read file content."));
    } finally {
      setFilePreviewLoading(false);
    }
  }

  async function handleAskQuestion() {
    setQuestionError(null);
    setResult(null);
    setQuestionLoading(true);
    try {
      const r = await askPropertyQuestion(question);
      setResult(r);
    } catch (err: unknown) {
      setQuestionError(getErrorMessage(err, String(err)));
    } finally {
      setQuestionLoading(false);
    }
  }

  const columns = result?.rows.length ? Object.keys(result.rows[0]) : [];

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,hsl(17_80%_94%),transparent_35%),linear-gradient(180deg,hsl(34_55%_98%),hsl(0_0%_100%))]">
      <div className="mx-auto max-w-6xl px-4 py-10">
        <header className="mb-8 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-700">
            Property AI Console
          </p>
          <h1 className="text-4xl font-semibold tracking-tight text-slate-950">
            Ask the context engine
          </h1>
          <p className="max-w-2xl text-sm text-slate-600">
            Upload a property document for extraction, or ask a property question that is
            translated into executable SQL and run against the context database.
          </p>
          <p className="text-sm text-slate-500">
            API base: <code className="font-mono">{API_BASE_URL}</code>
          </p>
        </header>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card className="border-slate-200 shadow-sm">
            <CardHeader>
              <CardTitle>Process a file</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <input
                type="file"
                className="block w-full cursor-pointer rounded-md border border-input bg-background text-sm text-slate-700 file:mr-4 file:border-0 file:bg-slate-950 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-slate-800"
                onChange={(e) => {
                  void handleSelectedFileChange(e.target.files?.[0] ?? null);
                }}
              />
              <p className="text-sm text-muted-foreground">
                Upload PDFs, CSVs, emails, XML, JSON, Markdown, or other text-like files. The backend
                extracts text and runs the document extraction pipeline.
              </p>
              <Button onClick={handleProcessFile} disabled={fileLoading || !selectedFile}>
                {fileLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Process file
              </Button>

              {(filePreviewLoading || filePreviewError || filePreview !== null) && (
                <div className="space-y-2">
                  <p className="text-sm font-medium text-slate-950">File content</p>
                  {filePreviewLoading ? (
                    <div className="flex items-center gap-2 rounded-md border bg-white p-3 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Reading file...
                    </div>
                  ) : filePreviewError ? (
                    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                      {filePreviewError}
                    </div>
                  ) : (
                    <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border bg-slate-950 p-4 text-xs text-slate-50">
                      <code>{filePreview}</code>
                    </pre>
                  )}
                </div>
              )}

              {fileError && (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                  {fileError}
                </div>
              )}

              {fileResult && (
                <div className="space-y-3 rounded-md border bg-white/70 p-4">
                  <div className="flex flex-wrap items-center gap-3 text-sm">
                    <LatencyPill ms={fileResult.latencyMs} />
                    <span className="text-muted-foreground">
                      db execution: {formatLatency(fileResult.execution_ms)}
                    </span>
                    <span className="text-muted-foreground">
                      model: {formatModelName(fileResult.model)}
                    </span>
                  </div>
                  <RawModelOutput
                    value={fileResult.raw_model_output}
                    fallback={fileResult.extraction}
                  />
                </div>
              )}
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card className="border-slate-200 shadow-sm">
              <CardHeader>
                <CardTitle>Ask a question</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Textarea
                  rows={7}
                  placeholder="Ask a question about the property..."
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                />
                <Button onClick={handleAskQuestion} disabled={questionLoading || !question.trim()}>
                  {questionLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Ask
                </Button>

                {questionError && (
                  <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                    {questionError}
                  </div>
                )}
              </CardContent>
            </Card>

            {result && (
              <>
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
                      <span className="text-muted-foreground">
                        model: {formatModelName(result.model)}
                      </span>
                    </div>
                    <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md border bg-slate-950 p-4 text-sm text-slate-50">
                      <code>{formatSqlQuery(result.sql)}</code>
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
              </>
            )}
          </div>
        </div>

        {!!fileResult?.comparisons?.length && (
          <Card className="mt-6 border-slate-200 shadow-sm">
            <CardHeader>
              <CardTitle>Process file comparison</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 xl:grid-cols-2">
                {fileResult.comparisons.map((comparison) => (
                  <div key={comparison.label} className="space-y-3 rounded-md border bg-white p-3">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium text-slate-950">
                        {formatModelName(comparison.model || comparison.label)}
                      </span>
                      {comparison.model &&
                        formatModelName(comparison.model) !== formatModelName(comparison.label) && (
                        <span className="text-muted-foreground">
                          model: {formatModelName(comparison.model)}
                        </span>
                      )}
                      <LatencyPill ms={comparison.latency_ms} />
                    </div>
                    {comparison.error ? (
                      <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                        {comparison.error}
                      </div>
                    ) : (
                      <RawModelOutput
                        value={comparison.raw_model_output}
                        fallback={comparison.extraction}
                      />
                    )}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </main>
  );
};

export default Index;
