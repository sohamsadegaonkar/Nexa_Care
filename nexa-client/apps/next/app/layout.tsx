import type { Metadata } from 'next'
import './product.css'
import { NextTamaguiProvider } from 'app/provider/NextTamaguiProvider'
import { ProviderAuthProvider } from 'app/features/doctor/ProviderAuthContext'

export const metadata: Metadata = {
  title: { default: 'Nexa Care | Connected care', template: '%s | Nexa Care' },
  description:
    'Consent-first health records and clinical workflows. Connect care with clear patient control and source transparency.',
  icons: '/favicon.ico',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // You can use `suppressHydrationWarning` to avoid the warning about mismatched content during hydration in dev mode
    <html
      lang="en"
      suppressHydrationWarning
    >
      <body>
        <NextTamaguiProvider>
          <ProviderAuthProvider>{children}</ProviderAuthProvider>
        </NextTamaguiProvider>
      </body>
    </html>
  )
}
