import { registerSW } from "virtual:pwa-register";
import { createApp } from "vue";

import App from "./App.vue";
import "./index.css";

// Installable PWA: register the service worker immediately; autoUpdate mode
// activates new deploys on the next launch without prompting.
registerSW({ immediate: true });

createApp(App).mount("#app");
