const API_BASE = "http://49.50.135.32:8000/api";
const BLOCK_PAGE = chrome.runtime.getURL("blocked.html");

async function postJson(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

function getClientId() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(["client_id"], (data) => {
      if (data.client_id) return resolve(data.client_id);
      const id = crypto.randomUUID();
      chrome.storage.sync.set({ client_id: id }, () => resolve(id));
    });
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // 1. URL 자동 평가
  if (msg.type === "CHECK_URL" && sender.tab) {
    const tabId = sender.tab.id;
    const url = msg.url;
    if (!url) return;

    getClientId().then((clientId) => {
      postJson("/evaluate", { url, client_id: clientId })
        .then((data) => {
          if (data.decision === "BLOCK") {
            const reason = encodeURIComponent(data.reason || "UNKNOWN_BLOCK");
            const redirectUrl = `${BLOCK_PAGE}?reason=${reason}&url=${encodeURIComponent(url)}`;
            chrome.tabs.update(tabId, { url: redirectUrl });
          }
        })
        .catch((err) => console.error(err));
    });
    return;
  }

  // 2. 수동 차단
  if (msg.type === "PG_BLOCK_URL" && sender.tab) {
    const tabId = sender.tab.id;
    const url = msg.url;
    if (!url) return;

    getClientId().then((clientId) => {
      Promise.all([
        postJson("/report", { user_token: clientId, url }),
        postJson("/override", { client_id: clientId, url, decision: 1 }),
      ])
        .then(() => {
          const reason = "USER_REPORTED_BLOCK";
          const redirectUrl = `${BLOCK_PAGE}?reason=${reason}&url=${encodeURIComponent(url)}`;
          chrome.tabs.update(tabId, { url: redirectUrl });
        })
        .catch((err) => console.error(err));
    });
    return;
  }

  // 3. 단일 차단 해제
  if (msg.type === "PG_UNBLOCK_URL") {
    const url = msg.url;
    if (!url) return;
    getClientId().then((clientId) => {
      postJson("/remove-override", { client_id: clientId, url }).catch((err) =>
        console.error(err)
      );
    });
    return;
  }

  // 4. 내 차단 목록 조회
  if (msg.type === "PG_GET_MY_BLOCKED_URLS") {
    getClientId()
      .then((clientId) => postJson("/my-blocked-urls", { client_id: clientId }))
      .then((data) => sendResponse({ urls: data.urls || [] }))
      .catch((err) => {
        console.error("PG_GET_MY_BLOCKED_URLS error:", err);
        sendResponse({ error: true });
      });
    return true;
  }

  // 5. 여러 개 해제
  if (msg.type === "PG_REMOVE_OVERRIDE_MULTI") {
    const urls = msg.urls || [];
    if (urls.length === 0) {
      sendResponse({ ok: true });
      return;
    }

    getClientId()
      .then((clientId) =>
        Promise.all(
          urls.map((url) => postJson("/remove-override", { client_id: clientId, url }))
        )
      )
      .then(() => sendResponse({ ok: true }))
      .catch((err) => {
        console.error("PG_REMOVE_OVERRIDE_MULTI error:", err);
        sendResponse({ error: true });
      });

    return true;
  }
});
