# Deployment environments

Nexa Care separates local development, alpha/demo, preview, and production.
Demo seeders are test-data tools, not patient or provider onboarding, and must
never receive real patient information.

| Environment | API transport | Firebase source | Demo tooling |
|---|---|---|---|
| development | HTTP only with `EXPO_PUBLIC_ALLOW_HTTP=true` | ignored local file | allowed with explicit `ENV=development` |
| alpha | HTTPS preferred; LAN only in a development build | alpha EAS file secret | refused; use separate governed alpha tooling |
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

The disposable localhost startup script may set
`NEXA_DEMO_ALLOW_INSECURE_LOOPBACK_WEB_COOKIES=true` only with
`ENVIRONMENT=development`. It permits non-`Secure`, first-party `SameSite=Lax`
provider web cookies solely when both the HTTP host and client source are
loopback; it is rejected everywhere else. Do not copy this flag to alpha,
preview, pilot, staging, or production configuration.

`NEXA_DEMO_PATIENT_LOGIN_ENABLED=true` is a separate, disposable-development
control for the two explicitly seeded synthetic patient identities. The API
requires both that flag and `ENVIRONMENT=development`, accepts no patient ID or
 arbitrary identity input, and permits only loopback or the standard Android
 emulator gateway (`10.0.2.2`) over HTTP by default. A physical phone requires
 both an explicit private API host and an explicit private client CIDR through
 the development launcher; malformed, partial, public, or nonmatching LAN
 configuration is denied. It still creates the normal Redis-backed patient
 session and requires ordinary device enrollment; none of these controls may
 be copied to alpha, preview, pilot, staging, or production configuration.

The alpha app generates P-256 material in JavaScript and protects the private
key with SecureStore. This is not equivalent to a hardware-backed
StrongBox/Secure Enclave key. Synthetic workstation keys are not physical
enrollment evidence.
