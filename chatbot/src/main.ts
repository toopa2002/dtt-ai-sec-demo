import { bootstrapApplication } from '@angular/platform-browser';
import { buildAppConfig, RuntimeConfig } from './app/app.config';
import { App } from './app/app';

// Entra IDs come from public/config.json, generated from .env by scripts/chatbot.sh.
fetch('config.json') // relative to <base href>, like everything the app loads
  .then((res) => res.json() as Promise<RuntimeConfig>)
  .then((config) => bootstrapApplication(App, buildAppConfig(config)))
  .catch((err) => console.error(err));
