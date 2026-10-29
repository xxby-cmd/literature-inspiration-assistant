import handler from "vinext/server/fetch-handler";
export default {
  async fetch(
    request: Request,
    env: Cloudflare.Env,
    context: ExecutionContext,
  ) {
    const expected = env.LC_INSTANCE_ID;
    if (expected && request.headers.get("X-Literature-Instance") !== expected)
      return new Response("Reader instance mismatch", { status: 409 });
    if (new URL(request.url).pathname === "/api/health")
      return Response.json(
        { instance: expected || "development" },
        { headers: { "Cache-Control": "no-store" } },
      );
    return handler.fetch(request, env, context);
  },
};
