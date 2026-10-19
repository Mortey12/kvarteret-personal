// Skonk admin chat. Delegated listeners so hx-boost navigation keeps working.
// The conversation lives only in this array; the server stores nothing.
(function () {
  const messages = [];
  const csrf = () =>
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Feil ${response.status}`);
    return data;
  }

  function bubble(log, text, own) {
    const el = document.createElement("div");
    el.className =
      "max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm " +
      (own ? "self-end bg-kvarteret-red/10" : "self-start bg-kvarteret-sand");
    el.textContent = text; // textContent: model output is never rendered as HTML
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  function proposalCard(log, proposal) {
    const card = document.createElement("div");
    card.className = "self-start rounded-2xl border border-kvarteret-red/30 p-4 text-sm";
    const title = document.createElement("p");
    title.className = "font-semibold";
    title.textContent = proposal.title;
    card.appendChild(title);
    for (const c of proposal.changes) {
      const line = document.createElement("p");
      line.textContent = `${c.field}: ${c.from ?? "–"} → ${c.to ?? "–"}`;
      card.appendChild(line);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className =
      "mt-3 rounded-full bg-kvarteret-red px-4 py-2 text-xs font-semibold text-kvarteret-surface";
    button.textContent = "Bekreft endring";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await post("/agent/apply", { token: proposal.token });
        button.replaceWith(Object.assign(document.createElement("p"), { textContent: "✓ " + result.message }));
      } catch (error) {
        button.disabled = false;
        button.textContent = "Feilet: " + error.message + " (prøv igjen)";
      }
    });
    card.appendChild(button);
    log.appendChild(card);
    log.scrollTop = log.scrollHeight;
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-agent-form]");
    if (!form) return;
    event.preventDefault();
    const log = form.parentElement.querySelector("[data-agent-log]");
    const field = form.elements.prompt;
    if (!log.children.length) messages.length = 0; // fresh page render = fresh conversation
    const text = field.value.trim();
    if (!text) return;
    field.value = "";
    messages.push({ role: "user", content: text });
    bubble(log, text, true);
    const pending = bubble(log, "Skonk tenker …", false);
    form.querySelector("button").disabled = true;
    try {
      const data = await post("/agent/chat", { messages });
      messages.push({ role: "assistant", content: data.reply || "(tomt svar)" });
      pending.textContent = data.reply || "(tomt svar)";
      data.proposals.forEach((p) => proposalCard(log, p));
    } catch (error) {
      messages.pop();
      pending.textContent = error.message;
    } finally {
      form.querySelector("button").disabled = false;
    }
  });
})();
