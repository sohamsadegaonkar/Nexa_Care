import { afterEach, vi } from 'vitest'
import React from 'react'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Vitest globals are disabled, so Testing Library cannot auto-register cleanup.
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

vi.mock('next/script', () => ({
  default: (props: any) => React.createElement('script', props),
}))

vi.mock('next/navigation', () => ({
  useServerInsertedHTML: () => {},
}))

vi.mock('react-native-svg/src/lib/SvgTouchableMixin', () => ({
  default: {},
}))

vi.mock('@tamagui/lucide-icons', () => {
  const MockIcon = (props: any) => React.createElement('span', { ...props, 'aria-hidden': 'true' })

  return {
    __esModule: true,
    AlertTriangle: MockIcon,
    Check: MockIcon,
    CheckCircle: MockIcon,
    CheckCircle2: MockIcon,
    ChevronDown: MockIcon,
    ChevronLeft: MockIcon,
    ChevronUp: MockIcon,
    Clock: MockIcon,
    Fingerprint: MockIcon,
    Lock: MockIcon,
    RadioReceiver: MockIcon,
    ShieldOff: MockIcon,
    ShieldAlert: MockIcon,
    TrendingUp: MockIcon,
    UserCheck: MockIcon,
    Users: MockIcon,
    XCircle: MockIcon,
  }
})

vi.mock('react-native-svg', () => {
  const Mock = (props: any) => React.createElement('svg', props)
  return {
    __esModule: true,
    default: Mock,
    Svg: Mock,
    Path: Mock,
    Rect: Mock,
    Circle: Mock,
    G: Mock,
    Defs: Mock,
    LinearGradient: Mock,
    Stop: Mock,
  }
})

if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}
