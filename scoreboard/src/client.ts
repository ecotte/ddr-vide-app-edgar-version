import { RayfinClient, resolveRayfinConfig } from '@microsoft/rayfin-client';
import {
  ensureSignedInWithFabric, initEmbeddedAuth, signInWithBrokeredToken,
} from '@microsoft/rayfin-auth-provider-fabric';
import type { AppSchema } from '../rayfin/data/schema';
import type { AppFunctionsSchema } from '../rayfin/functions/src/types';

export async function createClient() {
  const resolved = await resolveRayfinConfig({
    apiUrl: import.meta.env.VITE_RAYFIN_API_URL,
    publishableKey: import.meta.env.VITE_RAYFIN_PUBLISHABLE_KEY,
    workspaceId: import.meta.env.VITE_FABRIC_WORKSPACE_ID,
    itemId: import.meta.env.VITE_FABRIC_ITEM_ID,
    portalUrl: import.meta.env.VITE_FABRIC_PORTAL_URL,
    tenantId: import.meta.env.VITE_FABRIC_TENANT_ID,
  });
  if (!resolved.baseUrl || !resolved.publishableKey) {
    throw new Error('Rayfin configuration is missing. Deploy DDR Scoreboard first.');
  }
  return new RayfinClient<AppSchema, AppFunctionsSchema>({
    baseUrl: resolved.baseUrl,
    publishableKey: resolved.publishableKey,
    runtimeConfig: resolved.runtimeConfig,
    authStorage: true,
  });
}

export type Client = Awaited<ReturnType<typeof createClient>>;

function authOptions(client: Client) {
  const config = client.runtimeConfig;
  if (!config?.workspaceId || !config.itemId || !config.portalUrl) {
    throw new Error('Fabric sign-in configuration is missing.');
  }
  const portal = new URL(config.portalUrl);
  if (config.tenantId) portal.searchParams.set('ctid', config.tenantId);
  return {
    workspaceId: config.workspaceId, projectId: config.itemId,
    fabricPortalUrl: portal.toString(), returnOrigin: window.location.origin,
  };
}

export function signIn(client: Client) {
  return ensureSignedInWithFabric(client.auth, authOptions(client));
}

export async function initializeOperator(client: Client) {
  await initEmbeddedAuth(client.auth, authOptions(client));
  if (import.meta.env.DEV && !client.auth.getSession().isAuthenticated) {
    const { fetchRayfinLocalSessionToken } = await import('@microsoft/rayfin-local-dev');
    const token = await fetchRayfinLocalSessionToken();
    if (token) await signInWithBrokeredToken(client.auth, token);
  }
  return client.auth.getSession().isAuthenticated;
}
