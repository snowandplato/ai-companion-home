// Service Worker - M-Home
// 只做安装，不缓存，保证每次都拿最新内容
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', () => self.clients.claim())
