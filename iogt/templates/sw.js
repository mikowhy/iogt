importScripts('https://storage.googleapis.com/workbox-cdn/releases/6.1.5/workbox-sw.js');
const { precacheAndRoute }  = workbox.precaching;


const appShell = [
  "{{ test_url }}",
].map((partialUrl) => `${location.protocol}//${location.host}${partialUrl}`);

// self.addEventListener('install', async (event) => {
//   console.log('Installed!');
//   self.skipWaiting();
// });

// self.addEventListener('activate', event => {
//   console.log('V1 now ready to handle fetches!');
  
// });


self.addEventListener('install', event => {
  event.waitUntil(
    console.log('installed')
  );
});

self.addEventListener('activate', event => {
  console.log('V1 now ready to handle fetches!');
});


self.addEventListener('message', async (event) => {
  precacheAndRoute(appShell.map(url => ({
    url,
    revision: null,
  })));
});
