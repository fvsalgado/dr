export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response("", { headers: corsHeaders() });
    }
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: corsHeaders() });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("Invalid JSON", { status: 400, headers: corsHeaders() });
    }

    const items = Array.isArray(body.items) ? body.items : null;
    if (!items) {
      return new Response("items must be an array", { status: 400, headers: corsHeaders() });
    }
    if (!env.ANTHROPIC_API_KEY) {
      return new Response("Missing ANTHROPIC_API_KEY", { status: 500, headers: corsHeaders() });
    }

    const prompt = `You are a professional legal and policy translator.
Translate these Portuguese items to concise, clear English.
Return ONLY a JSON array (no markdown): [{idx, title_en, summary_en}]

Items:
${JSON.stringify(items, null, 2)}`;

    const upstream = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: env.ANTHROPIC_MODEL || "claude-sonnet-4-20250514",
        max_tokens: 4000,
        messages: [{ role: "user", content: prompt }],
      }),
    });

    if (!upstream.ok) {
      const txt = await upstream.text();
      return new Response(`Anthropic error: ${txt}`, { status: 502, headers: corsHeaders() });
    }

    const data = await upstream.json();
    const raw = (data.content || []).map((b) => b.text || "").join("");
    const cleaned = raw.replace(/```json|```/g, "").trim();
    try {
      const parsed = JSON.parse(cleaned);
      return new Response(JSON.stringify(parsed), {
        status: 200,
        headers: { ...corsHeaders(), "content-type": "application/json; charset=utf-8" },
      });
    } catch {
      return new Response("Could not parse model output", { status: 502, headers: corsHeaders() });
    }
  },
};

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "POST,OPTIONS",
    "access-control-allow-headers": "content-type",
  };
}

