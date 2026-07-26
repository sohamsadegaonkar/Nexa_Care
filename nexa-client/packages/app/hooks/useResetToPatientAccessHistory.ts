import { useCallback } from 'react'
import type { NavigationProp, ParamListBase } from '@react-navigation/native'
import { useNavigation } from 'expo-router'

export function useResetToPatientAccessHistory() {
  const rootNavigation = useNavigation('/') as NavigationProp<ParamListBase>

  return useCallback(() => {
    rootNavigation.reset({
      index: 1,
      routes: [
        {
          name: 'index',
        },
        {
          name: 'patient',
          state: {
            index: 0,
            routes: [
              {
                name: 'access-history',
              },
            ],
          },
        },
      ],
    })
  }, [rootNavigation])
}
