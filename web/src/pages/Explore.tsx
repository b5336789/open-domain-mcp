import { useState } from "react";
import { api, SearchResult } from "../api";

export default function Explore() {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [language, setLanguage] = useState("");
  const [source, setSource] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searched, setSearched] = useState(false);

  async function run() {
    if (!query) return;
    const res = await api.search(query, 8, {
      kind: kind || null,
      language: language || null,
      source_contains: source || null,
    });
    setResults(res);
    setSearched(true);
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">Explore</h2>

      <div className="flex gap-2">
        <input
          className="flex-1 rounded border px-3 py-2"
          placeholder="Ask about the indexed knowledge…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <select
          className="rounded border px-2"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          <option value="">all</option>
          <option value="code">code</option>
          <option value="text">text</option>
        </select>
        <button onClick={run} className="rounded bg-slate-900 px-4 py-2 text-white">
          Search
        </button>
      </div>

      <div className="flex gap-2 text-sm">
        <input
          className="rounded border px-3 py-1"
          placeholder="language (e.g. python)"
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
        />
        <input
          className="flex-1 rounded border px-3 py-1"
          placeholder="source contains…"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
        <span className="self-center text-xs text-slate-400">hybrid search (dense + BM25)</span>
      </div>

      {searched && results.length === 0 && <p className="text-slate-500">No results.</p>}

      <div className="space-y-4">
        {results.map((r) => (
          <article key={r.id} className="rounded-lg border bg-white p-4">
            <header className="flex justify-between text-sm text-slate-500">
              <span className="font-mono">
                {r.metadata.source}
                {r.metadata.symbol ? `::${r.metadata.symbol}` : ""}
              </span>
              <span>score {r.score.toFixed(3)}</span>
            </header>
            {r.metadata.summary && (
              <p className="mt-2 text-sm text-slate-700">{r.metadata.summary}</p>
            )}
            {r.metadata.concepts && (
              <div className="mt-2 flex flex-wrap gap-1">
                {r.metadata.concepts.split(",").map((c) => (
                  <span key={c} className="rounded bg-slate-100 px-2 py-0.5 text-xs">
                    {c.trim()}
                  </span>
                ))}
              </div>
            )}
            <pre className="mt-3 max-h-48 overflow-auto rounded bg-slate-50 p-3 text-xs whitespace-pre-wrap">
              {r.text}
            </pre>
          </article>
        ))}
      </div>
    </div>
  );
}
