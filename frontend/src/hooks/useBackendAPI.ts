import { useMemo } from 'react'
import { useBackendStore } from '../stores/useBackendStore'
import { createApiClient } from '../services/apiClient'
import { createProcessingAPI } from '../services/api/processing'
import { createCatalogAPI } from '../services/api/catalog'
import { createTaskAPI } from '../services/api/tasks'

/** React only selects the backend and keeps domain API functions stable. */
export function useBackendAPI() {
  const backendURL = useBackendStore((state) => state.backendURL)
  return useMemo(() => {
    const client = createApiClient(backendURL)
    return { ...createProcessingAPI(client), ...createCatalogAPI(client), ...createTaskAPI(client) }
  }, [backendURL])
}
