import Reader from "./reader";
import { articleHtml } from "@/lib/article";
import "katex/dist/katex.min.css";
import { env } from "cloudflare:workers";
import { digestFor, readingFor } from "@/lib/reader-data";
export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{
    read?: string;
    paper?: string;
    date?: string;
    batch?: string;
    day?: string;
    view?: string;
  }>;
}) {
  const params = await searchParams,
    id = /^[a-f0-9]{32}$/.test(params?.read || "") ? params.read! : "";
  let digest = null,
    reading = null,
    overview = null;
  try {
    const db = env.DB;
    if (!db) throw Error("reading database is starting");
    digest = await digestFor(
      db,
      /^\d{4}-\d{2}-\d{2}$/.test(params.day || "") ? params.day : undefined,
      /^[a-f0-9]{32}$/.test(params.batch || "") ? params.batch : undefined,
    );
    if (id) reading = await readingFor(db, id);
    if (
      /^[1-5]$/.test(params.paper || "") &&
      /^\d{4}-\d{2}-\d{2}$/.test(params.date || "")
    ) {
      const saved = await digestFor(
        db,
        params.date,
        /^[a-f0-9]{32}$/.test(params.batch || "") ? params.batch : undefined,
      );
      if (saved)
        overview = {
          date: params.date,
          paper: Number(params.paper),
          batch: saved.batch_id || "",
          card: saved.cards[Number(params.paper) - 1],
        };
    }
  } catch {
    /* The client can reconnect if the local database is starting. */
  }
  const paper = overview?.paper || 0;
  const requestedView =
    params.view === "research" || params.view === "shelf"
      ? params.view
      : params.day
        ? "day:" + params.day
        : "";
  return (
    <Reader
      initialDigest={digest}
      initialReading={reading}
      requestedId={id}
      requestedPaper={paper}
      initialOverview={overview}
      requestedView={requestedView}
      documentId={crypto.randomUUID()}
      englishHtml={
        digest ? articleHtml(digest.english + "\n\n" + digest.practice) : ""
      }
    />
  );
}
