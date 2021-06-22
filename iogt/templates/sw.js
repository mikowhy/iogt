importScripts('https://storage.googleapis.com/workbox-cdn/releases/6.1.5/workbox-sw.js');
const {precacheAndRoute}  = workbox.precaching;

// The below implementation will basically convert "{{ test_url }}" which is test/ into http://localhost:8000/test
// The relative paths such as test/ of the pages inside the app will be provided by the backend devs, 
// so this will be done programmatically

const appShell = [
  "{{ test_url }}",
].map((partialUrl) => `${location.protocol}//${location.host}${partialUrl}`);


addEventListener('message', async (event) => {
  if (event.data.type === 'CACHE') {
   
    precacheAndRoute(appShell.map(url => ({
      url,
      revision: null,
    })));

   console.log(appShell)
  }
});

