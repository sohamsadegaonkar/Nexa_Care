// Web shim for expo-secure-store in Next.js browser & SSR runtimes
// SecureStore is a native iOS/Android keychain module that is unavailable on web.

const memoryStore = new Map()

export const WHEN_UNLOCKED_THIS_DEVICE_ONLY = 0
export const AFTER_FIRST_UNLOCK = 1
export const ALWAYS = 2
export const WHEN_PASSCODE_SET_THIS_DEVICE_ONLY = 3

export async function getItemAsync(key, options) {
  if (typeof window === 'undefined') {
    return memoryStore.get(key) ?? null
  }
  try {
    return window.sessionStorage.getItem(key) ?? memoryStore.get(key) ?? null
  } catch {
    return memoryStore.get(key) ?? null
  }
}

export async function setItemAsync(key, value, options) {
  if (typeof window === 'undefined') {
    memoryStore.set(key, value)
    return
  }
  try {
    window.sessionStorage.setItem(key, value)
  } catch {
    // fallback
  }
  memoryStore.set(key, value)
}

export async function deleteItemAsync(key, options) {
  if (typeof window !== 'undefined') {
    try {
      window.sessionStorage.removeItem(key)
    } catch {
      // fallback
    }
  }
  memoryStore.delete(key)
}

export async function isAvailableAsync() {
  return true
}

export default {
  getItemAsync,
  setItemAsync,
  deleteItemAsync,
  isAvailableAsync,
  WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  AFTER_FIRST_UNLOCK,
  ALWAYS,
  WHEN_PASSCODE_SET_THIS_DEVICE_ONLY,
}
