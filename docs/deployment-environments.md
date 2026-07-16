# Deployment environments

Nexa Care separates local development, alpha/demo, preview, and production.
Demo seeders are test-data tools, not patient or provider onboarding, and must
never receive real patient information.

| Environment | API transport | Firebase source | Demo tooling |
|---|---|---|---|
| development | HTTP only with `EXPO_PUBLIC_ALLOW_HTTP=true` | ignored local file | allowed with explicit `ENV=development` |
| alpha | HTTPS preferred; LAN only in a development build | alpha EAS file secret | allowed with explicit `ENV=alpha` |
| preview | HTTPS required | preview EAS file secret | refused |
| production | HTTPS and non-private host required | independent production EAS file secret | refused |

Expo requires `EXPO_PUBLIC_API_URL`, `EXPO_PUBLIC_APP_ENV`,
`EXPO_PUBLIC_EAS_PROJECT_ID`, and `GOOGLE_SERVICES_FILE`. The Google Services
JSON is a Firebase Android client configuration, never an Admin service-account
file. It is ignored locally and delivered to EAS as a file secret. Production
configuration is rejected when its Firebase project ID contains `alpha`.

The Android package remains `ai.nexacare.patient`. Push notifications remain a
core feature; removing a local Firebase file does not remove notification code.

Provider demo prefill is disabled by default. `NEXT_PUBLIC_DEMO_MODE=true` may
prefill only the demo identifier and visibly labels the UI. Passwords are
always entered at runtime from an ignored environment or secret manager.

The alpha app generates P-256 material in JavaScript and protects the private
key with SecureStore. This is not equivalent to a hardware-backed
StrongBox/Secure Enclave key. Synthetic workstation keys are not physical
enrollment evidence.
