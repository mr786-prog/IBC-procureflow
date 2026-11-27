// Shared API client
let currentUser = null;

export async function apiRequest(url, options = {}) {
  const defaultHeaders = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };

  if (currentUser && currentUser.csrf_token) {
    defaultHeaders["X-CSRF-Token"] = currentUser.csrf_token;
  }

  const response = await fetch(url, {
    ...options,
    headers: {
      ...defaultHeaders,
      ...(options.headers || {})
    }
  });

  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }

  const contentType = response.headers.get("content-type");
  let data;
  if (contentType && contentType.includes("application/json")) {
    data = await response.json();
  } else {
    data = await response.text();
  }

  if (!response.ok) {
    const errorMsg = data.detail || (typeof data === "string" ? data : "Request failed");
    showToast(errorMsg, "error");
    throw new Error(errorMsg);
  }

  return data;
}

export async function fetchCurrentUser() {
  try {
    const user = await apiRequest("/api/v1/auth/me");
    currentUser = user;
    return user;
  } catch (err) {
    window.location.href = "/login";
  }
}

export function showToast(message, type = "info") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 4000);
}
