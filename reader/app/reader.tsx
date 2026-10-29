"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { nodeId } from "@/lib/reader-contract";
import { paperKey } from "@/lib/discovery";
import {
  cacheKey,
  progressKey,
  persistentEntries,
  liveEntry,
  routeFrom,
  routeUrl,
} from "@/lib/reader-session";
const labels: Record<string, string> = {
  coarse: "粗读",
  deep: "精读",
  translation: "中文翻译",
  explain: "通俗解释",
  practice: "实践",
  like: "喜欢",
  neutral: "无感",
  read: "已读",
};
const feedback = new Set(["like", "neutral", "read"]);
function plain(s: string) {
  return s
    .replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, (_, label: string) =>
      /^(原始摘要|期刊原页|原文|来源|出处|论文原页|原始论文)$/.test(
        label.trim(),
      )
        ? ""
        : label,
    )
    .trim();
}
function jsonGet(url: string): Promise<any> {
  return fetch(url, { cache: "no-store" }).then(async (r) => {
    const d: any = await r.json();
    if (!r.ok) throw Error(d.error || "暂时无法连接");
    return d;
  });
}
function html(value: string) {
  return <div dangerouslySetInnerHTML={{ __html: value }} />;
}

export default function Reader({
  initialDigest = null,
  initialReading = null,
  requestedId = "",
  requestedPaper = 0,
  initialOverview = null,
  requestedView = "",
  englishHtml = "",
  documentId = "",
}: {
  initialDigest?: any;
  initialReading?: any;
  requestedId?: string;
  requestedPaper?: number;
  initialOverview?: any;
  requestedView?: string;
  englishHtml?: string;
  documentId?: string;
}) {
  const firstRoute = requestedId
    ? "read:" + requestedId
    : requestedPaper
      ? "paper:" +
        (initialOverview?.date || initialDigest.date) +
        ":" +
        requestedPaper +
        (initialOverview?.batch ? ":" + initialOverview.batch : "")
      : requestedView ||
        (initialDigest?.batch_id ? "batch:" + initialDigest.batch_id : "today");
  const [digest, setDigest] = useState<any>(initialDigest),
    [route, setRoute] = useState(firstRoute),
    [revision, setRevision] = useState(0),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false),
    [resume, setResume] = useState<any>(null);
  const [batches, setBatches] = useState<any[]>([]),
    [researches, setResearches] = useState<any[]>([]),
    [historyLoaded, setHistoryLoaded] = useState(false),
    [historyError, setHistoryError] = useState(false),
    [papers, setPapers] = useState<any[]>([]),
    [topic, setTopic] = useState(""),
    [finding, setFinding] = useState<any>(null),
    [showHistory, setShowHistory] = useState(false),
    [shelfFilter, setShelfFilter] = useState("saved");
  const cache = useRef<Record<string, any>>(
    initialReading
      ? { [initialReading.node.id]: { ...initialReading, savedAt: Date.now() } }
      : {},
  );
  const discoveryOrigin = useRef("");
  const positions = useRef<Record<string, number>>({}),
    routeRef = useRef(route),
    lastRead = useRef(""),
    restoring = useRef(true),
    busyRef = useRef(false),
    inflight = useRef<Record<string, Promise<any>>>({}),
    requests = useRef<Record<string, string>>({});
  routeRef.current = route;
  const active = route.startsWith("read:")
    ? liveEntry(cache.current[route.slice(5)])
    : null;
  const intro = /^paper:(\d{4}-\d{2}-\d{2}):([1-5])(?::([a-f0-9]{32}))?$/.exec(
    route,
  );
  const date = active?.node.paper_date || intro?.[1] || digest?.date;
  const paper = active?.node.paper_no || Number(intro?.[2] || 0);
  const batch = active
    ? active.node.batch || ""
    : intro
      ? intro[3] || ""
      : digest?.batch_id || "";
  const card =
    active?.card ||
    (date === digest?.date && (digest?.batch_id || "") === batch && paper
      ? digest.cards[paper - 1]
      : date === initialOverview?.date &&
          paper === initialOverview?.paper &&
          (initialOverview.batch || "") === batch
        ? initialOverview.card
        : Object.values(cache.current).find(
            (e: any) =>
              e.node.paper_date === date &&
              e.node.paper_no === paper &&
              (e.node.batch || "") === batch,
          )?.card);
  const node = active?.node;
  const isList =
    route === "today" || route.startsWith("batch:") || route.startsWith("day:");
  const listReady =
    !!digest &&
    (route === "today"
      ? !digest.batch_id
      : route.startsWith("batch:")
        ? digest.batch_id === route.slice(6)
        : route.startsWith("day:")
          ? digest.date === route.slice(4) && !digest.batch_id
          : false);
  const homeRoute = digest?.batch_id ? "batch:" + digest.batch_id : "today";
  const overview = (day: string, no: number, edition = "") =>
    "paper:" + day + ":" + no + (edition ? ":" + edition : "");

  function persist() {
    try {
      localStorage.setItem(
        cacheKey,
        JSON.stringify(persistentEntries(cache.current)),
      );
    } catch {
      /* Memory remains available when browser storage is restricted. */
    }
  }
  function savePosition() {
    if (restoring.current) return;
    positions.current[routeRef.current] = Math.max(0, window.scrollY);
    if (/^(read:|paper:)/.test(routeRef.current))
      lastRead.current = routeRef.current;
    try {
      localStorage.setItem(
        progressKey,
        JSON.stringify({
          positions: positions.current,
          last: lastRead.current,
          at: Date.now(),
        }),
      );
    } catch {}
  }
  function navigate(next: string, replace = false) {
    if (next === routeRef.current) return;
    savePosition();
    restoring.current = true;
    setError("");
    setToast("");
    const depth = Number(history.state?.lcReaderDepth || 0);
    history[replace ? "replaceState" : "pushState"](
      { ...history.state, lcReaderDepth: replace ? depth : depth + 1 },
      "",
      routeUrl(next),
    );
    routeRef.current = next;
    setRoute(next);
  }
  function back() {
    if (Number(history.state?.lcReaderDepth || 0) > 0) history.back();
    else navigate("today");
  }
  function storeEntry(data: any) {
    const value = liveEntry(data);
    if (!value) return;
    const old = cache.current[value.node.id];
    const keepHtml =
      !value.node.html &&
      old?.node.status === "ready" &&
      value.node.status === "ready" &&
      old.node.generation === value.node.generation;
    cache.current[value.node.id] = {
      ...value,
      card: value.card || old?.card || null,
      node: {
        ...value.node,
        content: "",
        html: keepHtml ? old.node.html : value.node.html || "",
      },
      savedAt: Date.now(),
    };
    persist();
    setRevision((v) => v + 1);
  }
  async function fetchNode(id: string) {
    if (!inflight.current[id])
      inflight.current[id] = jsonGet("/api/node/" + id)
        .then((d) => {
          storeEntry(d);
          return d;
        })
        .finally(() => {
          delete inflight.current[id];
        });
    return inflight.current[id];
  }
  async function library(day: string, edition = "") {
    const d = await jsonGet(
      "/api/library?date=" + day + (edition ? "&batch=" + edition : ""),
    );
    for (const n of d.nodes || [])
      storeEntry({
        node: n,
        card:
          day === digest?.date && (digest?.batch_id || "") === edition
            ? digest.cards[n.paper_no - 1]
            : null,
      });
  }
  async function refreshTools() {
    const [history, book] = await Promise.all([
      jsonGet("/api/batches"),
      jsonGet("/api/shelf"),
    ]);
    setBatches(history.batches);
    setPapers(book.papers);
    const pending = history.batches.find((b: any) =>
      ["queued", "running"].includes(b.status),
    );
    if (pending) setFinding(pending);
  }
  async function refreshResearch() {
    try {
      const d = await jsonGet("/api/batches?kind=topic");
      setResearches(d.batches);
      setHistoryLoaded(true);
      setHistoryError(false);
      try {
        localStorage.setItem(
          "literature-research-history-v1",
          JSON.stringify(d.batches.slice(0, 50)),
        );
      } catch {}
    } catch {
      setHistoryError(true);
    }
  }
  useEffect(() => {
    try {
      const saved = JSON.parse(
        localStorage.getItem("literature-research-history-v1") || "[]",
      );
      if (
        Array.isArray(saved) &&
        saved.every((x) => x.mode === "topic" && /^[a-f0-9]{32}$/.test(x.id))
      ) {
        setResearches(saved);
        if (saved.length) setHistoryLoaded(true);
      }
    } catch {}
  }, []);
  useEffect(() => {
    if (route === "research") refreshResearch();
  }, [route]);
  useEffect(() => {
    if (
      finding?.mode === "topic" &&
      ["ready", "error"].includes(finding.status)
    )
      refreshResearch();
  }, [finding?.id, finding?.status]);
  useEffect(() => {
    refreshTools().catch(() => {});
  }, []);
  useEffect(() => {
    if (
      !intro ||
      (digest?.date === intro[1] &&
        (digest?.batch_id || "") === (intro[3] || ""))
    )
      return;
    let stopped = false;
    jsonGet("/api/batch?" + (intro[3] ? "id=" + intro[3] : "date=" + intro[1]))
      .then((d) => {
        if (!stopped) setDigest(d.digest);
      })
      .catch(() => setError("暂时无法打开这篇文献。"));
    return () => {
      stopped = true;
    };
  }, [route]);
  useEffect(() => {
    if (
      !route.startsWith("batch:") &&
      !route.startsWith("day:") &&
      route !== "today"
    )
      return;
    let stopped = false;
    const query = route.startsWith("batch:")
      ? "/api/batch?id=" + route.slice(6)
      : route.startsWith("day:")
        ? "/api/batch?date=" + route.slice(4)
        : "/api/today";
    jsonGet(query)
      .then((d) => {
        if (!stopped) setDigest(d.digest);
      })
      .catch(() => {
        if (!stopped) setError("这组文献暂时打不开，请稍后再试。");
      });
    return () => {
      stopped = true;
    };
  }, [route]);
  useEffect(() => {
    if (!finding || !["queued", "running"].includes(finding.status)) return;
    let stopped = false,
      timer: any;
    const poll = async () => {
      try {
        const d = await jsonGet("/api/batches");
        if (stopped) return;
        setBatches(d.batches);
        const job = d.batches.find((b: any) => b.id === finding.id);
        if (job) {
          setFinding(job);
          if (
            job.status === "ready" &&
            discoveryOrigin.current &&
            routeRef.current === discoveryOrigin.current
          ) {
            navigate("batch:" + job.id);
            setFinding(null);
            discoveryOrigin.current = "";
          }
        }
        if (job && ["queued", "running"].includes(job.status))
          timer = setTimeout(poll, 3000);
      } catch {
        if (!stopped) timer = setTimeout(poll, 8000);
      }
    };
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [finding?.id, finding?.status]);
  async function discover(mode: string, seed?: any) {
    if (
      busyRef.current ||
      (finding && ["queued", "running"].includes(finding.status))
    )
      return;
    if (mode === "topic" && topic.trim().length < 2) {
      setError("先写下想调研的问题。");
      return;
    }
    const origin = routeRef.current;
    const canOpen =
      origin === "today" ||
      origin === "research" ||
      origin.startsWith("batch:") ||
      origin.startsWith("day:");
    busyRef.current = true;
    setBusy(true);
    setError("");
    const key = [
      "discover",
      mode,
      mode === "topic" ? topic.trim() : "",
      seed?.date || "",
      seed?.batch || "",
      seed?.paper || "",
    ].join(":");
    const requestId =
      requests.current[key] || (requests.current[key] = crypto.randomUUID());
    try {
      const response = await fetch("/api/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          topic: mode === "topic" ? topic.trim() : "",
          date: seed?.date,
          paper: seed?.paper,
          batch: seed?.batch || "",
          requestId,
        }),
      });
      if (!response.ok) throw Error("not accepted");
      const job: any = await response.json();
      delete requests.current[key];
      discoveryOrigin.current = canOpen ? origin : "";
      setFinding(job);
      if (job.status === "ready" && canOpen && routeRef.current === origin) {
        navigate("batch:" + job.id);
        setFinding(null);
        discoveryOrigin.current = "";
      }
      await refreshTools().catch(() => {});
    } catch {
      setError("这次检索没有提交成功，稍后点击可继续同一次请求。");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function mark(c: any, context: any, field: string, value: boolean) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/paper-state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...context, field, value }),
      });
      if (!response.ok) throw Error("save failed");
      const d: any = await response.json();
      setPapers((old) => [
        d.paper,
        ...old.filter((p) => p.key !== d.paper.key),
      ]);
    } catch {
      setError("这次标记没有保存，请稍后再试。");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  function tools(c: any, context: any) {
    const state = papers.find((p) => p.key === paperKey(c.source_url));
    return (
      <div className="paper-tools">
        <button
          disabled={busy}
          aria-pressed={!!state?.saved}
          onClick={() => mark(c, context, "saved", !state?.saved)}
        >
          {state?.saved ? "已加入稍后读" : "稍后读"}
        </button>
        <button
          disabled={busy}
          aria-pressed={!!state?.read}
          onClick={() => mark(c, context, "read", !state?.read)}
        >
          {state?.read ? "已读 ✓" : "标为已读"}
        </button>
        <button
          disabled={
            busy ||
            (!!finding && ["queued", "running"].includes(finding.status))
          }
          onClick={() => discover("similar", context)}
        >
          找相似文献
        </button>
      </div>
    );
  }
  async function openSaved(p: any) {
    try {
      const d = await jsonGet(
        "/api/batch?" + (p.batch ? "id=" + p.batch : "date=" + p.date),
      );
      setDigest(d.digest);
      navigate(overview(p.date, p.paper, p.batch));
    } catch {
      setError("这篇文献暂时打不开。");
    }
  }
  function expire() {
    let changed = false;
    for (const [id, e] of Object.entries(cache.current)) {
      const live = liveEntry(e);
      if (
        live &&
        live.node.status === "expired" &&
        e.node.status !== "expired"
      ) {
        cache.current[id] = live;
        changed = true;
      }
    }
    if (changed) {
      persist();
      setRevision((v) => v + 1);
    }
  }
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(cacheKey) || "{}");
      for (const [id, e] of Object.entries(persistentEntries(saved)))
        if (!cache.current[id]) cache.current[id] = e;
      const progress = JSON.parse(localStorage.getItem(progressKey) || "{}");
      if (progress.at > Date.now() - 14 * 86400000) {
        positions.current = progress.positions || {};
        lastRead.current = /^(read:|paper:)/.test(progress.last || "")
          ? progress.last
          : "";
        if (lastRead.current) setResume(lastRead.current);
      }
    } catch {}
    persist();
    setRevision((v) => v + 1);
    const oldRestoration = history.scrollRestoration;
    history.scrollRestoration = "manual";
    history.replaceState(
      {
        ...history.state,
        lcReaderDepth: Number(history.state?.lcReaderDepth || 0),
      },
      "",
      location.href,
    );
    const pop = () => {
      savePosition();
      restoring.current = true;
      setError("");
      const next = routeFrom(new URL(location.href));
      routeRef.current = next;
      setRoute(next);
    };
    let scrollTimer: any;
    const scroll = () => {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(savePosition, 150);
    };
    const visibility = () => {
      savePosition();
      expire();
    };
    window.addEventListener("popstate", pop);
    window.addEventListener("scroll", scroll, { passive: true });
    window.addEventListener("pagehide", savePosition);
    document.addEventListener("visibilitychange", visibility);
    const expiryTimer = setInterval(expire, 1000);
    if (!initialDigest)
      jsonGet("/api/today")
        .then((d) => setDigest(d.digest))
        .catch(() => setError("今天的文献暂未连上，可以先读已保存的内容。"));
    return () => {
      savePosition();
      clearTimeout(scrollTimer);
      clearInterval(expiryTimer);
      history.scrollRestoration = oldRestoration;
      window.removeEventListener("popstate", pop);
      window.removeEventListener("scroll", scroll);
      window.removeEventListener("pagehide", savePosition);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);
  const readyToRestore = isList
    ? listReady
    : route === "research" || route === "shelf"
      ? true
      : intro
        ? !!card
        : !!node && (node.status !== "ready" || !!node.html);
  useLayoutEffect(() => {
    if (!restoring.current || !readyToRestore) return;
    const timer = requestAnimationFrame(() => {
      window.scrollTo(0, positions.current[route] || 0);
      restoring.current = false;
    });
    return () => cancelAnimationFrame(timer);
  }, [route, readyToRestore, revision]);
  useEffect(() => {
    if (!digest?.date) return;
    let stopped = false,
      timer: any;
    const load = async () => {
      try {
        await library(digest.date, digest.batch_id || "");
      } catch {}
      if (!stopped) timer = setTimeout(load, 20000);
    };
    load();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [digest?.date, digest?.batch_id]);
  useEffect(() => {
    if (!route.startsWith("read:")) return;
    let stopped = false,
      timer: any;
    const id = route.slice(5);
    const poll = async () => {
      try {
        const data = await fetchNode(id);
        if (!stopped && ["queued", "running"].includes(data.node.status))
          timer = setTimeout(poll, 3000);
      } catch {
        if (!stopped) {
          setError(
            cache.current[id]?.node.html
              ? "连接较慢，你可以先继续阅读。"
              : "暂时无法打开，请稍后再试。",
          );
          timer = setTimeout(poll, 8000);
        }
      }
    };
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [route, node?.generation]);

  async function act(
    e: any,
    kind: string,
    day: string,
    no: number,
    parent?: string,
  ) {
    e.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    const from = routeRef.current,
      key = [kind, day, no, parent || "", batch].join(":");
    try {
      const requestId =
          requests.current[key] ||
          (requests.current[key] = crypto.randomUUID()),
        payload = { kind, date: day, batch, paper: no, parent, requestId };
      if (!feedback.has(kind)) {
        const id = await nodeId(payload),
          saved = liveEntry(cache.current[id]);
        if (saved && !["expired", "error"].includes(saved.node.status)) {
          navigate("read:" + id);
          return;
        }
      }
      const r = await fetch("/api/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }),
        d: any = await r.json();
      if (!r.ok) throw Error(d.error);
      delete requests.current[key];
      if (feedback.has(kind)) {
        setToast(labels[kind] + "已记录。");
        setTimeout(() => setToast(""), 3500);
      } else {
        const previous = cache.current[d.id];
        if (!previous || ["error", "expired"].includes(previous.node.status))
          storeEntry({
            node: {
              ...previous?.node,
              id: d.id,
              kind,
              paper_date: day,
              paper_no: no,
              batch,
              status: d.status,
              title:
                date === day && paper === no
                  ? card?.title_cn
                  : digest?.cards[no - 1]?.title_cn,
              html: "",
              generation: d.generation || 1,
            },
            card:
              day === digest?.date && (digest?.batch_id || "") === batch
                ? digest.cards[no - 1]
                : card,
          });
        if (routeRef.current === from) navigate("read:" + d.id);
        else setToast("内容准备中，可以先读其他文章。");
      }
    } catch {
      setError("这次操作没有完成，请稍后再试。");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  function action(
    kind: string,
    day: string,
    no: number,
    parent?: string,
    label?: string,
  ) {
    return (
      <a
        href="#"
        aria-disabled={busy}
        onClick={(e) => {
          if (kind === "read" && card) {
            e.preventDefault();
            mark(card, { date: day, paper: no, batch }, "read", true);
          } else act(e, kind, day, no, parent);
        }}
      >
        {label || labels[kind]}
      </a>
    );
  }
  function homeLink() {
    const target = batch ? "batch:" + batch : homeRoute;
    return (
      <a
        className="today-link"
        href={routeUrl(target)}
        onClick={(e) => {
          e.preventDefault();
          navigate(target);
        }}
      >
        {target.startsWith("batch:") ? "文献列表" : "今日文献"}
      </a>
    );
  }
  function tabs() {
    return (
      <nav className="reading-tabs" aria-label="阅读档位">
        <a
          href={routeUrl(overview(date, paper, batch))}
          aria-current={intro ? "page" : undefined}
          onClick={(e) => {
            e.preventDefault();
            navigate(overview(date, paper, batch));
          }}
        >
          简介
        </a>
        {["coarse", "deep", "translation"].map((kind) => (
          <a
            key={kind}
            href="#"
            aria-current={node?.kind === kind ? "page" : undefined}
            aria-disabled={busy}
            onClick={(e) => act(e, kind, date, paper)}
          >
            {labels[kind]}
          </a>
        ))}
      </nav>
    );
  }
  function footer(n: any) {
    const d = n.paper_date,
      p = n.paper_no;
    return (
      <footer>
        <div className="link-row">
          {n.kind === "coarse" ? (
            <>
              {action("explain", d, p, n.id)}
              {action("deep", d, p)}
            </>
          ) : n.kind === "deep" ? (
            <>
              {action("practice", d, p, n.id)}
              {action("read", d, p)}
              {action("explain", d, p, n.id)}
            </>
          ) : n.kind === "explain" ? (
            <>
              {action("explain", d, p, n.id, "再通俗一点")}
              {action("deep", d, p)}
            </>
          ) : n.kind === "practice" ? (
            <>
              {action("read", d, p)}
              {action("explain", d, p, n.id)}
            </>
          ) : (
            <>
              {action("coarse", d, p)}
              {action("deep", d, p)}
              {card && (
                <a href={card.source_url} target="_blank" rel="noreferrer">
                  原文
                </a>
              )}
            </>
          )}
        </div>
        {["coarse", "explain"].includes(n.kind) && (
          <div className="link-row feedback">
            {action("like", d, p)}
            {action("neutral", d, p)}
          </div>
        )}
        <div className="link-row return-actions">
          {homeLink()}
          {date === digest?.date && p < 5 && (
            <a
              href={routeUrl(overview(date, p + 1, batch))}
              onClick={(e) => {
                e.preventDefault();
                navigate(overview(date, p + 1, batch));
              }}
            >
              下一篇
            </a>
          )}
        </div>
      </footer>
    );
  }
  return (
    <main
      className={isList ? "library-view" : "reader-view"}
      data-view={route}
      data-document-id={documentId}
      aria-busy={busy}
    >
      <header>
        <div className="eyebrow">文献启发</div>
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("today");
          }}
        >
          今日文献
        </a>
        <p>
          {isList
            ? digest?.mode === "topic" || digest?.mode === "similar"
              ? "围绕一个问题，读懂相关研究。"
              : "三篇贴近兴趣，两篇打开眼界。"
            : "读到哪里，回来还在这里。"}
        </p>
      </header>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
      <nav className="workspace-nav" aria-label="阅读工作区">
        <button
          aria-current={isList ? "page" : undefined}
          onClick={() => navigate("today")}
        >
          今日文献
        </button>
        <button
          aria-current={route === "research" ? "page" : undefined}
          onClick={() => navigate("research")}
        >
          主题调研
        </button>
        <button
          aria-current={route === "shelf" ? "page" : undefined}
          onClick={() => {
            navigate("shelf");
            refreshTools().catch(() => {});
          }}
        >
          我的书架
        </button>
        <button
          aria-expanded={showHistory}
          onClick={() => {
            setShowHistory(!showHistory);
            refreshTools().catch(() => {});
          }}
        >
          历史分组
        </button>
      </nav>
      {finding && (
        <div className="discovery-status" role="status">
          {["queued", "running"].includes(finding.status) ? (
            "正在查找新的五篇，可以先继续阅读。"
          ) : finding.status === "ready" ? (
            <>
              新的一组已就绪。
              <button
                onClick={() => {
                  navigate("batch:" + finding.id);
                  setFinding(null);
                }}
              >
                打开新五篇
              </button>
            </>
          ) : (
            <>
              这次没有找到完整的新五篇，原有文献已保留。
              <button
                onClick={() => {
                  setTopic(finding.topic || "");
                  setFinding(null);
                  navigate("research");
                }}
              >
                重新选择
              </button>
            </>
          )}
        </div>
      )}
      {showHistory && route !== "research" && (
        <section className="history-panel" aria-label="历史文献分组">
          <h2>之前的文献</h2>
          {batches
            .filter((b) => b.status === "ready")
            .map((b) => (
              <button
                key={b.id || b.date}
                onClick={() => {
                  navigate(b.id ? "batch:" + b.id : "day:" + b.date);
                  setShowHistory(false);
                }}
              >
                {b.label}
                <span>{b.date}</span>
              </button>
            ))}
        </section>
      )}
      {route === "research" && (
        <>
          <section className="research-form">
            <h1>从一个问题开始</h1>
            <p>写下想了解的主题或研究问题，找五篇直接相关和有启发的论文。</p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                discover("topic");
              }}
            >
              <label htmlFor="research-question">想研究什么？</label>
              <textarea
                id="research-question"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                maxLength={500}
                rows={4}
                placeholder="例如：四足机器人怎样减少里程计累计漂移？"
              />
              <button
                disabled={
                  busy ||
                  (!!finding && ["queued", "running"].includes(finding.status))
                }
                type="submit"
              >
                开始调研 · 找五篇
              </button>
            </form>
          </section>
          <section className="research-history" aria-label="历史主题调研">
            <h2>查过的主题</h2>
            <p className="history-caption">按查询时间排列，最近的在最前面。</p>
            {historyError && (
              <p role="status">
                {researches.length
                  ? "当前连接较慢，先显示已保存的历史。"
                  : "历史暂时没能取回，请稍后重试。"}
                <button onClick={refreshResearch}>重新读取历史</button>
              </p>
            )}
            {!historyLoaded && !historyError ? (
              <p>正在打开历史…</p>
            ) : historyLoaded && researches.length === 0 ? (
              <p className="empty-state">
                完成第一次主题调研后，查询和文献会保留在这里。
              </p>
            ) : (
              researches.map((r) => (
                <article className="research-history-item" key={r.id}>
                  <div>
                    <h3>{r.topic || r.label}</h3>
                    <time dateTime={new Date(r.created_at).toISOString()}>
                      {new Date(r.created_at).toLocaleString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                        year: "numeric",
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </time>
                  </div>
                  <button
                    onClick={() => {
                      if (r.status === "ready") navigate("batch:" + r.id);
                      else {
                        setTopic(r.topic || "");
                        setToast(
                          r.status === "error"
                            ? "上次未完成，可以修改问题后重新提交。"
                            : "这项调研正在进行，完成后会保留在这里。",
                        );
                      }
                    }}
                  >
                    {r.status === "ready"
                      ? "查看这次文献"
                      : ["queued", "running"].includes(r.status)
                        ? "正在调研"
                        : "查看原问题"}
                  </button>
                </article>
              ))
            )}
          </section>
        </>
      )}
      {route === "shelf" && (
        <section className="shelf-view">
          <h1>我的书架</h1>
          <div className="shelf-filters">
            <button
              aria-pressed={shelfFilter === "saved"}
              onClick={() => setShelfFilter("saved")}
            >
              稍后读
            </button>
            <button
              aria-pressed={shelfFilter === "read"}
              onClick={() => setShelfFilter("read")}
            >
              已读
            </button>
          </div>
          {papers.filter((p) => p[shelfFilter]).length === 0 ? (
            <p className="empty-state">
              在文献卡片点“稍后读”或“标为已读”，就会出现在这里。
            </p>
          ) : (
            papers
              .filter((p) => p[shelfFilter])
              .map((p) => (
                <section className="paper" key={p.key}>
                  <small>{p.card.field}</small>
                  <h2>{p.card.title_cn}</h2>
                  <p className="summary">{plain(p.card.plain_summary)}</p>
                  <button className="open-reading" onClick={() => openSaved(p)}>
                    继续阅读
                  </button>
                  {tools(p.card, {
                    date: p.date,
                    paper: p.paper,
                    batch: p.batch,
                  })}
                </section>
              ))
          )}
        </section>
      )}
      <div hidden={!isList}>
        <div className="discover-toolbar">
          <button
            disabled={
              busy ||
              (!!finding && ["queued", "running"].includes(finding.status))
            }
            onClick={() => discover("more")}
          >
            再找 5 篇
          </button>
          <span>旧的分组会保留在历史中</span>
        </div>
        {!listReady ? (
          <p className="loading" role="status">
            正在打开这组文献…
          </p>
        ) : (
          <>
            <div className="edition">
              <span>
                {digest?.date || "正在准备今日文献"}
                {digest?.batch_id ? " · " + digest.label : ""}
              </span>
            </div>
            {resume && (
              <a
                className="resume-link"
                href={routeUrl(resume)}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(resume);
                }}
              >
                继续上次阅读
                {cache.current[resume.slice(5)]?.node.title
                  ? " · " + cache.current[resume.slice(5)].node.title
                  : ""}
              </a>
            )}
            {digest?.cards?.map((c: any, i: number) => (
              <section className="paper" key={c.source_url}>
                <small>
                  {String(i + 1).padStart(2, "0")} /{" "}
                  {digest.mode === "topic" || digest.mode === "similar"
                    ? c.group === "preference"
                      ? "直接相关"
                      : "拓展阅读"
                    : c.group === "preference"
                      ? "贴近兴趣"
                      : "跨领域探索"}{" "}
                  · {c.field}
                </small>
                <h1>{c.title_cn}</h1>
                <p className="summary">{plain(c.plain_summary)}</p>
                <details className="paper-insight">
                  <summary>为什么读这篇</summary>
                  <p>{plain(c.why || "")}</p>
                  <p>{plain(c.learn || "")}</p>
                </details>
                <div className="link-row main-actions">
                  {action("coarse", digest.date, i + 1)}
                  {action("deep", digest.date, i + 1)}
                  {action("translation", digest.date, i + 1)}
                  <a href={c.source_url} target="_blank" rel="noreferrer">
                    原文
                  </a>
                </div>
                <div className="link-row feedback">
                  {action("like", digest.date, i + 1)}
                  {action("neutral", digest.date, i + 1)}
                </div>
                {tools(c, {
                  date: digest.date,
                  paper: i + 1,
                  batch: digest.batch_id || "",
                })}
              </section>
            ))}
            {digest &&
              !digest.batch_id &&
              digest.date === initialDigest?.date && (
                <details>
                  <summary>一点英语 · 今天动手</summary>
                  {html(englishHtml)}
                </details>
              )}
          </>
        )}
      </div>
      {(route.startsWith("read:") || intro) && (
        <>
          <div className="reader-back">
            <button onClick={back}>返回上一处</button>
            {homeLink()}
          </div>
          {card && tabs()}
          <section className="reading">
            {intro && card ? (
              <>
                <small>
                  简介 · {date} · 第 {paper} 篇
                </small>
                <h1>{card.title_cn}</h1>
                <p className="summary">{plain(card.plain_summary)}</p>
                <div className="link-row">
                  {action("coarse", date, paper)}
                  {card && (
                    <a href={card.source_url} target="_blank" rel="noreferrer">
                      原文
                    </a>
                  )}
                </div>
                <footer>{homeLink()}</footer>
              </>
            ) : !node ? (
              <div className="loading" role="status">
                正在打开…
              </div>
            ) : (
              <>
                <small>
                  {node.kind === "translation" && /摘要/.test(node.scope || "")
                    ? "摘要中文解读"
                    : labels[node.kind]}{" "}
                  · {node.paper_date}
                </small>
                <h1>{node.title || card?.title_cn}</h1>
                {["queued", "running"].includes(node.status) ? (
                  <div className="loading" role="status">
                    <span className="dot" />
                    正在准备{labels[node.kind]}…<p>可以先读其他文章。</p>
                  </div>
                ) : node.status === "expired" ? (
                  <div className="notice">
                    这份译文已到期，可以重新翻译。
                    <div className="link-row">
                      {action(
                        "translation",
                        node.paper_date,
                        node.paper_no,
                        undefined,
                        "重新翻译",
                      )}
                    </div>
                  </div>
                ) : node.status === "error" ? (
                  <div className="notice error">
                    这份内容暂时没能准备好，请稍后重试。
                    <div className="link-row">
                      {action(
                        node.kind,
                        node.paper_date,
                        node.paper_no,
                        node.parent_id,
                        "重新尝试",
                      )}
                    </div>
                  </div>
                ) : (
                  <>
                    {node.kind === "translation" && (
                      <p className="expiry">
                        译文可阅读至{" "}
                        {new Date(node.expires_at).toLocaleString("zh-CN", {
                          timeZone: "Asia/Shanghai",
                        })}
                      </p>
                    )}
                    {node.html ? (
                      html(node.html)
                    ) : (
                      <p className="loading">正在打开正文…</p>
                    )}
                    {card && tools(card, { date, paper, batch })}
                    {footer(node)}
                  </>
                )}
                {node.status !== "ready" && homeLink()}
              </>
            )}
          </section>
        </>
      )}
    </main>
  );
}
