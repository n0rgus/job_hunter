import React from "react";

export default function ScraperLauncher() {
  const [running, setRunning] = React.useState<boolean>(false);
  const [logLines, setLogLines] = React.useState<string[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const [evt, setEvt] = React.useState<EventSource | null>(null);

  async function startScraper() {
    setError(null);
    try {
      const res = await fetch(`/api/scrape/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}), // add {site_id: "DEFAULT"} if you expose it in UI
      });
      if (res.status === 409) {
        setRunning(true);
        attachLogStream();
        return;
      }
      if (!res.ok) throw new Error(await res.text());
      setRunning(true);
      attachLogStream();
    } catch (e: any) {
      setError(e?.message ?? "Failed to start scraper");
    }
  }

  async function refreshStatus() {
    try {
      const res = await fetch(`/api/scrape/status`);
      const data = await res.json();
      setRunning(Boolean(data.running));
    } catch {}
  }

  function attachLogStream() {
    if (evt) return;
    const sse = new EventSource(`/api/scrape/log/stream`);
    sse.onmessage = (ev) => {
      setLogLines((prev) => {
        const next = [...prev, ev.data as string];
        return next.slice(-400); // keep last 400 lines
      });
    };
    sse.onerror = () => {
      // auto-retry occasionally
      setTimeout(() => {
        setEvt(null);
        attachLogStream();
      }, 1500);
    };
    setEvt(sse);
  }

  React.useEffect(() => {
    refreshStatus();
    attachLogStream();
    return () => {
      evt?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="p-4 rounded-2xl shadow bg-white border">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-xl font-semibold">Site Scraper</h3>
          <p className="text-sm text-gray-500">Launch and watch logs live.</p>
        </div>
        <button
          onClick={startScraper}
          disabled={running}
          className={`px-4 py-2 rounded-xl text-white ${running ? "bg-gray-400" : "bg-blue-600 hover:bg-blue-700"}`}
          title={running ? "Scraper is running" : "Start the scraper"}
        >
          {running ? "Running…" : "Start Scraper"}
        </button>
      </div>

      {error && (
        <div className="mt-3 text-red-600 text-sm">{error}</div>
      )}

      <div className="mt-4 h-64 overflow-auto font-mono text-xs bg-black text-green-200 p-3 rounded-xl">
        {logLines.length === 0 ? (
          <div className="opacity-60">Connecting to log stream…</div>
        ) : (
          logLines.map((l, i) => (
            <div key={i} className="whitespace-pre">{l}</div>
          ))
        )}
      </div>
    </div>
  );
}