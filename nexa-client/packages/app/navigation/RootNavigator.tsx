'use client'

import { useState, useEffect } from 'react'
import { LoginScreen } from '../features/user/LoginScreen'
import { RoleNavigator, UserRole } from '../features/roles/RoleNavigator'
import { RoleSelector } from '../features/roles/RoleSelector'
import { getMyRole } from '../api/role'

export function RootNavigator() {
  const [isLoggedIn, setIsLoggedIn] = useState(false)
  const [selectedRole, setSelectedRole] = useState<UserRole | null>(null)
  const [loadingRole, setLoadingRole] = useState(false)
  const [mfaToken, setMfaToken] = useState<string | null>(null)

  const handleLoginSuccess = async () => {
    setIsLoggedIn(true)
    setLoadingRole(true)
    
    try {
      const roleData = await getMyRole()
      setSelectedRole(roleData.role)
    } catch {
      // Fallback to selector if role fetch fails
      setSelectedRole(null)
    } finally {
      setLoadingRole(false)
    }
  }

  const handleRoleSelected = (role: UserRole) => {
    setSelectedRole(role)
  }

  if (!isLoggedIn) {
    return <LoginScreen onLoginSuccess={handleLoginSuccess} />
  }

  if (loadingRole) {
    return <div style={{ padding: 40, textAlign: 'center' }}>Loading role...</div>
  }

  if (!selectedRole) {
    return <RoleSelector onRoleSelected={handleRoleSelected} />
  }

  return <RoleNavigator role={selectedRole} mfaToken={mfaToken} />
}
