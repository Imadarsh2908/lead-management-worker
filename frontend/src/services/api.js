// VITE_API_URL is baked in at build time (see frontend/.env.example). Falls back
// to localhost for local `npm run dev` against a locally running backend.
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function parseJwt(token) {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      window.atob(base64)
        .split('')
        .map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(jsonPayload);
  } catch (e) {
    return null;
  }
}

async function request(path, options = {}) {
  let accessToken = localStorage.getItem('access_token');
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }

  let res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (res.status === 401) {
    const refreshToken = localStorage.getItem('refresh_token');
    if (refreshToken) {
      console.log('Access token expired, attempting refresh...');
      try {
        const refreshRes = await fetch(`${API_BASE}/v1/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });

        if (refreshRes.ok) {
          const data = await refreshRes.json();
          localStorage.setItem('access_token', data.access_token);
          localStorage.setItem('refresh_token', data.refresh_token);

          headers['Authorization'] = `Bearer ${data.access_token}`;
          res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers,
          });
        } else {
          logout();
          window.dispatchEvent(new Event('auth-logout'));
        }
      } catch (err) {
        console.error('Failed to refresh token:', err);
        logout();
        window.dispatchEvent(new Event('auth-logout'));
      }
    }
  }

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({ detail: 'An unknown error occurred.' }));
    throw new Error(errorData.detail || errorData.error || 'Request failed.');
  }

  if (res.status === 204) return null;

  return res.json();
}

export function logout() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
}

export const api = {
  login: async (username, password) => {
    const data = await request('/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);
    return parseJwt(data.access_token);
  },

  getCurrentUser: () => {
    const token = localStorage.getItem('access_token');
    if (!token) return null;
    const decoded = parseJwt(token);
    if (!decoded || (decoded.exp && decoded.exp * 1000 < Date.now())) {
      logout();
      return null;
    }
    return {
      username: decoded.sub,
      role: decoded.role,
    };
  },

  createLead: (lead) => {
    return request('/v1/leads/', {
      method: 'POST',
      body: JSON.stringify(lead),
    });
  },

  getLeads: (page = 1, pageSize = 20) => {
    return request(`/v1/leads/?page=${page}&page_size=${pageSize}`);
  },

  getLead: (leadId) => {
    return request(`/v1/leads/${leadId}`);
  },

  getLeadStatus: (leadId) => {
    return request(`/v1/leads/${leadId}/status`);
  },

  getLeadAuditLogs: (leadId) => {
    return request(`/v1/leads/${leadId}/audit`);
  },

  deleteLead: (leadId) => {
    return request(`/v1/leads/${leadId}`, {
      method: 'DELETE',
    });
  },
};
