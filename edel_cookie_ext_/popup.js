const DOMAIN = "runway.edel.finance";
const COOKIE_NAME = "edel_session";

function $(id) { return document.getElementById(id); }

function setStatus(msg, type = "info") {
  $("status").textContent = msg;
  $("status").className = type;
}

function formatCookieMessage(accId, cookieValue) {
  return `@${accId} edel_session=${cookieValue}`;
}

async function getEdelSession() {
  try {
    const cookie = await chrome.cookies.get({
      url: `https://${DOMAIN}`,
      name: COOKIE_NAME
    });
    return cookie ? cookie.value : null;
  } catch (e) {
    console.error("Error getting cookie:", e);
    return null;
  }
}

function decodeTokenInfo(cookieValue) {
  if (!cookieValue) return null;
  try {
    const parts = cookieValue.split(".");
    const padded = parts[0] + "=".repeat((-parts[0].length % 4 + 4) % 4);
    const decoded = JSON.parse(atob(padded.replace(/-/g, "+").replace(/_/g, "/")));
    return decoded;
  } catch (e) {
    return null;
  }
}

function formatExpiry(tokenData) {
  if (!tokenData || !tokenData.e) return "Tidak terbaca";
  const expMs = typeof tokenData.e === "number" ? tokenData.e : parseInt(tokenData.e);
  const expDate = new Date(expMs);
  const now = new Date();
  const remaining = expDate - now;
  
  const wib = expDate.toLocaleString("id-ID", { timeZone: "Asia/Jakarta" });
  
  if (remaining <= 0) return `⛔ EXPIRED (${wib} WIB)`;
  
  const hours = Math.floor(remaining / 3600000);
  const mins = Math.floor((remaining % 3600000) / 60000);
  return `✅ Aktif ${hours}j ${mins}m — exp ${wib} WIB`;
}

async function refreshCookie() {
  setStatus("Mengambil cookie...", "info");
  $("cookieOutput").value = "";
  $("tokenInfo").textContent = "";
  
  const cookieValue = await getEdelSession();
  if (!cookieValue) {
    setStatus("❌ Tidak ada cookie edel_session. Pastikan sudah login di runway.edel.finance", "error");
    return;
  }
  
  const accId = $("accId").value.trim() || "acc1";
  const message = formatCookieMessage(accId, cookieValue);
  $("cookieOutput").value = message;
  
  // Decode token info
  const tokenData = decodeTokenInfo(cookieValue);
  if (tokenData) {
    const expiryStr = formatExpiry(tokenData);
    $("tokenInfo").textContent = `Token: ${expiryStr}`;
  }
  
  setStatus("✅ Cookie berhasil diambil!", "success");
}

async function copyCookie() {
  const text = $("cookieOutput").value;
  if (!text) {
    setStatus("❌ Ambil cookie dulu sebelum copy", "error");
    return;
  }
  
  try {
    await navigator.clipboard.writeText(text);
    setStatus("✅ Copied! Langsung kirim ke Telegram bot", "success");
  } catch (e) {
    // Fallback
    $("cookieOutput").select();
    document.execCommand("copy");
    setStatus("✅ Copied (fallback)!", "success");
  }
}

// Event listeners
$("refreshBtn").addEventListener("click", refreshCookie);
$("copyBtn").addEventListener("click", copyCookie);
$("clearBtn").addEventListener("click", () => {
  $("cookieOutput").value = "";
  $("tokenInfo").textContent = "";
  setStatus("Cleared", "info");
});

// Auto-refresh on popup open
document.addEventListener("DOMContentLoaded", refreshCookie);
