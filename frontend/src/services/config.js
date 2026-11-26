// Dev: Vite proxies /api to the backend (see vite.config.js). Set VITE_API_URL to call it directly.
export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';
