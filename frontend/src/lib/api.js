const BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export const api = {
  async login(email, password) {
    const url = new URL(`${BASE}/login`);
    url.searchParams.set("email", email);
    url.searchParams.set("password", password);
    const res = await fetch(url.toString(), { method: "POST" });
    if (!res.ok) throw new Error("Invalid credentials");
    return res.json();
  },

  async signup(email, password) {
    const url = new URL(`${BASE}/signup`);
    url.searchParams.set("email", email);
    url.searchParams.set("password", password);
    const res = await fetch(url.toString(), { method: "POST" });
    if (!res.ok) throw new Error("Registration failed");
    return res.json();
  },

  async createProject(token, name, description, files) {
    const form = new FormData();
    form.append("project_name", name);
    form.append("project_description", description || "");
    files.forEach((f) => form.append("files", f));
    const res = await fetch(`${BASE}/projects/create`, {
      method: "POST",
      headers: authHeaders(token),
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async processProject(token, projectId) {
    const res = await fetch(`${BASE}/projects/${projectId}/process`, {
      method: "POST",
      headers: authHeaders(token),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getParameters(token, projectId) {
    const res = await fetch(`${BASE}/projects/${projectId}/parameters`, {
      headers: authHeaders(token),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async query(token, projectId, question) {
    const form = new FormData();
    form.append("query", question);
    const res = await fetch(`${BASE}/projects/${projectId}/query`, {
      method: "POST",
      headers: authHeaders(token),
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getMe(token) {
    const res = await fetch(`${BASE}/me`, {
      headers: authHeaders(token),
    });
    if (!res.ok) throw new Error("Failed to fetch user");
    return res.json();
  },

  async listProjects(token) {
    try {
      const res = await fetch(`${BASE}/projects`, {
        headers: authHeaders(token),
      });
      if (!res.ok) return [];
      return res.json();
    } catch {
      return [];
    }
  },
};
