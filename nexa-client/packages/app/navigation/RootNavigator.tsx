'use client'

import { useCallback, useEffect, useState } from 'react'
import { LoginScreen } from '../features/user/LoginScreen'
import { RoleNavigator, UserRole } from '../features/roles/RoleNavigator'
import { getMyRole } from '../api/role'
import { setAuthTokenProvider } from '../utils/apiClient'

export function RootNavigator() {
  const [isLoggedIn, setIsLoggedIn] = useState(false)
  const [selectedRole, setSelectedRole] = useState<UserRole | null>(null)
  const [loadingRole, setLoadingRole] = useState(false)
  const [roleError, setRoleError] = useState(false)

  const clearAuthenticatedState = useCallback(() => {
    setAuthTokenProvider(() => null)
    setSelectedRole(null)
    setRoleError(false)
    setIsLoggedIn(false)
  }, [])

  const refreshRole = useCallback(async () => {
    setLoadingRole(true)
    setRoleError(false)
    try {
      const roleData = await getMyRole()
      setSelectedRole(roleData.role)
    } catch (error: unknown) {
      setSelectedRole(null)
      setRoleError(true)
      console.warn('ROLE_RESOLUTION_FAILED', {
        errorClass: error instanceof Error ? error.name : 'UnknownError',
      })
    } finally {
      setLoadingRole(false)
    }
  }, [])

  const handleLoginSuccess = async (_providerId: string, accessToken: string) => {
    setAuthTokenProvider(() => accessToken)
    setIsLoggedIn(true)
    await refreshRole()
  }

  useEffect(() => {
    if (!isLoggedIn) return
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void refreshRole()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [isLoggedIn, refreshRole])

  if (!isLoggedIn) return <LoginScreen onLoginSuccess={handleLoginSuccess} />
  if (loadingRole) return <div style={{ padding: 40, textAlign: 'center' }}>Verifying access…</div>
  if (roleError || !selectedRole) {
    return (
      <div style={{ padding: 40, textAlign: 'center' }}>
        <p>We could not verify your current access. No role has been selected.</p>
        <button onClick={() => void refreshRole()}>Retry</button>
        <button onClick={clearAuthenticatedState}>Sign in again</button>
      </div>
    )
  }
  return (
    <RoleNavigator
      key={selectedRole}
      role={selectedRole}
      onLogout={clearAuthenticatedState}
      onRoleRefresh={refreshRole}
    />
  )
}
