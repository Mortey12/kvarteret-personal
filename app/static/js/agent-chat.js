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

  // Minimal, safe Markdown: everything is HTML-escaped first; links are limited to
  // same-site paths and https, and open in a new tab so the chat is not lost.
  const esc = (t) =>
    t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  function inline(t) {
    return esc(t)
      .replace(/`([^`]+)`/g, '<code class="rounded bg-white/60 px-1">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\(((?:\/(?!\/)|https:\/\/)[^\s)]*)\)/g,
        '<a class="font-medium text-kvarteret-red underline" href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function markdown(src) {
    const lines = src.replace(/\r/g, "").split("\n");
    const out = [];
    let list = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let m;
      if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
        close();
        const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
        let html = '<table class="my-2 text-left text-xs"><thead><tr>' +
          cells(line).map((c) => `<th class="border-b border-kvarteret-night/20 px-2 py-1">${c}</th>`).join("") + "</tr></thead><tbody>";
        i += 2;
        for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++)
          html += "<tr>" + cells(lines[i]).map((c) => `<td class="px-2 py-1">${c}</td>`).join("") + "</tr>";
        i--;
        out.push(html + "</tbody></table>");
      } else if ((m = line.match(/^\s*[-*]\s+(.*)/))) {
        if (list !== "ul") { close(); out.push('<ul class="ml-5 list-disc">'); list = "ul"; }
        out.push(`<li>${inline(m[1])}</li>`);
      } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
        if (list !== "ol") { close(); out.push('<ol class="ml-5 list-decimal">'); list = "ol"; }
        out.push(`<li>${inline(m[1])}</li>`);
      } else if ((m = line.match(/^#{1,6}\s+(.*)/))) {
        close();
        out.push(`<p class="mt-2 font-semibold">${inline(m[1])}</p>`);
      } else if (!line.trim()) {
        close();
      } else {
        close();
        out.push(`<p class="mt-1">${inline(line)}</p>`);
      }
    }
    close();
    return out.join("");
  }

  function bubble(log, text, own, rich) {
    const el = document.createElement("div");
    el.className =
      "max-w-[85%] rounded-2xl px-4 py-2 text-sm " +
      (own ? "self-end whitespace-pre-wrap bg-kvarteret-red/10" : "self-start bg-kvarteret-sand");
    if (rich) el.innerHTML = markdown(text); // escaped + filtered, see markdown()
    else el.textContent = text;
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

  // Enter sends, Shift+Enter inserts a newline.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    const form = event.target.closest?.("[data-agent-form]");
    if (!form) return;
    event.preventDefault();
    form.requestSubmit();
  });

  // Capture phase + hx-boost="false" on the form: the body is boosted, and htmx
  // would otherwise submit the form itself and morph the page, wiping the chat.
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-agent-form]");
    if (!form) return;
    event.preventDefault();
    event.stopImmediatePropagation();
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
      pending.innerHTML = markdown(data.reply || "(tomt svar)");
      log.scrollTop = log.scrollHeight;
      data.proposals.forEach((p) => proposalCard(log, p));
    } catch (error) {
      messages.pop();
      pending.textContent = error.message;
    } finally {
      form.querySelector("button").disabled = false;
    }
  }, true);
})();
