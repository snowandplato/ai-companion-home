// Local development default; set VITE_API_BASE_URL for a deployed gateway.
export const DEFAULT_API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')
