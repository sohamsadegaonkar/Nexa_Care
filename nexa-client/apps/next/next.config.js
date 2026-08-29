function isLocalAddress(hostname) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '')
  return (
    host === 'localhost' ||
    host === '::1' ||
    host === '0.0.0.0' ||
    host.startsWith('127.') ||
    host.startsWith('10.') ||
    host.startsWith('192.168.') ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(host)
  )
}

function resolveApiProxyTarget(rawValue) {
  const raw = rawValue?.trim()
  if (!raw) return null

  let parsed
  try {
    parsed = new URL(raw)
  } catch {
    throw new Error('API_PROXY_TARGET must be a valid absolute URL.')
  }
  if (
    (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== '/'
  ) {
    throw new Error('API_PROXY_TARGET must be an origin-only HTTP(S) URL without credentials.')
  }
  if (
    process.env.NODE_ENV === 'production' &&
    (parsed.protocol !== 'https:' || isLocalAddress(parsed.hostname))
  ) {
    throw new Error('Production API_PROXY_TARGET must use HTTPS and a non-local host.')
  }
  return parsed.origin
}

/** @type {import('next').NextConfig} */
module.exports = {
  async headers() {
    const values = [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
      { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
      { key: 'X-Frame-Options', value: 'DENY' },
    ]
    if (process.env.NODE_ENV === 'production') {
      values.push({
        key: 'Strict-Transport-Security',
        value: 'max-age=31536000; includeSubDomains',
      })
    }
    return [{ source: '/(.*)', headers: values }]
  },
  async rewrites() {
    const target = resolveApiProxyTarget(process.env.API_PROXY_TARGET)

    if (!target) {
      return []
    }

    return [
      {
        source: '/api/:path*',
        destination: `${target}/api/:path*`,
      },
    ]
  },
  transpilePackages: [
    'solito',
    'react-native-web',
    '@tamagui/react-native-svg',
    '@tamagui/next-theme',
    '@tamagui/lucide-icons',
    'expo-linking',
    'expo-constants',
    'expo-modules-core',
    '@noble/curves',
    '@noble/hashes',
  ],
  experimental: {
    scrollRestoration: true,
  },
  turbopack: {
    resolveAlias: {
      'react-native': 'react-native-web',
      'react-native-svg': '@tamagui/react-native-svg',
      'react-native-safe-area-context': './shims/react-native-safe-area-context.js',
    },
    resolveExtensions: [
      '.web.tsx',
      '.web.ts',
      '.web.js',
      '.web.jsx',
      '.tsx',
      '.ts',
      '.js',
      '.jsx',
      '.json',
    ],
  },
}
