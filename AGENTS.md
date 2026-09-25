# Deployment instructions

- The primary and only production application is **Amvera Miami** (`mia0`):
  `https://cloud.amvera.ru/projects/applications/mia0/~/lesyaabrikosova/allokator`.
- Deploy, build, start, stop, inspect logs, and troubleshoot production only in `mia0` unless the user explicitly changes the production region.
- Do not deploy to or start an Amvera Moscow (`msk0`) copy. If a Moscow copy still exists, treat it as obsolete and keep it stopped.
- Before any production action, verify that the Amvera URL contains `/mia0/`.
- Never run two instances of the Telegram bot with the same token: this causes `TelegramConflictError` and can switch message handling away from Miami.
