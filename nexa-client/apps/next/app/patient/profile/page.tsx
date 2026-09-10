'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ActionButton,
  FormField,
  InlineNotice,
  LoadingState,
  Paragraph,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import {
  ArrowLeft,
  Calendar,
  CheckCircle,
  Key,
  Lock,
  LogOut,
  QrCode,
  ShieldAlert,
  ShieldCheck,
  User,
} from '@tamagui/lucide-icons'
import {
  clearPatientAuthSession,
  getCurrentPatientId,
  usePatientAuthSession,
} from 'app/services/patientAuthSession'
import { apiClient } from 'app/utils/apiClient'

interface ProfileData {
  full_name: string
  date_of_birth: string
  public_patient_id: string
}

interface OnboardingStatus {
  profile_complete: boolean
  terms_current: boolean
  privacy_current: boolean
  complete: boolean
}

export default function PatientProfilePage() {
  const router = useRouter()
  const session = usePatientAuthSession()
  const patientId = getCurrentPatientId()

  const [profile, setProfile] = useState<ProfileData | null>(null)
  const [onboarding, setOnboarding] = useState<OnboardingStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editFullName, setEditFullName] = useState('')
  const [editDob, setEditDob] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  const loadProfileData = useCallback(async () => {
    if (session.status !== 'authenticated') return
    setLoading(true)
    setError(null)
    try {
      const profRes = await apiClient.get<any>('/api/v2/patient/me/profile')
      const p = (profRes as any)?.data || profRes
      if (p?.public_patient_id) {
        setProfile(p)
        setEditFullName(p.full_name || '')
        setEditDob(p.date_of_birth || '')
      }
    } catch {
      // Profile may not be created yet; user can enter it
      setIsEditing(true)
    }

    try {
      const onbRes = await apiClient.get<any>('/api/v2/patient/me/onboarding-status')
      const o = (onbRes as any)?.data || onbRes
      if (o) setOnboarding(o)
    } catch {
      // Graceful fallback
    } finally {
      setLoading(false)
    }
  }, [session.status])

  useEffect(() => {
    loadProfileData()
  }, [loadProfileData])

  const handleSaveProfile = async () => {
    if (!editFullName.trim()) {
      setError('Full name is required.')
      return
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(editDob.trim())) {
      setError('Date of birth must be in YYYY-MM-DD format.')
      return
    }

    setSaving(true)
    setError(null)
    setSuccessMessage(null)

    try {
      const response = await apiClient.put<any>('/api/v2/patient/me/profile', {
        full_name: editFullName.trim(),
        date_of_birth: editDob.trim(),
      })
      const saved = (response as any)?.data || response
      setProfile(saved)
      setIsEditing(false)
      setSuccessMessage('Profile successfully saved.')
      const onbRes = await apiClient.get<any>('/api/v2/patient/me/onboarding-status')
      const o = (onbRes as any)?.data || onbRes
      if (o) setOnboarding(o)
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : 'Unable to update profile. Please check the details and try again.'
      )
    } finally {
      setSaving(false)
    }
  }

  const handleSignOut = async () => {
    try {
      await clearPatientAuthSession('logout')
    } catch {
      // fallback
    }
    router.push('/patient/login')
  }

  return (
    <YStack gap="$5" maxWidth={1000} width="100%" marginHorizontal="auto">
      {/* Header */}
      <XStack
        flexWrap="wrap"
        justifyContent="space-between"
        alignItems="center"
        gap="$3"
      >
        <YStack gap="$1.5">
          <XStack alignItems="center" gap="$2">
            <ActionButton
              chromeless
              paddingHorizontal="$2"
              onPress={() => router.push('/patient/dashboard')}
            >
              <XStack alignItems="center" gap="$1.5">
                <ArrowLeft size={16} color="$nexaSecondary" />
                <Text color="$nexaSecondary" fontSize={13} fontWeight="600">
                  Dashboard
                </Text>
              </XStack>
            </ActionButton>
          </XStack>
          <Text fontSize={26} fontWeight="900" color="$nexaText">
            Patient Profile & Identity
          </Text>
          <Paragraph color="$nexaSecondary" fontSize={14}>
            Encrypted profile attributes and cryptographic access controls managed server-side.
          </Paragraph>
        </YStack>

        <ActionButton onPress={handleSignOut}>
          <XStack alignItems="center" gap="$1.5">
            <LogOut size={15} color="$nexaText" />
            <Text color="$nexaText" fontSize={13} fontWeight="600">Sign Out</Text>
          </XStack>
        </ActionButton>
      </XStack>

      {/* Notices */}
      {error && <InlineNotice title={error} tone="danger" />}
      {successMessage && <InlineNotice title={successMessage} tone="success" />}

      {/* Profile Card */}
      <Surface padding="$5" borderRadius={16} elevation="$1">
        {loading ? (
          <LoadingState label="Loading patient profile..." />
        ) : (
          <YStack gap="$4">
            <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$3">
              <XStack gap="$3" alignItems="center">
                <Surface
                  padding="$3"
                  borderRadius={12}
                  backgroundColor="$nexaAccentSoft"
                >
                  <User size={28} color="$nexaAccent" />
                </Surface>
                <YStack gap="$0.5">
                  <Text fontSize={20} fontWeight="900" color="$nexaText">
                    {profile?.full_name || 'Unregistered Profile'}
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={13}>
                    {profile?.date_of_birth ? `Date of Birth: ${profile.date_of_birth}` : 'Date of birth not recorded'}
                  </Paragraph>
                </YStack>
              </XStack>

              <Surface
                paddingHorizontal="$3"
                paddingVertical="$2"
                borderRadius={8}
                backgroundColor="$nexaMuted"
              >
                <XStack alignItems="center" gap="$2">
                  <QrCode size={16} color="$nexaAccent" />
                  <Text fontSize={13} fontWeight="700" color="$nexaText">
                    {profile?.public_patient_id || (patientId ? `ID: ${patientId.slice(0, 12)}...` : 'ID Unavailable')}
                  </Text>
                </XStack>
              </Surface>
            </XStack>

            {/* Profile Status & Details Row */}
            <XStack flexWrap="wrap" gap="$3">
              <Surface
                flex={1}
                minWidth={180}
                padding="$3.5"
                borderRadius={10}
                backgroundColor="$nexaSurface"
                borderWidth={1}
                borderColor="$nexaBorder"
              >
                <Text fontSize={11} fontWeight="700" color="$nexaSecondary" textTransform="uppercase">
                  Profile Status
                </Text>
                <Text fontSize={15} fontWeight="800" color={profile ? '$nexaSuccess' : '$nexaWarning'}>
                  {profile ? 'Encrypted & Active' : 'Pending Completion'}
                </Text>
              </Surface>

              <Surface
                flex={1}
                minWidth={180}
                padding="$3.5"
                borderRadius={10}
                backgroundColor="$nexaSurface"
                borderWidth={1}
                borderColor="$nexaBorder"
              >
                <Text fontSize={11} fontWeight="700" color="$nexaSecondary" textTransform="uppercase">
                  Terms & Privacy
                </Text>
                <Text fontSize={15} fontWeight="800" color={onboarding?.complete ? '$nexaSuccess' : '$nexaText'}>
                  {onboarding?.complete ? 'Accepted' : 'Review Pending'}
                </Text>
              </Surface>

              <Surface
                flex={1}
                minWidth={180}
                padding="$3.5"
                borderRadius={10}
                backgroundColor="$nexaSurface"
                borderWidth={1}
                borderColor="$nexaBorder"
              >
                <Text fontSize={11} fontWeight="700" color="$nexaSecondary" textTransform="uppercase">
                  Public Discovery Handle
                </Text>
                <Text fontSize={15} fontWeight="800" color="$nexaAccent">
                  {profile?.public_patient_id || 'Unavailable'}
                </Text>
              </Surface>
            </XStack>

            {/* Edit / View Form */}
            {isEditing ? (
              <YStack
                gap="$3"
                padding="$4"
                borderRadius={12}
                borderWidth={1}
                borderColor="$nexaBorder"
                backgroundColor="$nexaSurface"
              >
                <Text fontSize={16} fontWeight="800" color="$nexaText">
                  {profile ? 'Edit Profile Information' : 'Complete Your Profile'}
                </Text>
                <FormField
                  id="patient-full-name"
                  label="Full Legal Name"
                  placeholder="Your legal name"
                  value={editFullName}
                  onChangeText={(text) => {
                    setEditFullName(text)
                    if (error) setError(null)
                  }}
                  disabled={saving}
                />
                <FormField
                  id="patient-dob"
                  label="Date of Birth (YYYY-MM-DD)"
                  placeholder="1985-04-12"
                  value={editDob}
                  onChangeText={(text) => {
                    setEditDob(text)
                    if (error) setError(null)
                  }}
                  disabled={saving}
                />
                <XStack justifyContent="flex-end" gap="$2" marginTop="$2">
                  {profile && (
                    <ActionButton
                      chromeless
                      onPress={() => {
                        setIsEditing(false)
                        setEditFullName(profile.full_name || '')
                        setEditDob(profile.date_of_birth || '')
                        setError(null)
                      }}
                      disabled={saving}
                    >
                      Cancel
                    </ActionButton>
                  )}
                  <ActionButton
                    intent="primary"
                    onPress={handleSaveProfile}
                    disabled={saving || !editFullName.trim() || !editDob.trim()}
                    aria-busy={saving}
                  >
                    {saving ? 'Saving…' : 'Save Encrypted Profile'}
                  </ActionButton>
                </XStack>
              </YStack>
            ) : (
              <XStack justifyContent="flex-end">
                <ActionButton
                  onPress={() => setIsEditing(true)}
                >
                  Edit Profile
                </ActionButton>
              </XStack>
            )}
          </YStack>
        )}
      </Surface>

      {/* Privacy & Security Guarantees */}
      <Surface padding="$5" borderRadius={16} elevation="$1">
        <YStack gap="$4">
          <YStack gap="$1">
            <Text fontSize={18} fontWeight="800" color="$nexaText">
              Security & Consent Architecture
            </Text>
            <Paragraph color="$nexaSecondary" fontSize={13}>
              Architectural safety guarantees enforced by the Nexa Care Clinical Core
            </Paragraph>
          </YStack>

          <YStack gap="$3">
            <Surface
              padding="$3.5"
              borderRadius={10}
              backgroundColor="$nexaSurface"
              borderWidth={1}
              borderColor="$nexaBorder"
            >
              <XStack gap="$3" alignItems="flex-start">
                <Lock size={18} color="$nexaAccent" />
                <YStack gap="$1" flex={1}>
                  <Text fontSize={14} fontWeight="700" color="$nexaText">
                    SEC-001: Ephemeral Memory Capabilities
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Healthcare providers receive time-bounded scoped tokens held only in ephemeral process memory. Tokens are never stored in URLs or browser storage.
                  </Paragraph>
                </YStack>
              </XStack>
            </Surface>

            <Surface
              padding="$3.5"
              borderRadius={10}
              backgroundColor="$nexaSurface"
              borderWidth={1}
              borderColor="$nexaBorder"
            >
              <XStack gap="$3" alignItems="flex-start">
                <ShieldAlert size={18} color="$nexaAccent" />
                <YStack gap="$1" flex={1}>
                  <Text fontSize={14} fontWeight="700" color="$nexaText">
                    SEC-005: Break-Glass Separation & Mandatory Audit
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Emergency clinical overrides cannot access general longitudinal records. They receive strictly minimum-necessary categories (such as critical allergies and medications) and emit an immutable audit alert.
                  </Paragraph>
                </YStack>
              </XStack>
            </Surface>

            <Surface
              padding="$3.5"
              borderRadius={10}
              backgroundColor="$nexaSurface"
              borderWidth={1}
              borderColor="$nexaBorder"
            >
              <XStack gap="$3" alignItems="flex-start">
                <Key size={18} color="$nexaAccent" />
                <YStack gap="$1" flex={1}>
                  <Text fontSize={14} fontWeight="700" color="$nexaText">
                    SEC-022: Sovereign Consent Revocation
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    You can revoke consent at any moment. The Nexa Care backend revokes the cryptographic capability immediately, causing subsequent provider access checks to fail closed.
                  </Paragraph>
                </YStack>
              </XStack>
            </Surface>
          </YStack>
        </YStack>
      </Surface>
    </YStack>
  )
}
