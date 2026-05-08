// Cloudflare Worker: прозрачный прокси к Google AI Studio (Gemini API).
// Прод (РФ-IP) → этот Worker (CF-IP) → generativelanguage.googleapis.com
//
// Деплой: cloudflare.com → Workers & Pages → Create Worker → Edit code →
// заменить всё этим кодом → Save and Deploy. URL воркера будет вида
// https://<имя>.<твой-username>.workers.dev — это и есть новый GEMINI_BASE_URL,
// надо только дописать к нему "/v1beta/openai/" в конце.

const TARGET = "https://generativelanguage.googleapis.com";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const targetUrl = TARGET + url.pathname + url.search;

    // Прокидываем метод/заголовки/тело без изменений.
    // Authorization: Bearer <GEMINI_KEY> идёт с прода как есть.
    const proxied = await fetch(targetUrl, {
      method: request.method,
      headers: request.headers,
      body: request.body,
      redirect: "follow",
    });

    // Возвращаем ответ Google как есть (статус, body, headers).
    return new Response(proxied.body, {
      status: proxied.status,
      statusText: proxied.statusText,
      headers: proxied.headers,
    });
  },
};
